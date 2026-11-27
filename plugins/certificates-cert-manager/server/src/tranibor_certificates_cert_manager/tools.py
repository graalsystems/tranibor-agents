# SPDX-License-Identifier: Apache-2.0
"""The four read tools of the profile, over cert-manager. None of them writes anywhere.

A certificate's id is `<namespace>/<name>` of the cert-manager `Certificate`. What a renewal is waiting on is
read from the objects cert-manager chains to it by owner references: CertificateRequest -> Order -> Challenge.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .errors import Refused, SourceError
from .kube import CERTIFICATE_REQUESTS, CERTIFICATES, CHALLENGES, CLUSTER_ISSUERS, ISSUERS, ORDERS, KubeClient
from .model import (
    CRITICAL_DAYS,
    DEFAULT_WINDOW,
    certificate,
    finding,
    findings_answer,
    list_answer,
    renewal,
    window_findings,
)
from .util import clip, parse_time
from .views import (
    certificate_view,
    challenge_view,
    condition,
    issuer_view,
    order_view,
    references,
    request_view,
)

Clock = Callable[[], datetime]

SEES = (
    "The cert-manager Certificates the service account may read: their dates, issuer, Ready state, and what a "
    "renewal is waiting on (requests, ACME orders and challenges). It cannot see a certificate cert-manager does "
    "not manage, nor what a client is actually served."
)


def _now() -> datetime:
    return datetime.now(UTC)


def split_id(value: str) -> tuple[str, str]:
    namespace, _, name = value.partition("/")
    if not namespace or not name or "/" in name:
        raise Refused(f"{value!r} is not a certificate id: expected <namespace>/<name> of a cert-manager Certificate")
    return namespace, name


def renewal_state(view: dict[str, Any]) -> tuple[str, str]:
    """The profile's renewal state of a certificate view, and the sentence that backs it."""
    attempts = view["failed_issuance_attempts"]
    if attempts:
        return (
            "failing",
            f"{attempts} failed issuance attempt(s), last at {view['last_failure_time'] or 'an unknown time'}",
        )
    if view["not_after"] is None:
        return "pending", "not issued yet"
    if view["renewal_overdue"]:
        return "due", f"renewalTime {view['renewal_time']} has passed and no new revision was issued"
    if view["issuing"]:
        return "pending", "cert-manager is issuing a new revision"
    return "ok", f"renews at {view['renewal_time']}" if view["renewal_time"] else "no renewal pending"


def _issuer_text(view: dict[str, Any]) -> str:
    ref = view["issuer"]
    return f"{ref['kind']}/{ref['name']}" if ref["name"] else "unknown"


def _view(obj: dict[str, Any], now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    """(cert-manager's own view, the profile's certificate)."""
    raw = certificate_view(obj, now)
    state, detail = renewal_state(raw)
    names = [*raw["dns_names"], *raw["ip_addresses"]] or ([raw["common_name"]] if raw["common_name"] else [])
    cert = certificate(
        id=f"{raw['namespace']}/{raw['name']}",
        names=names,
        issuer=_issuer_text(raw),
        renewal=renewal(
            managed=True,
            state=state,
            manager="cert-manager",
            detail=detail,
            next_renewal_at=raw["renewal_time"] or "",
        ),
        now=now,
        not_after=parse_time(raw["not_after"]),
        not_before=parse_time(raw["not_before"]),
        subject=raw["common_name"] or "",
        location=f"secret {raw['namespace']}/{raw['secret_name']}" if raw["secret_name"] else "",
    )
    return raw, cert


class Chain:
    """What cert-manager chained to one Certificate, read once for a whole namespace."""

    def __init__(self, requests: list[dict[str, Any]], orders: list[dict[str, Any]], challenges: list[dict[str, Any]]):
        self.requests, self.orders, self.challenges = requests, orders, challenges

    def of(self, certificate_obj: dict[str, Any]) -> Chain:
        requests = [r for r in self.requests if references(certificate_obj, "Certificate", r)]
        orders = [o for o in self.orders if any(references(r, "CertificateRequest", o) for r in requests)]
        challenges = [c for c in self.challenges if any(references(o, "Order", c) for o in orders)]
        return Chain(requests, orders, challenges)

    def attempts(self, now: datetime) -> tuple[list[dict[str, Any]], list[str]]:
        out: list[dict[str, Any]] = []
        signals: set[str] = set()
        for r in sorted(self.requests, key=lambda r: str(request_view(r)["created"]), reverse=True):
            v = request_view(r)
            ready = v["ready"] or {}
            out.append(
                _attempt("request", v["name"], ready.get("reason") or "pending", ready.get("message", ""), v["signals"])
            )
            signals.update(v["signals"])
        for o in self.orders:
            v = order_view(o)
            out.append(_attempt("order", v["name"], v["state"] or "unknown", v["reason"], v["signals"]))
            signals.update(v["signals"])
        for c in self.challenges:
            v = challenge_view(c, now)
            detail = f"{v['type']} for {v['dns_name']} via {v['solver']['kind']}: {v['reason']}".strip()
            out.append(_attempt("challenge", v["name"], v["state"] or "unknown", detail, v["signals"]))
            signals.update(v["signals"])
        return out, sorted(signals)


def _attempt(kind: str, name: str, state: str, detail: str, signals: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "name": name, "state": state}
    if detail:
        out["detail"] = clip(detail)
    if signals:
        out["signals"] = signals
    return out


class CertManagerTools:
    def __init__(self, settings: Settings, kube: KubeClient, clock: Clock = _now) -> None:
        self._settings, self._kube, self._clock = settings, kube, clock

    async def _objects(self) -> list[dict[str, Any]]:
        return await self._kube.list(CERTIFICATES, None)

    async def _chain(self, namespace: str | None) -> Chain:
        return Chain(
            await self._kube.list(CERTIFICATE_REQUESTS, namespace),
            await self._kube.list(ORDERS, namespace),
            await self._kube.list(CHALLENGES, namespace),
        )

    async def get_provider(self) -> dict[str, Any]:
        allowed = list(self._settings.namespaces)
        return {
            "instance": self._settings.instance,
            "provider": "cert-manager",
            "kind": "manager",
            "scope": [f"namespace {n}" for n in allowed] or ["every namespace the service account may list"],
            "sees": SEES,
        }

    async def list_certificates(self, within_days: int | None, name: str | None, limit: int) -> dict[str, Any]:
        now = self._clock()
        certs = [_view(o, now)[1] for o in await self._objects()]
        return list_answer(certs, within_days=within_days, name=name, limit=limit)

    async def get_certificate(self, id: str) -> dict[str, Any]:
        """The certificate with what its renewal is waiting on, and its issuer."""
        namespace, name = split_id(id)
        now = self._clock()
        obj = await self._kube.get(CERTIFICATES, name, namespace)
        _, cert = _view(obj, now)
        chain = (await self._chain(namespace)).of(obj)
        attempts, signals = chain.attempts(now)
        issuer = await self._issuer(obj, namespace)
        if issuer is not None:
            attempts.append(issuer)
        notes = [
            f"cert-manager signals read in the attempts: {', '.join(signals)} (a reading of the text, not a verdict)"
        ]
        if not attempts:
            notes = ["no request, order or challenge is chained to it: nothing is in flight"]
        return {"certificate": cert, "renewal_attempts": attempts, "notes": notes}

    async def _issuer(self, obj: dict[str, Any], namespace: str) -> dict[str, Any] | None:
        ref = (obj.get("spec") or {}).get("issuerRef") or {}
        kind, issuer_name = str(ref.get("kind") or "Issuer"), str(ref.get("name") or "")
        if not issuer_name:
            return None
        if ref.get("group", "cert-manager.io") != "cert-manager.io" or kind not in ("ClusterIssuer", "Issuer"):
            return _attempt(
                "issuer", f"{kind}/{issuer_name}", "unknown", "an external issuer: its status is not read", []
            )
        try:
            if kind == "ClusterIssuer":
                view = issuer_view(await self._kube.get(CLUSTER_ISSUERS, issuer_name, None))
            else:
                view = issuer_view(await self._kube.get(ISSUERS, issuer_name, namespace))
        except SourceError as error:
            return _attempt("issuer", f"{kind}/{issuer_name}", "unknown", f"not readable: {error}", [])
        ready = view["ready"] or {}
        state = "ready" if ready.get("status") == "True" else "not_ready"
        detail = f"{view['type']} issuer" + (f" at {view['acme_server']}" if view.get("acme_server") else "")
        if ready.get("message"):
            detail += f": {ready['message']}"
        return _attempt("issuer", f"{kind}/{issuer_name}", state, detail, [])

    async def list_findings(
        self, within_days: int = DEFAULT_WINDOW, certificate_id: str | None = None
    ) -> dict[str, Any]:
        now = self._clock()
        objects = await self._objects()
        views = [(o, *_view(o, now)) for o in objects]
        needs_chain = any(
            raw["failed_issuance_attempts"] or raw["issuing"] or raw["renewal_overdue"] for _, raw, _ in views
        )
        chain = await self._chain(None) if needs_chain else Chain([], [], [])
        findings: list[dict[str, Any]] = []
        for obj, raw, cert in views:
            cid = cert["id"]
            findings.extend(window_findings(cert, within_days))
            days = cert.get("days_remaining")
            urgent = days is not None and days <= CRITICAL_DAYS
            attempts, signals = chain.of(obj).attempts(now) if needs_chain else ([], [])
            waiting = [
                f"{a['kind']} {a['name']} is {a['state']}" + (f": {a['detail']}" if a.get("detail") else "")
                for a in attempts[:4]
            ]
            if raw["failed_issuance_attempts"]:
                findings.append(
                    finding(
                        "renewal_failing",
                        "critical" if urgent or days is None else "warning",
                        cid,
                        f"cert-manager has failed to issue {cid} {raw['failed_issuance_attempts']} time(s)",
                        [
                            (
                                f"failedIssuanceAttempts {raw['failed_issuance_attempts']}, "
                                f"last failure {raw['last_failure_time']}"
                            ),
                            *waiting,
                        ],
                        signals,
                    )
                )
            elif raw["renewal_overdue"]:
                findings.append(
                    finding(
                        "renewal_overdue",
                        "critical" if urgent else "warning",
                        cid,
                        f"{cid} should have been renewed already",
                        [f"renewalTime {raw['renewal_time']} has passed; not_after {cert.get('not_after')}", *waiting],
                        signals,
                    )
                )
            if raw["not_after"] is None:
                ready = condition(obj, "Ready") or {}
                findings.append(
                    finding(
                        "not_issued",
                        "warning",
                        cid,
                        f"{cid} has no issued certificate yet",
                        [
                            (
                                f"Ready {ready.get('status', 'unknown')}: "
                                f"{ready.get('reason', '')} {ready.get('message', '')}"
                            ).strip(),
                            *waiting,
                        ],
                        signals,
                    )
                )
        return findings_answer(findings, within_days=within_days, certificate_id=certificate_id)
