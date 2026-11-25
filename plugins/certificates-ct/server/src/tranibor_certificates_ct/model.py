# SPDX-License-Identifier: Apache-2.0
"""The profile's shapes (`certificates/certificates@1`), built in one place: a certificate, its renewal, a finding,
and the list and findings answers. A time the source does not know is left out, never null."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .util import days_until, iso

MAX_LIST = 200
DEFAULT_WINDOW = 30
#: Within this many days, an expiry is critical rather than a warning.
CRITICAL_DAYS = 7
_RANK = {"critical": 0, "warning": 1, "info": 2}


def renewal(
    *, managed: bool, state: str, manager: str = "", detail: str = "", next_renewal_at: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"managed": managed, "state": state}
    if manager:
        out["manager"] = manager
    if detail:
        out["detail"] = detail
    if next_renewal_at:
        out["next_renewal_at"] = next_renewal_at
    return out


def certificate(
    *,
    id: str,
    names: list[str],
    issuer: str,
    renewal: dict[str, Any],
    now: datetime,
    not_after: datetime | None = None,
    not_before: datetime | None = None,
    **optional: Any,
) -> dict[str, Any]:
    """A certificate of the profile. `optional` keys (subject, serial, trusted, location...) are kept when set."""
    out: dict[str, Any] = {"id": id, "names": names, "issuer": issuer, "renewal": renewal}
    if not_before:
        out["not_before"] = iso(not_before)
    if not_after:
        out["not_after"] = iso(not_after)
        out["days_remaining"] = days_until(not_after, now)
        out["expired"] = not_after < now
    out.update({k: v for k, v in optional.items() if v is not None and v != ""})
    return out


def finding(
    kind: str, severity: str, cert: str, summary: str, evidence: list[str], signals: list[str] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": f"{kind}:{cert}",
        "kind": kind,
        "severity": severity,
        "certificate": cert,
        "summary": summary,
        "evidence": evidence,
    }
    if signals:
        out["signals"] = signals
    return out


def window_findings(cert: dict[str, Any], within_days: int, expired_severity: str = "critical") -> list[dict[str, Any]]:
    """Expired, or expiring within the window: the findings every source can raise from the dates alone."""
    days = cert.get("days_remaining")
    if days is None:
        return []
    when = f"not_after {cert['not_after']}, {days} days remaining"
    if cert.get("expired"):
        return [finding("expired", expired_severity, cert["id"], f"{cert['id']} has expired", [when])]
    if days <= within_days:
        severity = "critical" if days <= CRITICAL_DAYS else "warning"
        return [finding("expires_soon", severity, cert["id"], f"{cert['id']} expires in {days} days", [when])]
    return []


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(findings, key=lambda f: (_RANK[f["severity"]], f["certificate"], f["kind"]))


def sort_certificates(certs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Not issued yet first, then soonest `not_after`; the id breaks ties."""
    return sorted(certs, key=lambda c: ("not_after" in c, c.get("not_after", ""), c["id"]))


def covers(pattern: str, name: str) -> bool:
    """Does a certificate name (possibly `*.example.com`) cover `name`? A wildcard covers one label only."""
    pattern, name = pattern.lower(), name.lower()
    if pattern == name:
        return True
    if pattern.startswith("*."):
        head, _, rest = name.partition(".")
        return bool(head) and rest == pattern[2:]
    return False


def list_answer(
    certs: list[dict[str, Any]],
    *,
    within_days: int | None,
    name: str | None,
    limit: int,
    unavailable: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    kept = certs
    if within_days is not None:
        kept = [c for c in kept if c.get("days_remaining") is None or c["days_remaining"] <= within_days]
    if name:
        wanted = name.strip().lower()
        kept = [c for c in kept if any(covers(n, wanted) or n == wanted for n in c["names"])]
    kept = sort_certificates(kept)
    shown = kept[: max(1, min(limit, MAX_LIST))]
    out: dict[str, Any] = {"total": len(kept), "truncated": len(shown) < len(kept), "certificates": shown}
    if unavailable:
        out["unavailable"] = unavailable
    return out


def findings_answer(findings: list[dict[str, Any]], *, within_days: int, certificate_id: str | None) -> dict[str, Any]:
    kept = [f for f in findings if certificate_id is None or f["certificate"] == certificate_id]
    ordered = sort_findings(kept)
    return {"within_days": within_days, "total": len(ordered), "findings": ordered}
