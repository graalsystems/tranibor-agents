# SPDX-License-Identifier: Apache-2.0
"""The four read tools of the profile, over live TLS endpoints. None of them writes anywhere."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .config import Settings, format_host_port
from .errors import SourceError
from .model import (
    DEFAULT_WINDOW,
    certificate,
    finding,
    findings_answer,
    list_answer,
    renewal,
    window_findings,
)
from .probe import Prober, check_target

Clock = Callable[[], datetime]

SEES = (
    "What a client connecting to each configured host is served: the leaf, its names, its chain, whether the "
    "system roots trust it. It cannot tell what renews the certificate."
)
UNKNOWN_RENEWAL = "A TLS probe sees what clients are served, not what renews it: ask the instances that manage."


def _now() -> datetime:
    return datetime.now(UTC)


def _view(probe: dict[str, Any], now: datetime) -> dict[str, Any]:
    return certificate(
        id=probe["endpoint"],
        names=list(probe["names"]),
        issuer=probe["issuer"],
        renewal=renewal(managed=False, state="unknown", detail=UNKNOWN_RENEWAL),
        now=now,
        not_after=probe["not_after"],
        not_before=probe["not_before"],
        subject=probe["subject"],
        serial=probe["serial"],
        trusted=probe["trusted"],
        chain_complete=probe["chain_complete"],
        hostname_matches=probe["hostname_matches"],
        location=probe["endpoint"],
    )


class ProbeTools:
    def __init__(self, settings: Settings, prober: Prober, clock: Clock = _now) -> None:
        self._settings, self._prober, self._clock = settings, prober, clock

    async def _probe_all(self) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, str]]]:
        """Every configured endpoint, probed together: (probe, certificate view) pairs, and those that failed."""
        now = self._clock()
        hosts = self._settings.hosts
        results = await asyncio.gather(*(self._prober.probe(h, p) for h, p in hosts), return_exceptions=True)
        found: list[tuple[dict[str, Any], dict[str, Any]]] = []
        unavailable: list[dict[str, str]] = []
        for (host, port), result in zip(hosts, results, strict=True):
            if isinstance(result, SourceError):
                unavailable.append({"id": format_host_port(host, port), "reason": str(result)})
            elif isinstance(result, BaseException):
                raise result
            else:
                found.append((result, _view(result, now)))
        return found, unavailable

    async def get_provider(self) -> dict[str, Any]:
        return {
            "instance": self._settings.instance,
            "provider": "tls-probe",
            "kind": "endpoint",
            "scope": [format_host_port(h, p) for h, p in self._settings.hosts],
            "sees": SEES,
        }

    async def list_certificates(self, within_days: int | None, name: str | None, limit: int) -> dict[str, Any]:
        found, unavailable = await self._probe_all()
        return list_answer(
            [v for _, v in found], within_days=within_days, name=name, limit=limit, unavailable=unavailable
        )

    async def get_certificate(self, id: str) -> dict[str, Any]:
        host, port = check_target(self._settings, id)
        probe = await self._prober.probe(host, port)
        notes = [
            f"served over {probe['tls_version']}, chain of {probe['chain_length']} certificate(s)",
            f"sha256 fingerprint {probe['sha256']}",
        ]
        if probe.get("trust_error"):
            notes.append(f"the system roots refused it: {probe['trust_error']}")
        return {"certificate": _view(probe, self._clock()), "renewal_attempts": [], "notes": notes}

    async def list_findings(
        self, within_days: int = DEFAULT_WINDOW, certificate_id: str | None = None
    ) -> dict[str, Any]:
        found, unavailable = await self._probe_all()
        findings: list[dict[str, Any]] = []
        for probe, view in found:
            cid = view["id"]
            findings.extend(window_findings(view, within_days))
            if not probe["trusted"]:
                findings.append(
                    finding(
                        "not_trusted",
                        "critical",
                        cid,
                        f"{cid} is not trusted by the system roots",
                        [f"verification failed: {probe['trust_error']}", f"issuer {probe['issuer']}"],
                    )
                )
            if not probe["chain_complete"]:
                findings.append(
                    finding(
                        "chain_incomplete",
                        "warning",
                        cid,
                        f"{cid} serves an incomplete chain",
                        [f"{probe['chain_length']} certificate(s) served: the leaf, without its intermediate"],
                    )
                )
            if not probe["hostname_matches"]:
                findings.append(
                    finding(
                        "hostname_mismatch",
                        "critical",
                        cid,
                        f"{cid} serves a certificate that does not cover its name",
                        [f"names served: {', '.join(probe['san_dns'])}"],
                    )
                )
        for gone in unavailable:
            findings.append(
                finding("unreachable", "warning", gone["id"], f"{gone['id']} did not answer", [gone["reason"]])
            )
        return findings_answer(findings, within_days=within_days, certificate_id=certificate_id)
