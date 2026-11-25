# SPDX-License-Identifier: Apache-2.0
"""What the tools return: cert-manager objects reduced to the fields a reviewer reads.

Everything here is a pure function of an object the Kubernetes API returned and of `now`. Free text
(a condition's message, an ACME order's reason) was written by other systems, an ACME server's error body
included: it is clipped, stripped of control characters, and the tools' instructions tell the agent to read
it as data. Secrets are never read (the token cannot get them); a challenge's `token` and `key` are left out.

Field names follow the cert-manager API reference (https://cert-manager.io/docs/reference/api-docs/):
`Certificate.status.{notBefore,notAfter,renewalTime,revision,failedIssuanceAttempts,lastFailureTime,conditions}`,
`Order.status.{state,reason,url,authorizations,failureTime}`, `Challenge.{spec.type,spec.dnsName,spec.solver,
status.{state,reason,presented,processing,presentedAt}}`. The `Issuing` condition and the condition reasons are
those cert-manager sets; none is assumed beyond what the objects carry.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

MAX_TEXT = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

#: Heuristic reading of a condition or an ACME error, never a verdict: each signal names the text it matched.
SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "rate_limited",
        re.compile(r"rateLimited|too many (certificates|failed authorizations|new orders)|rate limit", re.I),
    ),
    ("http01_wrong_status", re.compile(r"wrong status code", re.I)),
    ("http01_wrong_content", re.compile(r"did not get expected response", re.I)),
    (
        "http01_unreachable",
        re.compile(r"connection refused|connection reset|timeout during connect|i/o timeout|no such host", re.I),
    ),
    ("self_check_failed", re.compile(r"failed to perform self check", re.I)),
    ("dns01_not_propagated", re.compile(r"dns-01 challenge propagation|not yet propagated", re.I)),
    (
        "dns_provider_denied",
        re.compile(r"AccessDenied|unauthorized|forbidden|\b40[13]\b|invalid credentials|authentication", re.I),
    ),
    ("caa_forbids", re.compile(r"CAA record", re.I)),
    ("dnssec", re.compile(r"DNSSEC", re.I)),
    ("acme_unauthorized", re.compile(r"acme:error:unauthorized", re.I)),
    ("acme_connection", re.compile(r"acme:error:connection", re.I)),
)


def clip(value: object, limit: int = MAX_TEXT) -> str:
    """Text from another system, made safe to carry: control characters out, length bounded."""
    text = _CONTROL.sub(" ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_time(value: object) -> datetime | None:
    """An RFC 3339 time (Kubernetes) or a naive UTC one (crt.sh), as an aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def days_until(moment: datetime | None, now: datetime) -> int | None:
    """Whole days from `now` to `moment`, negative once past (floor: 0 means less than a day left)."""
    return None if moment is None else math.floor((moment - now).total_seconds() / 86400)


def signals_of(*texts: object) -> list[str]:
    joined = " ".join(str(t) for t in texts if t)
    return [name for name, pattern in SIGNALS if pattern.search(joined)]


def _meta(obj: dict[str, Any]) -> dict[str, Any]:
    meta = obj.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _section(obj: dict[str, Any], key: str) -> dict[str, Any]:
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def name_of(obj: dict[str, Any]) -> str:
    return str(_meta(obj).get("name", ""))


def namespace_of(obj: dict[str, Any]) -> str | None:
    value = _meta(obj).get("namespace")
    return str(value) if value else None


def owners(obj: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    refs = _meta(obj).get("ownerReferences")
    return [r for r in refs if isinstance(r, dict) and r.get("kind") == kind] if isinstance(refs, list) else []


def conditions_of(obj: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _section(obj, "status").get("conditions")
    found = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            found.append(
                {
                    "type": clip(item.get("type"), 80),
                    "status": clip(item.get("status"), 20),
                    "reason": clip(item.get("reason"), 120),
                    "message": clip(item.get("message")),
                    "last_transition": clip(item.get("lastTransitionTime"), 40) or None,
                }
            )
    return found


def condition(obj: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next((c for c in conditions_of(obj) if c["type"] == kind), None)


def _issuer_ref(spec: dict[str, Any]) -> dict[str, str]:
    ref = _section(spec, "issuerRef")
    return {
        "name": clip(ref.get("name"), 253),
        "kind": clip(ref.get("kind") or "Issuer", 40),
        "group": clip(ref.get("group") or "cert-manager.io", 80),
    }


def certificate_view(obj: dict[str, Any], now: datetime) -> dict[str, Any]:
    spec, status = _section(obj, "spec"), _section(obj, "status")
    not_after = parse_time(status.get("notAfter"))
    renewal = parse_time(status.get("renewalTime"))
    ready, issuing = condition(obj, "Ready"), condition(obj, "Issuing")
    return {
        "namespace": namespace_of(obj),
        "name": name_of(obj),
        "secret_name": clip(spec.get("secretName"), 253),
        "common_name": clip(spec.get("commonName"), 253) or None,
        "dns_names": [clip(n, 253) for n in spec.get("dnsNames") or []],
        "ip_addresses": [clip(n, 64) for n in spec.get("ipAddresses") or []],
        "issuer": _issuer_ref(spec),
        "ready": {k: ready[k] for k in ("status", "reason", "message")} if ready else None,
        "issuing": bool(issuing and issuing["status"] == "True"),
        "not_before": iso(parse_time(status.get("notBefore"))),
        "not_after": iso(not_after),
        "days_remaining": days_until(not_after, now),
        "renewal_time": iso(renewal),
        "renewal_overdue": bool(
            renewal and not_after and renewal < now and not_after > now and not _recent(renewal, now)
        ),
        "revision": status.get("revision"),
        "failed_issuance_attempts": int(status.get("failedIssuanceAttempts") or 0),
        "last_failure_time": clip(status.get("lastFailureTime"), 40) or None,
        "duration": clip(spec.get("duration"), 40) or None,
        "renew_before": clip(spec.get("renewBefore"), 40) or None,
    }


def _recent(moment: datetime, now: datetime) -> bool:
    """Within the hour after `renewalTime`: cert-manager has had no time to react yet, so not overdue."""
    return (now - moment).total_seconds() < 3600


def request_view(obj: dict[str, Any]) -> dict[str, Any]:
    annotations = _meta(obj).get("annotations") or {}
    status = _section(obj, "status")
    kinds = {c["type"]: c for c in conditions_of(obj)}
    return {
        "name": name_of(obj),
        "namespace": namespace_of(obj),
        "revision": annotations.get("cert-manager.io/certificate-revision"),
        "issuer": _issuer_ref(_section(obj, "spec")),
        "created": clip(_meta(obj).get("creationTimestamp"), 40) or None,
        "approved": _flag(kinds.get("Approved")),
        "denied": _flag(kinds.get("Denied")),
        "ready": kinds.get("Ready"),
        "invalid_request": _flag(kinds.get("InvalidRequest")),
        "failure_time": clip(status.get("failureTime"), 40) or None,
        "conditions": list(kinds.values()),
        "signals": signals_of(*(c["message"] for c in kinds.values())),
    }


def _flag(cond: dict[str, Any] | None) -> bool | None:
    return None if cond is None else cond["status"] == "True"


def order_view(obj: dict[str, Any]) -> dict[str, Any]:
    spec, status = _section(obj, "spec"), _section(obj, "status")
    authorizations = []
    for auth in status.get("authorizations") or []:
        if isinstance(auth, dict):
            identifier = clip(auth.get("identifier"), 253)
            authorizations.append(
                {
                    "identifier": identifier,
                    "wildcard": bool(auth.get("wildcard")),
                    "initial_state": clip(auth.get("initialState"), 40),
                    "challenge_types": [
                        clip(c.get("type"), 20) for c in auth.get("challenges") or [] if isinstance(c, dict)
                    ],
                }
            )
    return {
        "name": name_of(obj),
        "namespace": namespace_of(obj),
        "dns_names": [clip(n, 253) for n in spec.get("dnsNames") or []],
        "issuer": _issuer_ref(spec),
        "state": clip(status.get("state"), 40),
        "reason": clip(status.get("reason")),
        "url": clip(status.get("url"), 300) or None,
        "authorizations": authorizations,
        "failure_time": clip(status.get("failureTime"), 40) or None,
        "created": clip(_meta(obj).get("creationTimestamp"), 40) or None,
        "signals": signals_of(status.get("reason")),
    }


def _solver(spec: dict[str, Any]) -> dict[str, Any]:
    """Which solver answers the challenge, and nothing of its credentials (only the provider's name)."""
    solver = _section(spec, "solver")
    http01, dns01 = _section(solver, "http01"), _section(solver, "dns01")
    if http01:
        ingress = _section(http01, "ingress")
        return {
            "kind": "http01",
            "mechanism": "gatewayHTTPRoute" if "gatewayHTTPRoute" in http01 else "ingress",
            "ingress_class": clip(ingress.get("ingressClassName") or ingress.get("class"), 100) or None,
            "ingress_name": clip(ingress.get("name"), 253) or None,
            "service_type": clip(ingress.get("serviceType"), 40) or None,
        }
    if dns01:
        providers = sorted(k for k in dns01 if k not in ("cnameStrategy",))
        webhook = _section(dns01, "webhook")
        if "webhook" in providers and webhook.get("solverName"):
            providers = [f"webhook:{clip(webhook.get('solverName'), 100)}"]
        return {"kind": "dns01", "providers": [clip(p, 100) for p in providers]}
    return {"kind": "unknown"}


def challenge_view(obj: dict[str, Any], now: datetime) -> dict[str, Any]:
    spec, status = _section(obj, "spec"), _section(obj, "status")
    created = parse_time(_meta(obj).get("creationTimestamp"))
    return {
        "name": name_of(obj),
        "namespace": namespace_of(obj),
        "type": clip(spec.get("type"), 20),
        "dns_name": clip(spec.get("dnsName"), 253),
        "wildcard": bool(spec.get("wildcard")),
        "state": clip(status.get("state"), 40),
        "reason": clip(status.get("reason")),
        "presented": bool(status.get("presented")),
        "processing": bool(status.get("processing")),
        "presented_at": clip(status.get("presentedAt"), 40) or None,
        "solver": _solver(spec),
        "issuer": _issuer_ref(spec),
        "age_minutes": None if created is None else max(0, int((now - created).total_seconds() // 60)),
        "created": iso(created),
        "signals": signals_of(status.get("reason")),
    }


#: The `spec` keys that name an issuer's type (https://cert-manager.io/docs/configuration/).
ISSUER_TYPES = ("acme", "ca", "vault", "selfSigned", "venafi")


def issuer_view(obj: dict[str, Any]) -> dict[str, Any]:
    spec = _section(obj, "spec")
    kind = next((t for t in ISSUER_TYPES if t in spec), "other")
    view: dict[str, Any] = {
        "kind": clip(obj.get("kind"), 40),
        "name": name_of(obj),
        "namespace": namespace_of(obj),
        "type": kind,
        "ready": condition(obj, "Ready"),
    }
    if kind == "acme":
        view["acme_server"] = clip(_section(spec, "acme").get("server"), 300)
    return view


def references(owner: dict[str, Any], kind: str, obj: dict[str, Any]) -> bool:
    """True when `obj` is owned by `owner` (matched on the owner reference's uid when both have one, else its name)."""
    for ref in owners(obj, kind):
        uid, owner_uid = ref.get("uid"), _meta(owner).get("uid")
        if uid and owner_uid:
            if uid == owner_uid:
                return True
        elif ref.get("name") == name_of(owner):
            return True
    return False
