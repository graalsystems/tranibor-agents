# SPDX-License-Identifier: Apache-2.0
"""The six read tools. None of them writes anywhere: there is no write path in this server."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .ct import CtClient, covers, entry_view
from .errors import Refused, SourceError
from .kube import (
    CERTIFICATE_REQUESTS,
    CERTIFICATES,
    CHALLENGES,
    CLUSTER_ISSUERS,
    ISSUERS,
    ORDERS,
    KubeClient,
)
from .probe import Prober, check_target
from .views import (
    certificate_view,
    challenge_view,
    clip,
    conditions_of,
    issuer_view,
    name_of,
    namespace_of,
    order_view,
    references,
    request_view,
)

MAX_LIST = 200
#: A challenge in one of these states is finished, whatever else the object says.
DONE_STATES = ("valid",)

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


class CertWatchTools:
    def __init__(self, settings: Settings, kube: KubeClient, ct: CtClient, prober: Prober, clock: Clock = _now) -> None:
        self._settings, self._kube, self._ct, self._prober, self._clock = settings, kube, ct, prober, clock

    # ───────────────────────── certificates ─────────────────────────

    async def list_certificates(
        self, namespace: str | None = None, ready: bool | None = None, limit: int = 100
    ) -> dict[str, Any]:
        now = self._clock()
        objects = await self._kube.list(CERTIFICATES, namespace)
        views = [certificate_view(o, now) for o in objects]
        if ready is not None:
            views = [v for v in views if (v["ready"] is not None and v["ready"]["status"] == "True") == ready]
        views.sort(
            key=lambda v: (v["days_remaining"] is None, v["days_remaining"] or 0, v["namespace"] or "", v["name"])
        )
        capped = views[: max(1, min(limit, MAX_LIST))]
        return {"total": len(views), "truncated": len(capped) < len(views), "certificates": capped}

    async def get_certificate(self, namespace: str, name: str) -> dict[str, Any]:
        """The certificate with what it is waiting on: requests, orders, challenges, and its issuer.

        Linked by ownerReferences (Certificate -> CertificateRequest -> Order -> Challenge), as cert-manager
        sets them. Objects are listed in the certificate's namespace and filtered here: no per-object guess."""
        now = self._clock()
        certificate = await self._kube.get(CERTIFICATES, name, namespace)
        requests = [
            r
            for r in await self._kube.list(CERTIFICATE_REQUESTS, namespace)
            if references(certificate, "Certificate", r)
        ]
        orders = [
            o
            for o in await self._kube.list(ORDERS, namespace)
            if any(references(r, "CertificateRequest", o) for r in requests)
        ]
        challenges = [
            c for c in await self._kube.list(CHALLENGES, namespace) if any(references(o, "Order", c) for o in orders)
        ]
        view = certificate_view(certificate, now)
        issuer = await self._issuer(certificate, namespace)
        requests_view = sorted((request_view(r) for r in requests), key=lambda r: str(r["created"]), reverse=True)
        return {
            "certificate": view,
            "conditions": conditions_of(certificate),
            "issuer": issuer,
            "certificate_requests": requests_view,
            "orders": [order_view(o) for o in orders],
            "challenges": [challenge_view(c, now) for c in challenges],
            "signals": sorted(
                {s for r in requests_view for s in r["signals"]}
                | {s for o in orders for s in order_view(o)["signals"]}
                | {s for c in challenges for s in challenge_view(c, now)["signals"]}
            ),
        }

    async def _issuer(self, certificate: dict[str, Any], namespace: str) -> dict[str, Any] | None:
        ref = certificate.get("spec", {}).get("issuerRef") or {}
        kind, issuer_name = str(ref.get("kind") or "Issuer"), str(ref.get("name") or "")
        if ref.get("group", "cert-manager.io") != "cert-manager.io" or not issuer_name:
            return {"kind": kind, "name": issuer_name, "note": "an external issuer: its status is not read"}
        try:
            if kind == "ClusterIssuer":
                return issuer_view(await self._kube.get(CLUSTER_ISSUERS, issuer_name, None))
            if kind == "Issuer":
                return issuer_view(await self._kube.get(ISSUERS, issuer_name, namespace))
        except SourceError as error:
            return {"kind": kind, "name": issuer_name, "note": f"not readable: {error}"}
        return {"kind": kind, "name": issuer_name, "note": "an external issuer: its status is not read"}

    # ───────────────────────── challenges ─────────────────────────

    async def list_failing_challenges(self, namespace: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Every challenge that is not `valid`, with the certificate it belongs to (via its order and request)."""
        now = self._clock()
        challenges = [
            c
            for c in await self._kube.list(CHALLENGES, namespace)
            if str((c.get("status") or {}).get("state", "")) not in DONE_STATES
        ]
        orders = await self._kube.list(ORDERS, namespace) if challenges else []
        requests = await self._kube.list(CERTIFICATE_REQUESTS, namespace) if challenges else []
        found = []
        for challenge in challenges:
            view = challenge_view(challenge, now)
            order = next((o for o in orders if references(o, "Order", challenge)), None)
            request = next((r for r in requests if order and references(r, "CertificateRequest", order)), None)
            view["order"] = (
                None
                if order is None
                else {
                    "name": name_of(order),
                    "state": order_view(order)["state"],
                    "reason": order_view(order)["reason"],
                }
            )
            view["certificate"] = None
            if request is not None:
                owner = next(iter((request.get("metadata") or {}).get("ownerReferences") or []), None)
                if isinstance(owner, dict) and owner.get("kind") == "Certificate":
                    view["certificate"] = {"namespace": namespace_of(request), "name": clip(owner.get("name"), 253)}
            found.append(view)
        found.sort(key=lambda v: -(v["age_minutes"] or 0))
        capped = found[: max(1, min(limit, MAX_LIST))]
        return {"total": len(found), "truncated": len(capped) < len(found), "challenges": capped}

    # ───────────────────────── CT ─────────────────────────

    async def ct_search(
        self, domain: str, include_subdomains: bool = True, undeclared_only: bool = False
    ) -> dict[str, Any]:
        """CT entries for a configured domain, each marked as declared (or not) by a cert-manager Certificate."""
        now = self._clock()
        entries = await self._ct.search(domain, include_subdomains)
        managed: dict[str, tuple[str, str]] = {}
        declared_known = True
        try:
            for obj in await self._kube.list(CERTIFICATES, None):
                for dns in (obj.get("spec") or {}).get("dnsNames") or []:
                    managed.setdefault(str(dns).lower(), (namespace_of(obj) or "", name_of(obj)))
        except (SourceError, Refused):
            declared_known = False
        views = []
        for raw in entries:
            view = entry_view(raw, now)
            if declared_known:
                owners = {n: next((managed[m] for m in managed if covers(m, n)), None) for n in view["names"]}
                view["unmatched_names"] = sorted(n for n, o in owners.items() if o is None)
                view["declared_by"] = sorted({f"{o[0]}/{o[1]}" for o in owners.values() if o})
                view["declared"] = not view["unmatched_names"]
            else:
                view.update({"unmatched_names": None, "declared_by": None, "declared": None})
            views.append(view)
        views.sort(key=lambda v: (str(v["not_after"] or ""), v["ct_id"] or 0), reverse=True)
        undeclared = [v for v in views if v["declared"] is False]
        shown = undeclared if undeclared_only else views
        return {
            "domain": self._ct.check_domain(domain),
            "total": len(views),
            "declared_known": declared_known,
            "undeclared_total": len(undeclared),
            "undeclared_current": [v["ct_id"] for v in undeclared if not v["expired"]],
            "truncated": len(shown) > MAX_LIST,
            "entries": shown[:MAX_LIST],
        }

    # ───────────────────────── probe ─────────────────────────

    async def probe_tls(self, host: str) -> dict[str, Any]:
        target_host, port = check_target(self._settings, host)
        return await self._prober.probe(target_host, port)

    # ───────────────────────── report ─────────────────────────

    async def expiry_report(self, days: int = 30) -> dict[str, Any]:
        """One view of the estate: what expires within `days`, what is not Ready, what is stuck, what the
        probes serve. A source that fails is reported in `errors`; the others still answer."""
        if not 1 <= days <= 3650:
            raise Refused("days must be between 1 and 3650")
        now = self._clock()
        errors: dict[str, str] = {}
        report: dict[str, Any] = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "window_days": days}
        try:
            certs = [certificate_view(o, now) for o in await self._kube.list(CERTIFICATES, None)]
        except (SourceError, Refused) as error:
            certs, errors["kubernetes"] = [], str(error)
        expiring = [c for c in certs if c["days_remaining"] is not None and c["days_remaining"] <= days]
        not_ready = [c for c in certs if c["ready"] is None or c["ready"]["status"] != "True"]
        report["certificates_total"] = len(certs)
        report["expiring"] = sorted(expiring, key=lambda c: c["days_remaining"])
        report["not_ready"] = not_ready
        report["renewal_failing"] = [c for c in certs if c["failed_issuance_attempts"] > 0 and c not in not_ready]
        try:
            failing = (await self.list_failing_challenges(None, MAX_LIST))["challenges"]
        except (SourceError, Refused) as error:
            failing, errors["challenges"] = [], str(error)
        report["failing_challenges"] = failing
        report["failing_signals"] = sorted({s for c in failing for s in c["signals"]})
        results = await asyncio.gather(
            *(self._prober.probe(h, p) for h, p in self._settings.probe_hosts), return_exceptions=True
        )
        probes = []
        for (host, port), result in zip(self._settings.probe_hosts, results, strict=True):
            if isinstance(result, SourceError):
                probes.append({"endpoint": f"{host}:{port}", "error": str(result)})
            elif isinstance(result, BaseException):
                raise result
            else:
                probes.append(result)
        report["probes"] = probes
        report["probe_findings"] = [
            {"endpoint": p["endpoint"], "finding": f} for p in probes for f in _probe_findings(p, days)
        ]
        report["errors"] = errors
        return report


def _probe_findings(probe: dict[str, Any], days: int) -> list[str]:
    if "error" in probe:
        return ["unreachable"]
    findings = []
    if probe["expired"]:
        findings.append("expired")
    elif probe["days_remaining"] is not None and probe["days_remaining"] <= days:
        findings.append("expires_within_window")
    if not probe["chain_complete"]:
        findings.append("chain_incomplete")
    if not probe["trusted"]:
        findings.append("not_trusted")
    if not probe["hostname_matches"]:
        findings.append("hostname_mismatch")
    return findings
