# SPDX-License-Identifier: Apache-2.0
"""A seeded, in-memory stand-in for the three sources, so tests and demos need no cluster and no network.

`FakeKube` and `FakeCt` are `httpx` mock transports: the real clients run unchanged against them, so the
request paths, pagination and parsing are exercised. `FakeProber` returns seeded probe results. Everything
is relative to the `now` given, so a demo stays meaningful whatever the day. All names are invented
(example.com, RFC 5737 addresses); nothing here is a real credential.

The seeded situations:
  shop/shop-tls          healthy, 60 days left
  shop/api-tls           9 days left, renewal overdue: an HTTP-01 challenge fails with a 404 (wrong ingress)
  mail/mail-wildcard-tls first issuance of a wildcard, stuck on a DNS-01 propagation wait
  legacy/legacy-tls      4 days left, the last order was refused by the CA's rate limit
CT holds those plus `vpn.example.com` (issued by a CA this cluster never uses: nobody declared it) and an old,
expired `staging.example.com`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx

from .config import normalise_domain
from .errors import SourceError
from .probe import hostname_matches

_ISSUER = "letsencrypt-prod"
_ACME = "https://acme-v02.api.example.com/directory"


def _t(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _meta(name: str, namespace: str | None, created: datetime, owner: dict[str, Any] | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "name": name,
        "creationTimestamp": _t(created),
        "uid": f"uid-{namespace or 'cluster'}-{name}",
    }
    if namespace:
        meta["namespace"] = namespace
    if owner:
        meta["ownerReferences"] = [
            {"apiVersion": "cert-manager.io/v1", "kind": owner["kind"], "name": owner["name"], "uid": owner["uid"]}
        ]
    return meta


def _cond(kind: str, status: str, reason: str, message: str, when: datetime) -> dict[str, str]:
    return {"type": kind, "status": status, "reason": reason, "message": message, "lastTransitionTime": _t(when)}


def seed_kube(now: datetime) -> dict[str, list[dict[str, Any]]]:
    """The cert-manager objects, by plural."""
    store: dict[str, list[dict[str, Any]]] = {
        k: [] for k in ("certificates", "certificaterequests", "issuers", "clusterissuers", "orders", "challenges")
    }
    store["clusterissuers"].append(
        {
            "kind": "ClusterIssuer",
            "metadata": _meta(_ISSUER, None, now - timedelta(days=400)),
            "spec": {"acme": {"server": _ACME, "email": "pki@example.com"}},
            "status": {
                "conditions": [_cond("Ready", "True", "ACMEAccountRegistered", "The ACME account was registered", now)]
            },
        }
    )
    store["issuers"].append(
        {
            "kind": "Issuer",
            "metadata": _meta("internal-ca", "legacy", now - timedelta(days=300)),
            "spec": {"ca": {"secretName": "internal-ca-key"}},
            "status": {"conditions": [_cond("Ready", "True", "KeyPairVerified", "Signing CA verified", now)]},
        }
    )

    def certificate(
        ns: str,
        name: str,
        dns: list[str],
        *,
        left: float | None,
        renewal: float | None,
        ready: tuple[str, str, str],
        issuing: bool = False,
        failures: int = 0,
        revision: int | None = 1,
        issuer_kind: str = "ClusterIssuer",
        issuer_name: str = _ISSUER,
    ) -> dict[str, Any]:
        status: dict[str, Any] = {"conditions": [_cond("Ready", *ready, now - timedelta(days=30))]}
        if left is not None:
            status["notAfter"] = _t(now + timedelta(days=left))
            status["notBefore"] = _t(now + timedelta(days=left - 90))
        if renewal is not None:
            status["renewalTime"] = _t(now + timedelta(days=renewal))
        if revision is not None:
            status["revision"] = revision
        if failures:
            status["failedIssuanceAttempts"] = failures
            status["lastFailureTime"] = _t(now - timedelta(hours=3))
        if issuing:
            status["conditions"].append(
                _cond(
                    "Issuing", "True", "Renewing", "Renewing certificate as renewal was scheduled for " + _t(now), now
                )
            )
        obj = {
            "kind": "Certificate",
            "metadata": _meta(name, ns, now - timedelta(days=200)),
            "spec": {
                "secretName": f"{name}-secret",
                "dnsNames": dns,
                "issuerRef": {"name": issuer_name, "kind": issuer_kind, "group": "cert-manager.io"},
                "duration": "2160h0m0s",
                "renewBefore": "720h0m0s",
            },
            "status": status,
        }
        store["certificates"].append(obj)
        return obj

    def request(
        cert: dict[str, Any], revision: int, ready: tuple[str, str] | None, message: str = ""
    ) -> dict[str, Any]:
        ns = cert["metadata"]["namespace"]
        name = f"{cert['metadata']['name']}-{revision}"
        conditions = [_cond("Approved", "True", "cert-manager.io", "Certificate request has been approved", now)]
        if ready:
            conditions.append(_cond("Ready", ready[0], ready[1], message, now))
        obj = {
            "kind": "CertificateRequest",
            "metadata": {
                **_meta(name, ns, now - timedelta(hours=4), cert["metadata"] | {"kind": "Certificate"}),
                "annotations": {"cert-manager.io/certificate-revision": str(revision)},
            },
            "spec": {"issuerRef": cert["spec"]["issuerRef"]},
            "status": {"conditions": conditions},
        }
        store["certificaterequests"].append(obj)
        return obj

    def order(
        req: dict[str, Any], dns: list[str], state: str, reason: str = "", challenge_type: str = "http-01"
    ) -> dict[str, Any]:
        ns = req["metadata"]["namespace"]
        obj: dict[str, Any] = {
            "kind": "Order",
            "metadata": _meta(
                f"{req['metadata']['name']}-1234567890",
                ns,
                now - timedelta(hours=4),
                req["metadata"] | {"kind": "CertificateRequest"},
            ),
            "spec": {"dnsNames": dns, "issuerRef": req["spec"]["issuerRef"]},
            "status": {
                "state": state,
                "url": f"{_ACME.rsplit('/', 1)[0]}/acme/order/1/2",
                "authorizations": [
                    {
                        "identifier": d.removeprefix("*."),
                        "wildcard": d.startswith("*."),
                        "initialState": "pending",
                        "challenges": [{"type": challenge_type, "url": "https://acme-v02.api.example.com/chall/1"}],
                    }
                    for d in dns
                ],
            },
        }
        if reason:
            obj["status"]["reason"] = reason
            obj["status"]["failureTime"] = _t(now - timedelta(hours=3))
        store["orders"].append(obj)
        return obj

    def challenge(od: dict[str, Any], dns_name: str, kind: str, reason: str, wildcard: bool = False) -> None:
        solver = (
            {"http01": {"ingress": {"ingressClassName": "nginx"}}}
            if kind == "HTTP-01"
            else {"dns01": {"cloudDNS": {"project": "example-project"}}}
        )
        store["challenges"].append(
            {
                "kind": "Challenge",
                "metadata": _meta(
                    f"{od['metadata']['name']}-9876543210",
                    od["metadata"]["namespace"],
                    now - timedelta(hours=4),
                    od["metadata"] | {"kind": "Order"},
                ),
                "spec": {
                    "type": kind,
                    "dnsName": dns_name,
                    "wildcard": wildcard,
                    "solver": solver,
                    "issuerRef": od["spec"]["issuerRef"],
                    "token": "fake-token-not-real",
                    "key": "fake-key-not-real",
                },
                "status": {"state": "pending", "reason": reason, "presented": kind == "DNS-01", "processing": True},
            }
        )

    certificate(
        "shop",
        "shop-tls",
        ["shop.example.com", "www.shop.example.com"],
        left=60,
        renewal=30,
        ready=("True", "Ready", "Certificate is up to date and has not expired"),
    )
    api = certificate(
        "shop",
        "api-tls",
        ["api.shop.example.com"],
        left=9,
        renewal=-20,
        ready=("True", "Ready", "Certificate is up to date and has not expired"),
        issuing=True,
        failures=3,
        revision=4,
    )
    req = request(
        api, 5, ("False", "Pending"), 'Waiting on certificate issuance from order shop/api-tls-5-1234567890: "pending"'
    )
    od = order(req, ["api.shop.example.com"], "pending")
    challenge(
        od,
        "api.shop.example.com",
        "HTTP-01",
        "Waiting for HTTP-01 challenge propagation: wrong status code '404', expected '200'",
    )
    wild = certificate(
        "mail",
        "mail-wildcard-tls",
        ["*.mail.example.com", "mail.example.com"],
        left=None,
        renewal=None,
        ready=("False", "DoesNotExist", "Issuing certificate as Secret does not exist"),
        issuing=True,
        revision=None,
    )
    req = request(
        wild,
        1,
        ("False", "Pending"),
        'Waiting on certificate issuance from order mail/mail-wildcard-tls-1-1234567890: "pending"',
    )
    od = order(req, ["*.mail.example.com", "mail.example.com"], "pending", challenge_type="dns-01")
    challenge(
        od,
        "example.com",
        "DNS-01",
        'Waiting for DNS-01 challenge propagation: DNS record for "example.com" not yet propagated',
        wildcard=True,
    )
    legacy = certificate(
        "legacy",
        "legacy-tls",
        ["legacy.example.com"],
        left=4,
        renewal=-26,
        ready=("True", "Ready", "Certificate is up to date and has not expired"),
        issuing=True,
        failures=2,
        revision=7,
    )
    req = request(
        legacy,
        8,
        ("False", "Failed"),
        'Failed to wait for order resource "legacy-tls-8-1234567890" to become ready: order is in "errored" state',
    )
    order(
        req,
        ["legacy.example.com"],
        "errored",
        "Failed to create Order: 429 urn:ietf:params:acme:error:rateLimited: too many certificates (5) already "
        "issued for this exact set of identifiers in the last 168 hours",
    )
    return store


def seed_ct(now: datetime) -> list[dict[str, Any]]:
    def entry(ct_id: int, names: list[str], issuer: str, days_ago: int, left: int) -> dict[str, Any]:
        issued = now - timedelta(days=days_ago)
        stamp = issued.strftime("%Y-%m-%dT%H:%M:%S")
        return {
            "issuer_ca_id": 1,
            "issuer_name": issuer,
            "common_name": names[0],
            "name_value": "\n".join(names),
            "id": ct_id,
            "entry_timestamp": stamp + ".123",
            "not_before": stamp,
            "not_after": (now + timedelta(days=left)).strftime("%Y-%m-%dT%H:%M:%S"),
            "serial_number": f"{ct_id:032x}",
            "result_count": 1,
        }

    ca = "C=XX, O=Example Public CA, CN=Example Public CA R1"
    return [
        entry(1001, ["shop.example.com", "www.shop.example.com"], ca, 30, 60),
        entry(1002, ["api.shop.example.com"], ca, 81, 9),
        entry(1003, ["legacy.example.com"], ca, 86, 4),
        entry(1004, ["vpn.example.com"], "C=XX, O=Other Authority, CN=Other Authority DV CA", 12, 78),
        entry(1005, ["staging.example.com"], ca, 400, -310),
    ]


class FakeKube:
    """An `httpx` handler answering the list/get paths of the six cert-manager collections."""

    def __init__(self, now: datetime | Callable[[], datetime] | None = None) -> None:
        self._now = now if callable(now) else (lambda: now or datetime.now(UTC))
        self.store = seed_kube(self._now())
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.raw_path.decode()))
        if request.headers.get("authorization") != "Bearer fake-kube-token":
            return httpx.Response(401, json={"kind": "Status", "reason": "Unauthorized"})
        if request.method != "GET":
            return httpx.Response(405, json={"kind": "Status", "reason": "MethodNotAllowed"})
        parts = [p for p in request.url.path.split("/") if p]
        if len(parts) < 4 or parts[0] != "apis":
            return httpx.Response(404, json={"kind": "Status", "reason": "NotFound"})
        rest = parts[3:]
        namespace = None
        if rest[0] == "namespaces" and len(rest) >= 3:
            namespace, rest = rest[1], rest[2:]
        plural, name = rest[0], (rest[1] if len(rest) > 1 else None)
        items = self.store.get(plural)
        if items is None:
            return httpx.Response(404, json={"kind": "Status", "reason": "NotFound"})
        scoped = [i for i in items if namespace is None or i["metadata"].get("namespace") == namespace]
        if name:
            found = next((i for i in scoped if i["metadata"]["name"] == name), None)
            return (
                httpx.Response(200, json=found)
                if found
                else httpx.Response(404, json={"kind": "Status", "reason": "NotFound"})
            )
        limit = int(request.url.params.get("limit", "0") or 0)
        start = int(request.url.params.get("continue", "0") or 0)
        page = scoped[start : start + limit] if limit else scoped
        more = bool(limit) and start + limit < len(scoped)
        return httpx.Response(
            200, json={"kind": "List", "metadata": {"continue": str(start + limit) if more else ""}, "items": page}
        )


class FakeCt:
    """An `httpx` handler imitating crt.sh's `?q=…&output=json`."""

    def __init__(self, now: datetime | Callable[[], datetime] | None = None) -> None:
        self._now = now if callable(now) else (lambda: now or datetime.now(UTC))
        self.entries = seed_ct(self._now())
        self.queries: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = unquote(request.url.params.get("q", ""))
        self.queries.append(query)
        if request.url.params.get("output") != "json":
            return httpx.Response(200, text="<html>not json</html>")
        wildcard = query.startswith("%.")
        domain = normalise_domain(query)
        hits = [
            e
            for e in self.entries
            if any(n == domain or (wildcard and n.endswith("." + domain)) for n in e["name_value"].splitlines())
        ]
        return httpx.Response(200, content=json.dumps(hits), headers={"content-type": "application/json"})


class FakeProber:
    """Seeded probe results; `down.example.com` does not answer."""

    def __init__(self, now: datetime | Callable[[], datetime] | None = None) -> None:
        self._now = now if callable(now) else (lambda: now or datetime.now(UTC))

    async def probe(self, host: str, port: int) -> dict[str, Any]:
        now = self._now()
        if host == "down.example.com":
            raise SourceError(f"{host}:{port}: no TLS handshake (TimeoutError)")
        left, complete, trusted, error = {
            "shop.example.com": (60, True, True, None),
            "api.shop.example.com": (9, True, True, None),
            "legacy.example.com": (4, False, False, "unable to get local issuer certificate"),
        }.get(host, (30, True, True, None))
        view: dict[str, Any] = {
            "subject": host,
            "issuer": "Example Public CA R1, Example Public CA",
            "serial": "0a1b2c3d",
            "san_dns": [host],
            "san_ip": [],
            "not_before": _t(now + timedelta(days=left - 90)),
            "not_after": _t(now + timedelta(days=left)),
            "days_remaining": left,
            "expired": False,
            "self_signed": False,
            "sha256": "00" * 32,
            "chain_length": 2 if complete else 1,
            "chain_complete": complete,
        }
        return {
            "endpoint": f"{host}:{port}",
            "tls_version": "TLSv1.3",
            **view,
            "hostname_matches": hostname_matches(view["san_dns"], host),
            "trusted": trusted,
            "trust_error": error,
        }


__all__ = ["FakeCt", "FakeKube", "FakeProber", "seed_ct", "seed_kube"]
