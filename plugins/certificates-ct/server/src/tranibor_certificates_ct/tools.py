# SPDX-License-Identifier: Apache-2.0
"""The four read tools of the profile, over Certificate Transparency. None of them writes anywhere.

A log lists what was issued, not what is served or renewed: every certificate here has `renewal.state: unknown`.
Its value is the question no other source can answer, which names have a certificate nobody else knows about;
the agent asks it by comparing this instance's names with the other instances' (the profile's job, not this
server's). An entry replaced by a later one that covers all its names is not reported as expiring: the log keeps
every certificate ever issued, and a renewed one leaves its predecessor in it.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .ct import CtClient, entry_view
from .errors import Refused, SourceError
from .model import (
    DEFAULT_WINDOW,
    certificate,
    covers,
    finding,
    findings_answer,
    list_answer,
    renewal,
    window_findings,
)
from .util import parse_time

Clock = Callable[[], datetime]

SEES = (
    "The publicly trusted certificates logged for the configured domains and their subdomains: names, issuer, "
    "dates. It does not know what is served, nor what renews a certificate, nor private CAs."
)
UNKNOWN_RENEWAL = "A transparency log lists what was issued, not what renews it: ask the instances that manage."
_ID = re.compile(r"^ct-(\d{1,20})$")


def _now() -> datetime:
    return datetime.now(UTC)


def _superseded_by(entry: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A later-expiring entry that covers every name of this one, if any."""
    after = entry.get("not_after") or ""
    for other in entries:
        if other is entry or (other.get("not_after") or "") <= after:
            continue
        if all(any(covers(o, n) for o in other["names"]) for n in entry["names"]):
            return other
    return None


def _view(entry: dict[str, Any], now: datetime) -> dict[str, Any]:
    return certificate(
        id=f"ct-{entry['ct_id']}",
        names=entry["names"],
        issuer=entry["issuer"],
        renewal=renewal(managed=False, state="unknown", detail=UNKNOWN_RENEWAL),
        now=now,
        not_after=parse_time(entry["not_after"]),
        not_before=parse_time(entry["not_before"]),
        subject=entry["common_name"],
        serial=entry["serial"],
        location=f"logged {entry['logged_at']}" if entry["logged_at"] else None,
    )


class CtTools:
    def __init__(self, settings: Settings, client: CtClient, clock: Clock = _now) -> None:
        self._settings, self._client, self._clock = settings, client, clock

    async def _search_all(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        """Every entry logged for the configured domains (one search each), and the domains that failed."""
        domains = self._settings.domains
        results = await asyncio.gather(*(self._client.search(d) for d in domains), return_exceptions=True)
        now = self._clock()
        entries: dict[Any, dict[str, Any]] = {}
        unavailable: list[dict[str, str]] = []
        for domain, result in zip(domains, results, strict=True):
            if isinstance(result, SourceError):
                unavailable.append({"id": domain, "reason": str(result)})
            elif isinstance(result, BaseException):
                raise result
            else:
                for raw in result:
                    view = entry_view(raw, now)
                    if view["ct_id"] is not None:
                        entries[view["ct_id"]] = view
        return list(entries.values()), unavailable

    async def get_provider(self) -> dict[str, Any]:
        return {
            "instance": self._settings.instance,
            "provider": "crt.sh",
            "kind": "log",
            "scope": [f"*.{d}" for d in self._settings.domains],
            "sees": SEES,
        }

    async def list_certificates(self, within_days: int | None, name: str | None, limit: int) -> dict[str, Any]:
        entries, unavailable = await self._search_all()
        now = self._clock()
        return list_answer(
            [_view(e, now) for e in entries], within_days=within_days, name=name, limit=limit, unavailable=unavailable
        )

    async def get_certificate(self, id: str) -> dict[str, Any]:
        matched = _ID.fullmatch(id.strip())
        if not matched:
            raise Refused(f"{id!r} is not a certificate of the configured domains (ids look like ct-1234567)")
        entries, unavailable = await self._search_all()
        found = next((e for e in entries if e["ct_id"] == int(matched.group(1))), None)
        if found is None:
            why = f" (could not search: {', '.join(u['id'] for u in unavailable)})" if unavailable else ""
            raise Refused(f"{id!r} names no certificate logged for the configured domains{why}")
        notes = [f"Certificate Transparency entry {found['ct_id']}"]
        newer = _superseded_by(found, entries)
        if newer is not None:
            notes.append(f"replaced by ct-{newer['ct_id']}, which covers all its names and expires later")
        return {"certificate": _view(found, self._clock()), "renewal_attempts": [], "notes": notes}

    async def list_findings(
        self, within_days: int = DEFAULT_WINDOW, certificate_id: str | None = None
    ) -> dict[str, Any]:
        entries, unavailable = await self._search_all()
        now = self._clock()
        findings: list[dict[str, Any]] = []
        for entry in entries:
            if _superseded_by(entry, entries) is not None:
                continue  # a renewed certificate leaves its predecessor in the log: not an alarm
            findings.extend(window_findings(_view(entry, now), within_days, expired_severity="info"))
        for gone in unavailable:
            findings.append(
                finding("unreachable", "warning", gone["id"], f"{gone['id']}: the log did not answer", [gone["reason"]])
            )
        return findings_answer(findings, within_days=within_days, certificate_id=certificate_id)
