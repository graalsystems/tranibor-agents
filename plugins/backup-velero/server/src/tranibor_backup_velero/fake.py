# SPDX-License-Identifier: Apache-2.0
"""A seeded in-memory cluster and bucket, served as an `httpx` transport (`VELERO_FAKE=1`).

It answers the few Kubernetes paths and the one S3 call the server makes, with the shapes of the real APIs, so
the client code runs unchanged. Everything is relative to the clock it is given, so the scene stays coherent:

- `daily-apps`  : fresh, completed backups of `shop` and `blog`; a restore of it already succeeded.
- `weekly-archive`: weekly, fresh, completed.
- `hourly-db`   : its newest backup FAILED and the last good one is old (stale), a Red case.
- `offsite`     : a storage location that is Unavailable.

A Restore created through the fake starts without a status and reads back `Completed` afterwards, with as many
items as the source namespace held, which is what a healthy restore looks like. Names and hosts are invented
(example.com); nothing here is a real cluster's data.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote
from xml.sax.saxutils import escape

import httpx

FAKE_BUCKET = "velero-backups"
FAKE_S3_ENDPOINT = "https://s3.fake.example.com"
NAMESPACE_ITEMS = {"shop": 310, "blog": 170, "cache": 40}


def _ts(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status(code: int, reason: str, message: str) -> httpx.Response:
    body = {
        "kind": "Status",
        "apiVersion": "v1",
        "status": "Failure",
        "message": message,
        "reason": reason,
        "code": code,
    }
    return httpx.Response(code, json=body)


class FakeCluster:
    def __init__(self, now: datetime | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self.now = (now or (clock() if clock else datetime.now(UTC))).astimezone(UTC)
        self.velero_namespace = "velero"
        self.requests: list[tuple[str, str]] = []
        self.restores: dict[str, dict[str, Any]] = {}
        self.backups: dict[str, dict[str, Any]] = {}
        self.schedules: dict[str, dict[str, Any]] = {}
        self.locations: list[dict[str, Any]] = []
        self.namespaces: dict[str, dict[str, Any]] = {}
        self.objects: list[tuple[str, int, datetime]] = []
        self._seed()

    # --- the scene --------------------------------------------------------------------------------------------

    def _backup(
        self, schedule: str, age_h: float, phase: str, namespaces: list[str], errors: int = 0, location: str = "default"
    ) -> str:
        start = self.now - timedelta(hours=age_h)
        name = f"{schedule}-{start:%Y%m%d%H%M%S}"
        items = sum(NAMESPACE_ITEMS.get(n, 25) for n in namespaces)
        self.backups[name] = {
            "apiVersion": "velero.io/v1",
            "kind": "Backup",
            "metadata": {
                "name": name,
                "namespace": self.velero_namespace,
                "creationTimestamp": _ts(start),
                "labels": {"velero.io/schedule-name": schedule, "velero.io/storage-location": location},
            },
            "spec": {"storageLocation": location, "includedNamespaces": namespaces, "ttl": "720h0m0s"},
            "status": {
                "phase": phase,
                "startTimestamp": _ts(start),
                "completionTimestamp": _ts(start + timedelta(minutes=6)),
                "expiration": _ts(start + timedelta(days=30)),
                "errors": errors,
                "warnings": 1 if phase == "Completed" else 0,
                "formatVersion": "1.1.0",
                "progress": {"totalItems": items, "itemsBackedUp": items if phase != "Failed" else items // 4},
                **({"failureReason": "timed out waiting for the volume snapshot"} if phase == "Failed" else {}),
            },
        }
        for rel, size in (("velero-backup.json", 2_100), (f"{name}.tar.gz", 8_400_000), (f"{name}-logs.gz", 31_000)):
            self.objects.append((f"backups/{name}/{rel}", size, start + timedelta(minutes=6)))
        return name

    def _schedule(self, name: str, cron: str, namespaces: list[str], paused: bool = False) -> None:
        self.schedules[name] = {
            "apiVersion": "velero.io/v1",
            "kind": "Schedule",
            "metadata": {"name": name, "namespace": self.velero_namespace},
            "spec": {
                "schedule": cron,
                "paused": paused,
                "template": {"storageLocation": "default", "includedNamespaces": namespaces, "ttl": "720h0m0s"},
            },
            "status": {"phase": "Enabled"},
        }

    def _seed(self) -> None:
        apps = ["shop", "blog"]
        self._schedule("daily-apps", "0 2 * * *", apps)
        self._schedule("weekly-archive", "0 3 * * 0", ["cache"])
        self._schedule("hourly-db", "0 * * * *", ["shop"])
        good = self._backup("daily-apps", 7.0, "Completed", apps)
        self._backup("daily-apps", 31.0, "Completed", apps)
        self._backup("weekly-archive", 60.0, "Completed", ["cache"])
        self._backup("hourly-db", 7.5, "Completed", ["shop"])
        self._backup("hourly-db", 5.0, "Failed", ["shop"], errors=3)
        for schedule, backup in (("daily-apps", good),):
            self.schedules[schedule]["status"]["lastBackup"] = self.backups[backup]["status"]["startTimestamp"]
        self.schedules["hourly-db"]["status"]["lastBackup"] = _ts(self.now - timedelta(hours=5))
        self.locations = [
            self._location("default", True, "Available", None),
            self._location("offsite", False, "Unavailable", "error listing objects: AccessDenied: the key was rotated"),
        ]
        self.restores["daily-apps-shop-rehearsal"] = self._restore_doc(
            "daily-apps-shop-rehearsal", good, "shop", "restore-test-shop-old", "Completed", 310, hours=30.0
        )
        for name in ("shop", "blog", "kube-system"):
            self.namespaces[name] = {"metadata": {"name": name, "labels": {}}}
        self.namespaces["restore-test-shop-old"] = {
            "metadata": {
                "name": "restore-test-shop-old",
                "labels": {
                    "app.kubernetes.io/managed-by": "tranibor-backup-velero",
                    "tranibor.io/test-restore": "true",
                },
            }
        }
        self.namespaces["restore-test-manual"] = {"metadata": {"name": "restore-test-manual", "labels": {}}}

    def _location(self, name: str, default: bool, phase: str, message: str | None) -> dict[str, Any]:
        status: dict[str, Any] = {
            "phase": phase,
            "lastValidationTime": _ts(self.now - timedelta(seconds=40)),
            "lastSyncedTime": _ts(self.now - timedelta(minutes=1)),
            "accessMode": "ReadWrite",
        }
        if message:
            status["message"] = message
        return {
            "apiVersion": "velero.io/v1",
            "kind": "BackupStorageLocation",
            "metadata": {"name": name, "namespace": self.velero_namespace},
            "spec": {
                "provider": "aws",
                "default": default,
                "objectStorage": {"bucket": FAKE_BUCKET, "prefix": "backups" if default else "offsite"},
                "validationFrequency": "1m0s",
            },
            "status": status,
        }

    def _restore_doc(
        self, name: str, backup: str, source: str, target: str, phase: str, items: int, hours: float = 0.0
    ) -> dict[str, Any]:
        start = self.now - timedelta(hours=hours)
        return {
            "apiVersion": "velero.io/v1",
            "kind": "Restore",
            "metadata": {
                "name": name,
                "namespace": self.velero_namespace,
                "creationTimestamp": _ts(start),
                "labels": {},
            },
            "spec": {
                "backupName": backup,
                "includedNamespaces": [source],
                "namespaceMapping": {source: target},
                "restorePVs": False,
            },
            "status": {
                "phase": phase,
                "startTimestamp": _ts(start),
                "completionTimestamp": _ts(start + timedelta(minutes=3)),
                "errors": 0,
                "warnings": 2,
                "progress": {"totalItems": items, "itemsRestored": items},
            },
        }

    # --- the transport ----------------------------------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        self.requests.append((request.method, path))
        if request.url.host.startswith("s3."):
            return self._s3(request, path)
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return _status(401, "Unauthorized", "Unauthorized")
        velero = re.fullmatch(rf"/apis/velero\.io/v1/namespaces/{self.velero_namespace}/([a-z]+)(?:/([^/]+))?", path)
        if velero:
            return self._velero(request, velero.group(1), velero.group(2))
        namespaces = re.fullmatch(r"/api/v1/namespaces(?:/([^/]+))?", path)
        if namespaces:
            return self._namespaces(request, namespaces.group(1))
        return _status(404, "NotFound", f"the server could not find the requested resource ({path})")

    @staticmethod
    def _list(items: list[dict[str, Any]], kind: str, selector: str | None = None) -> httpx.Response:
        if selector:
            key, _, value = selector.partition("=")
            items = [i for i in items if (i.get("metadata", {}).get("labels") or {}).get(key) == value]
        return httpx.Response(
            200, json={"apiVersion": "velero.io/v1", "kind": f"{kind}List", "metadata": {}, "items": items}
        )

    def _materialize(self) -> None:
        """A restore created through the fake reads back as Completed."""
        for doc in self.restores.values():
            if "status" not in doc:
                source = next(iter(doc["spec"]["namespaceMapping"]))
                items = NAMESPACE_ITEMS.get(source, 25)
                done = self._restore_doc(
                    doc["metadata"]["name"], doc["spec"]["backupName"], source, "", "Completed", items
                )
                doc["status"] = done["status"]

    def _velero(self, request: httpx.Request, plural: str, name: str | None) -> httpx.Response:
        self._materialize()
        tables: dict[str, tuple[dict[str, dict[str, Any]], str]] = {
            "backups": (self.backups, "Backup"),
            "schedules": (self.schedules, "Schedule"),
            "restores": (self.restores, "Restore"),
        }
        if request.method == "POST" and plural == "restores" and name is None:
            body = json.loads(request.content)
            new = body["metadata"]["name"]
            if new in self.restores:
                return _status(409, "AlreadyExists", f'restores.velero.io "{new}" already exists')
            body["metadata"]["creationTimestamp"] = _ts(self.now)
            self.restores[new] = body
            return httpx.Response(201, json=body)
        if request.method == "POST" and plural == "backups" and name is None:
            body = json.loads(request.content)
            new = body["metadata"]["name"]
            if new in self.backups:
                return _status(409, "AlreadyExists", f'backups.velero.io "{new}" already exists')
            body["metadata"]["creationTimestamp"] = _ts(self.now)
            body["status"] = {"phase": "InProgress", "startTimestamp": _ts(self.now)}
            self.backups[new] = body
            return httpx.Response(201, json=body)
        if request.method != "GET":
            return _status(405, "MethodNotAllowed", "the fake serves only reads, the restore and the backup create")
        selector = request.url.params.get("labelSelector")
        if plural == "backupstoragelocations":
            items, kind = self.locations, "BackupStorageLocation"
        elif plural in ("volumesnapshotlocations", "podvolumebackups"):
            return self._list([], plural)
        elif plural in tables:
            table, kind = tables[plural]
            items = list(table.values())
        else:
            return _status(404, "NotFound", f"unknown resource {plural}")
        if name is None:
            return self._list(items, kind, selector)
        found = next((i for i in items if i["metadata"]["name"] == name), None)
        if found is None:
            return _status(404, "NotFound", f'{plural}.velero.io "{name}" not found')
        return httpx.Response(200, json=found)

    def _namespaces(self, request: httpx.Request, name: str | None) -> httpx.Response:
        if request.method == "POST" and name is None:
            body = json.loads(request.content)
            new = body["metadata"]["name"]
            if new in self.namespaces:
                return _status(409, "AlreadyExists", f'namespaces "{new}" already exists')
            self.namespaces[new] = body
            return httpx.Response(201, json=body)
        if name is None:
            return _status(403, "Forbidden", "namespaces is forbidden: the service account may not list namespaces")
        found = self.namespaces.get(name)
        if found is None:
            return _status(404, "NotFound", f'namespaces "{name}" not found')
        if request.method == "DELETE":
            # A namespace without finalizers is gone moments after the delete: the fake removes it at once.
            found["metadata"]["deletionTimestamp"] = _ts(self.now)
            found["status"] = {"phase": "Terminating"}
            del self.namespaces[name]
            return httpx.Response(200, json=found)
        return httpx.Response(200, json=found)

    def _s3(self, request: httpx.Request, path: str) -> httpx.Response:
        if "AWS4-HMAC-SHA256" not in request.headers.get("authorization", ""):
            return httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>")
        if path != f"/{FAKE_BUCKET}":
            return httpx.Response(404, text="<Error><Code>NoSuchBucket</Code></Error>")
        prefix = request.url.params.get("prefix", "")
        rows = "".join(
            f"<Contents><Key>{escape(key)}</Key><LastModified>{_ts(when)}</LastModified><Size>{size}</Size></Contents>"
            for key, size, when in self.objects
            if key.startswith(prefix)
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"<Name>{FAKE_BUCKET}</Name><IsTruncated>false</IsTruncated>{rows}</ListBucketResult>"
        )
        return httpx.Response(200, text=xml)
