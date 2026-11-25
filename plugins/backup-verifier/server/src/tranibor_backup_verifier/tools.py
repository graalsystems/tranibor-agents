# SPDX-License-Identifier: Apache-2.0
"""The tools: reads of Velero's custom resources and the bucket, and two writes confined to the restore prefix.

What a write may touch, and nothing else:

- `create_test_restore` creates a namespace named `<prefix><label>-<yyyymmdd>` (the prefix is the connector's
  `restore_namespace_prefix`) and a Restore in the Velero namespace that maps ONE source namespace of a
  completed backup into it. It never writes to a Backup, a Schedule, a storage location, nor to the source
  namespace, and the Restore never includes cluster-scoped resources.
- `delete_test_restore_namespace` deletes a namespace only if its name starts with the prefix AND it carries
  the label this server put on it when it created it.

Kubernetes RBAC cannot restrict a verb to namespace names by prefix, so this module is the guard; the
procedure suggests an admission policy that enforces the same rule inside the cluster.

Fields read from Velero's CRDs (docs: https://velero.io/docs/main/api-types/): Backup `status.phase`,
`errors`, `warnings`, `expiration`, `startTimestamp`, `completionTimestamp`, `validationErrors`,
`failureReason`, `volumeSnapshots*`; Schedule `spec.schedule`, `spec.paused`, `status.lastBackup`,
`status.phase`; Restore `spec.namespaceMapping`, `status.phase`, `errors`, `warnings`. NOT on those pages and read
defensively (optional) from Velero's Go types as I know them: `status.progress.{totalItems,itemsBackedUp|itemsRestored}`
of Backup and Restore; BackupStorageLocation `status.{phase,lastSyncedTime,lastValidationTime,message}` and
`spec.default`; VolumeSnapshotLocation `status.phase`; the labels `velero.io/schedule-name`,
`velero.io/storage-location` (on backups) and `velero.io/backup-name` (on PodVolumeBackups).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any

from .config import Settings
from .cron import next_fire, period_hours
from .errors import Failure, invalid, refused
from .kube import Kube
from .s3 import MAX_PAGES, Bucket

SOURCE = "velero"
MANAGED_BY = "tranibor-backup-verifier"
LABEL_MANAGED = "app.kubernetes.io/managed-by"
LABEL_TEST = "tranibor.io/test-restore"
LABEL_KEY = "tranibor.io/action-key-hash"
MAX_LIST = 100
#: A backup in one of these phases holds a restorable set of objects.
RESTORABLE = ("Completed", "PartiallyFailed")
LABEL_VALUE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
RESOURCE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
#: A freshness allowance for a schedule's period: one period and a quarter, plus an hour for the run itself.
PERIOD_FACTOR, GRACE_HOURS, FALLBACK_HOURS = 1.25, 1.0, 26.0


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def hours_between(later: datetime, earlier: datetime | None) -> float | None:
    return round((later - earlier).total_seconds() / 3600, 2) if earlier else None


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _meta(obj: dict[str, Any]) -> dict[str, Any]:
    meta = obj.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _status(obj: dict[str, Any]) -> dict[str, Any]:
    status = obj.get("status")
    return status if isinstance(status, dict) else {}


def _spec(obj: dict[str, Any]) -> dict[str, Any]:
    spec = obj.get("spec")
    return spec if isinstance(spec, dict) else {}


def _strings(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def schedule_of(backup: dict[str, Any], schedules: set[str]) -> str | None:
    """The schedule that made a backup: its `velero.io/schedule-name` label, else its name (`<schedule>-<ts>`)."""
    meta = _meta(backup)
    labeled = (meta.get("labels") or {}).get("velero.io/schedule-name")
    if labeled:
        return str(labeled)
    name = str(meta.get("name", ""))
    for schedule in sorted(schedules, key=len, reverse=True):
        if re.fullmatch(re.escape(schedule) + r"-\d{8,14}", name):
            return schedule
    return None


def backup_summary(backup: dict[str, Any], now: datetime, schedules: set[str] | None = None) -> dict[str, Any]:
    meta, spec, status = _meta(backup), _spec(backup), _status(backup)
    progress = _obj(status.get("progress"))
    started = parse_time(status.get("startTimestamp")) or parse_time(meta.get("creationTimestamp"))
    return {
        "name": meta.get("name"),
        "schedule": schedule_of(backup, schedules or set()),
        "phase": status.get("phase") or "New",
        "errors": _count(status.get("errors")),
        "warnings": _count(status.get("warnings")),
        "start": iso(started),
        "completion": iso(parse_time(status.get("completionTimestamp"))),
        "age_hours": hours_between(now, started),
        "expiration": iso(parse_time(status.get("expiration"))),
        "items_backed_up": progress.get("itemsBackedUp"),
        "items_total": progress.get("totalItems"),
        "storage_location": spec.get("storageLocation"),
        "included_namespaces": _strings(spec.get("includedNamespaces")),
        "snapshot_volumes": spec.get("snapshotVolumes"),
        "ttl": spec.get("ttl"),
    }


def _started(summary: dict[str, Any]) -> datetime:
    return parse_time(summary.get("start")) or datetime.min.replace(tzinfo=UTC)


def _list_arg(value: list[str] | str | None, what: str) -> list[str]:
    """A list the platform may render as a JSON array string or a comma-separated string."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                loaded = json.loads(text)
            except ValueError:
                raise invalid(f"{what} is not valid JSON") from None
            if not isinstance(loaded, list):
                raise invalid(f"{what} must be a list")
            value = [str(v) for v in loaded]
        else:
            value = [part.strip() for part in text.split(",") if part.strip()]
    return value


class Tools:
    def __init__(
        self,
        settings: Settings,
        kube: Kube,
        bucket: Bucket | None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.kube = kube
        self.bucket = bucket
        self.clock = clock or (lambda: datetime.now(UTC))

    # --- guards -----------------------------------------------------------------------------------------------

    def _ready(self) -> None:
        if problem := self.settings.problem():
            raise Failure("not_configured", problem)

    @property
    def prefix(self) -> str:
        return self.settings.restore_namespace_prefix

    def _envelope(self, now: datetime, **body: Any) -> dict[str, Any]:
        return {"source": SOURCE, "as_of": iso(now), **body}

    async def _schedules(self) -> list[dict[str, Any]]:
        return await self.kube.list(self.kube.velero("schedules"))

    async def _backups(self, label_selector: str | None = None) -> list[dict[str, Any]]:
        return await self.kube.list(self.kube.velero("backups"), label_selector)

    # --- reads ------------------------------------------------------------------------------------------------

    async def list_schedules(self) -> dict[str, Any]:
        self._ready()
        now = self.clock()
        schedules, backups = await self._schedules(), await self._backups()
        names = {str(_meta(s).get("name")) for s in schedules}
        by_schedule: dict[str, list[dict[str, Any]]] = {n: [] for n in names}
        for backup in backups:
            owner = schedule_of(backup, names)
            if owner in by_schedule:
                by_schedule[owner].append(backup_summary(backup, now, names))
        rows = []
        for sched in sorted(schedules, key=lambda s: str(_meta(s).get("name"))):
            name = str(_meta(sched).get("name"))
            spec, status = _spec(sched), _status(sched)
            cron = str(spec.get("schedule") or "")
            mine = sorted(by_schedule[name], key=_started, reverse=True)
            last = mine[0] if mine else None
            template = _obj(spec.get("template"))
            paused = bool(spec.get("paused"))
            rows.append(
                {
                    "name": name,
                    "cron": cron,
                    "paused": paused,
                    "phase": status.get("phase") or "New",
                    "validation_errors": _strings(status.get("validationErrors")),
                    "last_backup_status": iso(parse_time(status.get("lastBackup"))),
                    "last_backup": last,
                    "next_run": None if paused else iso(next_fire(cron, now)),
                    "period_hours": period_hours(cron, now),
                    "storage_location": template.get("storageLocation"),
                    "included_namespaces": _strings(template.get("includedNamespaces")),
                    "ttl": template.get("ttl"),
                    "backups_known": len(mine),
                }
            )
        return self._envelope(now, schedules=rows)

    async def list_backups(
        self, schedule: str | None = None, phase: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        self._ready()
        now = self.clock()
        if not 1 <= limit <= MAX_LIST:
            raise invalid(f"limit is between 1 and {MAX_LIST}")
        names = {str(_meta(s).get("name")) for s in await self._schedules()}
        rows = [backup_summary(b, now, names) for b in await self._backups()]
        if schedule:
            rows = [r for r in rows if r["schedule"] == schedule]
        if phase:
            rows = [r for r in rows if str(r["phase"]).lower() == phase.lower()]
        rows.sort(key=_started, reverse=True)
        return self._envelope(now, total=len(rows), backups=rows[:limit], truncated=len(rows) > limit)

    async def get_backup(self, name: str) -> dict[str, Any]:
        self._ready()
        now = self.clock()
        self._name(name, "name")
        backup = await self.kube.get(self.kube.velero("backups", name))
        names = {str(_meta(s).get("name")) for s in await self._schedules()}
        spec, status = _spec(backup), _status(backup)
        summary = backup_summary(backup, now, names)
        pvbs = await self.kube.list(self.kube.velero("podvolumebackups"), f"velero.io/backup-name={name}")
        by_phase = Counter(str(_status(p).get("phase") or "New") for p in pvbs)
        # The backup's own log lives in the bucket behind a DownloadRequest; its URL is never created nor returned.
        return self._envelope(
            now,
            backup={
                **summary,
                "validation_errors": _strings(status.get("validationErrors")),
                "failure_reason": status.get("failureReason"),
                "format_version": status.get("formatVersion"),
                "volume_snapshots_attempted": status.get("volumeSnapshotsAttempted"),
                "volume_snapshots_completed": status.get("volumeSnapshotsCompleted"),
                "item_operations_failed": status.get("backupItemOperationsFailed"),
                "excluded_namespaces": _strings(spec.get("excludedNamespaces")),
                "included_resources": _strings(spec.get("includedResources")),
                "excluded_resources": _strings(spec.get("excludedResources")),
                "volume_snapshot_locations": _strings(spec.get("volumeSnapshotLocations")),
                "default_volumes_to_fs_backup": spec.get("defaultVolumesToFsBackup"),
                "pod_volume_backups": {"total": len(pvbs), "by_phase": dict(sorted(by_phase.items()))},
            },
        )

    async def storage_locations(self) -> dict[str, Any]:
        self._ready()
        now = self.clock()
        rows = []
        for item in await self.kube.list(self.kube.velero("backupstoragelocations")):
            spec, status = _spec(item), _status(item)
            store = _obj(spec.get("objectStorage"))
            phase = status.get("phase")
            validated = parse_time(status.get("lastValidationTime"))
            rows.append(
                {
                    "name": _meta(item).get("name"),
                    "provider": spec.get("provider"),
                    "bucket": store.get("bucket"),
                    "prefix": store.get("prefix"),
                    "access_mode": status.get("accessMode") or spec.get("accessMode"),
                    "default": bool(spec.get("default")),
                    "phase": phase,
                    "available": phase == "Available",
                    "message": str(status.get("message") or "")[:300] or None,
                    "last_validated": iso(validated),
                    "last_validated_age_hours": hours_between(now, validated),
                    "last_synced": iso(parse_time(status.get("lastSyncedTime"))),
                    "validation_frequency": spec.get("validationFrequency"),
                }
            )
        snapshots = [
            {
                "name": _meta(item).get("name"),
                "provider": _spec(item).get("provider"),
                "phase": _status(item).get("phase"),
            }
            for item in await self.kube.list(self.kube.velero("volumesnapshotlocations"))
        ]
        return self._envelope(now, backup_storage_locations=rows, volume_snapshot_locations=snapshots)

    def _restore_row(self, restore: dict[str, Any], now: datetime) -> dict[str, Any]:
        meta, spec, status = _meta(restore), _spec(restore), _status(restore)
        progress = _obj(status.get("progress"))
        started = parse_time(status.get("startTimestamp"))
        mapping = _obj(spec.get("namespaceMapping"))
        return {
            "name": meta.get("name"),
            "backup_name": spec.get("backupName"),
            "phase": status.get("phase") or "New",
            "errors": _count(status.get("errors")),
            "warnings": _count(status.get("warnings")),
            "validation_errors": _strings(status.get("validationErrors")),
            "failure_reason": status.get("failureReason"),
            "start": iso(started),
            "completion": iso(parse_time(status.get("completionTimestamp"))),
            "age_hours": hours_between(now, started or parse_time(meta.get("creationTimestamp"))),
            "namespace_mapping": {str(k): str(v) for k, v in mapping.items()},
            "items_restored": progress.get("itemsRestored"),
            "items_total": progress.get("totalItems"),
            "restore_pvs": spec.get("restorePVs"),
            "excluded_resources": _strings(spec.get("excludedResources")),
            "made_by_this_server": (meta.get("labels") or {}).get(LABEL_MANAGED) == MANAGED_BY,
        }

    async def list_restores(self, backup_name: str | None = None, limit: int = 20) -> dict[str, Any]:
        self._ready()
        now = self.clock()
        if not 1 <= limit <= MAX_LIST:
            raise invalid(f"limit is between 1 and {MAX_LIST}")
        rows = [self._restore_row(r, now) for r in await self.kube.list(self.kube.velero("restores"))]
        if backup_name:
            rows = [r for r in rows if r["backup_name"] == backup_name]
        rows.sort(key=lambda r: parse_time(r["start"]) or datetime.min.replace(tzinfo=UTC), reverse=True)
        return self._envelope(now, total=len(rows), restores=rows[:limit], truncated=len(rows) > limit)

    async def get_restore(self, name: str) -> dict[str, Any]:
        """The restore, and how its item count compares with its backup's.

        `comparison.comparable` is true only when the restore covers exactly the namespaces the backup named
        explicitly and excluded no resource: only then do the two counts measure the same set."""
        self._ready()
        now = self.clock()
        self._name(name, "name")
        restore = await self.kube.get(self.kube.velero("restores", name))
        row = self._restore_row(restore, now)
        comparison: dict[str, Any] = {"comparable": False, "reason": "the backup could not be read"}
        try:
            backup = await self.kube.get(self.kube.velero("backups", str(row["backup_name"])))
        except Failure as failure:
            if failure.code != "not_found":
                raise
            comparison["reason"] = "the backup no longer exists"
        else:
            summary = backup_summary(backup, now)
            included = set(summary["included_namespaces"])
            mapped = set(row["namespace_mapping"]) or set(_strings(_spec(restore).get("includedNamespaces")))
            backed, restored = summary["items_backed_up"], row["items_restored"]
            explicit = bool(included) and not any(c in n for n in included for c in "*?")
            comparable = explicit and mapped == included and not row["excluded_resources"]
            reason = (
                "same namespaces, no resource excluded"
                if comparable
                else "the restore covers "
                + (", ".join(sorted(mapped)) or "?")
                + " of a backup of "
                + (", ".join(sorted(included)) or "every namespace")
                + (" with excluded resources" if row["excluded_resources"] else "")
            )
            ratio = (
                round(restored / backed, 3)
                if isinstance(backed, int) and isinstance(restored, int) and backed
                else None
            )
            comparison = {
                "comparable": comparable,
                "reason": reason,
                "backup_items": backed,
                "restored_items": restored,
                "ratio": ratio,
                "backup_phase": summary["phase"],
                "backup_errors": summary["errors"],
            }
        return self._envelope(now, restore=row, comparison=comparison)

    async def backup_freshness(self, max_age_hours: float | None = None) -> dict[str, Any]:
        """Per schedule: age of its newest Completed backup against what its period allows (or `max_age_hours`).

        Age counts from the backup's START (the point in time the data represents), which is what an RPO
        measures. A PartiallyFailed or Failed backup is never counted as a fresh one."""
        self._ready()
        now = self.clock()
        if max_age_hours is not None and not 0 < max_age_hours <= 24 * 366:
            raise invalid("max_age_hours is between 0 and 8784")
        schedules = await self._schedules()
        names = {str(_meta(s).get("name")) for s in schedules}
        summaries = [backup_summary(b, now, names) for b in await self._backups()]
        locations = {
            str(_meta(i).get("name")): _status(i).get("phase") == "Available"
            for i in await self.kube.list(self.kube.velero("backupstoragelocations"))
        }
        rows = []
        for sched in sorted(schedules, key=lambda s: str(_meta(s).get("name"))):
            name = str(_meta(sched).get("name"))
            spec = _spec(sched)
            mine = sorted((s for s in summaries if s["schedule"] == name), key=_started, reverse=True)
            ok = next((s for s in mine if s["phase"] == "Completed"), None)
            if max_age_hours is not None:
                allowed, basis = max_age_hours, "max_age_hours argument"
            elif (period := period_hours(str(spec.get("schedule") or ""), now)) is not None:
                allowed = round(period * PERIOD_FACTOR + GRACE_HOURS, 2)
                basis = f"{period:g} h period x {PERIOD_FACTOR} + {GRACE_HOURS:g} h"
            else:
                allowed, basis = FALLBACK_HOURS, "the cron expression was not understood: default"
            age = ok["age_hours"] if ok else None
            if bool(spec.get("paused")):
                verdict = "paused"
            elif ok is None:
                verdict = "never"
            else:
                verdict = "stale" if isinstance(age, (int, float)) and age > allowed else "fresh"
            template = _obj(spec.get("template"))
            location = template.get("storageLocation") or next(
                (n for n, available in locations.items() if available), None
            )
            rows.append(
                {
                    "schedule": name,
                    "freshness": verdict,
                    "last_completed_backup": ok["name"] if ok else None,
                    "last_completed_start": ok["start"] if ok else None,
                    "age_hours": age,
                    "allowed_age_hours": allowed,
                    "basis": basis,
                    "newest_backup": mine[0]["name"] if mine else None,
                    "newest_backup_phase": mine[0]["phase"] if mine else None,
                    "newest_backup_errors": mine[0]["errors"] if mine else None,
                    "storage_location": location,
                    "storage_location_available": locations.get(str(location)),
                }
            )
        return self._envelope(now, schedules=rows)

    async def bucket_listing(self, prefix: str | None = None) -> dict[str, Any]:
        """Object count, total size and age of what the backup bucket holds (no object is read)."""
        self._ready()
        now = self.clock()
        if self.bucket is None:
            return self._envelope(now, configured=False, note="no bucket is configured: storage locations only")
        base = self.settings.s3_prefix
        wanted = "/".join(p for p in (base, (prefix or "").strip("/")) if p)
        if prefix and ".." in prefix.split("/"):
            raise invalid("prefix may not contain '..'")
        count, size, token, pages = 0, 0, None, 0
        newest = oldest = None
        top: Counter[str] = Counter()
        while True:
            objects, token = await self.bucket.list_page(now, wanted, token)
            pages += 1
            for obj in objects:
                count += 1
                size += int(obj["size"] or 0)
                stamp = parse_time(obj["last_modified"])
                if stamp:
                    newest = stamp if newest is None or stamp > newest else newest
                    oldest = stamp if oldest is None or stamp < oldest else oldest
                rest = obj["key"].removeprefix(base + "/" if base else "")
                top[rest.split("/", 1)[0] if "/" in rest else "(root)"] += 1
            if token is None or pages >= MAX_PAGES:
                break
        return self._envelope(
            now,
            configured=True,
            bucket=self.settings.s3_bucket,
            prefix=wanted or None,
            object_count=count,
            total_bytes=size,
            newest_object=iso(newest),
            newest_object_age_hours=hours_between(now, newest),
            oldest_object=iso(oldest),
            by_top_folder=dict(sorted(top.items())),
            truncated=token is not None,
        )

    # --- writes -----------------------------------------------------------------------------------------------

    @staticmethod
    def _name(value: str, what: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?", value):
            raise invalid(f"{what} is not a valid Kubernetes object name")

    def _guard_namespace(self, namespace: str) -> None:
        """The one rule every write goes through: the namespace is in the restore prefix, a valid name, and
        not the Velero namespace."""
        if not re.fullmatch(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?", namespace or ""):
            raise invalid("the namespace is not a valid DNS label")
        if not namespace.startswith(self.prefix) or len(namespace) == len(self.prefix):
            raise refused(f"{namespace!r} is outside the restore prefix {self.prefix!r}: this server only writes there")
        if namespace == self.settings.velero_namespace:
            raise refused("the Velero namespace is never a target")

    async def create_test_restore(
        self,
        backup_name: str,
        source_namespace: str,
        name: str | None = None,
        restore_volumes: bool = False,
        exclude_resources: list[str] | str | None = None,
        action_key: str | None = None,
    ) -> dict[str, Any]:
        """Restore ONE namespace of a backup into `<prefix><label>-<yyyymmdd>`. Idempotent on `action_key`."""
        self._ready()
        now = self.clock()
        self._name(backup_name, "backup_name")
        if not re.fullmatch(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?", source_namespace or ""):
            raise invalid("source_namespace is not a valid namespace name")
        if source_namespace.startswith(self.prefix):
            raise refused("the source is itself a restore-test namespace")
        label = name or source_namespace
        if not LABEL_VALUE.fullmatch(label):
            raise invalid("name must be lower-case letters, digits and dashes")
        excluded = _list_arg(exclude_resources, "exclude_resources")
        if len(excluded) > 20 or not all(RESOURCE.fullmatch(r) for r in excluded):
            raise invalid("exclude_resources: at most 20 resource names such as cronjobs.batch")
        target = f"{self.prefix}{label}-{now:%Y%m%d}"
        self._guard_namespace(target)  # also bounds the length: 63
        digest = hashlib.sha256((action_key or f"{backup_name}/{source_namespace}/{target}").encode()).hexdigest()[:12]
        restore_name = f"vt-{digest}"
        labels = {LABEL_MANAGED: MANAGED_BY, LABEL_TEST: "true"}

        existing = await self._get_or_none(self.kube.velero("restores", restore_name))
        if existing is not None:
            return self._created(now, existing, backup_name, source_namespace, target, True)

        backup = await self.kube.get(self.kube.velero("backups", backup_name))
        phase = _status(backup).get("phase")
        if phase not in RESTORABLE:
            raise refused(f"backup {backup_name!r} is {phase}: only Completed or PartiallyFailed backups are restored")
        included = _strings(_spec(backup).get("includedNamespaces"))
        if included and not any(fnmatchcase(source_namespace, pattern) for pattern in included):
            raise invalid(f"{source_namespace!r} is not among the backup's namespaces {included}")

        namespace = await self._get_or_none(f"/api/v1/namespaces/{target}")
        if namespace is None:
            await self.kube.request(
                "POST",
                "/api/v1/namespaces",
                body={
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": target,
                        "labels": labels,
                        "annotations": {
                            "tranibor.io/source-backup": backup_name,
                            "tranibor.io/source-namespace": source_namespace,
                            "tranibor.io/created-at": iso(now) or "",
                        },
                    },
                },
            )
        elif (_meta(namespace).get("labels") or {}).get(LABEL_TEST) != "true":
            raise refused(f"namespace {target!r} already exists and is not a restore-test namespace of this server")

        spec: dict[str, Any] = {
            "backupName": backup_name,
            "includedNamespaces": [source_namespace],
            "namespaceMapping": {source_namespace: target},
            "includeClusterResources": False,
            "restorePVs": bool(restore_volumes),
        }
        if excluded:
            spec["excludedResources"] = excluded
        manifest = {
            "apiVersion": "velero.io/v1",
            "kind": "Restore",
            "metadata": {
                "name": restore_name,
                "namespace": self.settings.velero_namespace,
                "labels": {**labels, LABEL_KEY: digest},
                "annotations": {"tranibor.io/target-namespace": target},
            },
            "spec": spec,
        }
        try:
            created = await self.kube.request("POST", self.kube.velero("restores"), body=manifest)
        except Failure as failure:
            if failure.code != "conflict":
                raise
            created = await self.kube.get(self.kube.velero("restores", restore_name))
            return self._created(now, created, backup_name, source_namespace, target, True)
        return self._created(now, created, backup_name, source_namespace, target, False)

    def _created(
        self, now: datetime, restore: dict[str, Any], backup_name: str, source: str, target: str, existed: bool
    ) -> dict[str, Any]:
        spec = _spec(restore)
        mapping = _obj(spec.get("namespaceMapping"))
        return self._envelope(
            now,
            restore_name=_meta(restore).get("name"),
            backup_name=spec.get("backupName") or backup_name,
            source_namespace=source,
            target_namespace=str(mapping.get(source) or target),
            restore_volumes=spec.get("restorePVs"),
            phase=_status(restore).get("phase") or "New",
            already_existed=existed,
        )

    async def _get_or_none(self, path: str) -> dict[str, Any] | None:
        try:
            return await self.kube.get(path)
        except Failure as failure:
            if failure.code == "not_found":
                return None
            raise

    async def delete_test_restore_namespace(self, namespace: str) -> dict[str, Any]:
        """Delete a restore-test namespace: in the prefix, and labelled by this server when it created it."""
        self._ready()
        now = self.clock()
        self._guard_namespace(namespace)
        found = await self._get_or_none(f"/api/v1/namespaces/{namespace}")
        if found is None:
            return self._envelope(now, namespace=namespace, deleted=False, already_gone=True)
        if (_meta(found).get("labels") or {}).get(LABEL_TEST) != "true":
            raise refused(f"namespace {namespace!r} was not created by this server for a restore test: not deleted")
        if _meta(found).get("deletionTimestamp"):
            return self._envelope(now, namespace=namespace, deleted=False, terminating=True)
        await self.kube.request("DELETE", f"/api/v1/namespaces/{namespace}")
        return self._envelope(now, namespace=namespace, deleted=True, terminating=True)
