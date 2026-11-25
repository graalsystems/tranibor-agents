# SPDX-License-Identifier: Apache-2.0
"""Certificate Transparency search through crt.sh's JSON output.

crt.sh publishes no API documentation: `https://crt.sh/?q=<identity>&output=json` is the de facto interface
its own web page links to, and the fields below (`id`, `issuer_name`, `common_name`, `name_value` with names
separated by newlines, `not_before`, `not_after` as UTC without a zone, `entry_timestamp`, `serial_number`)
are what it returns today. NOT confirmed in an official document: expect to adapt if crt.sh changes. It is
also rate limited and sometimes slow or answering 5xx: errors are reported as such, never retried in a loop.
A wildcard search (`%.example.com`, the `%` URL-encoded) returns the domain and every subdomain.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .config import Settings, normalise_domain
from .errors import Refused, SourceError
from .views import clip, iso, parse_time

TIMEOUT = httpx.Timeout(45.0, connect=10.0)
MAX_ENTRIES = 5000


class CtClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.ct_url,
            headers={"Accept": "application/json", "User-Agent": "tranibor-cert-watch"},
            timeout=TIMEOUT,
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def check_domain(self, domain: str) -> str:
        """Only a configured domain, or a subdomain of one, is searched: nothing else leaves the server."""
        try:
            wanted = normalise_domain(domain)
        except ValueError as error:
            raise Refused(str(error)) from error
        if not any(wanted == d or wanted.endswith("." + d) for d in self._settings.domains):
            raise Refused(f"{wanted!r} is not under a configured domain (CERT_WATCH_DOMAINS)")
        return wanted

    async def search(self, domain: str, include_subdomains: bool = True) -> list[dict[str, Any]]:
        wanted = self.check_domain(domain)
        query = f"%.{wanted}" if include_subdomains else wanted
        try:
            response = await self._http.get("/", params={"q": query, "output": "json"})
        except httpx.HTTPError as error:
            raise SourceError(f"the CT search did not answer: {type(error).__name__}") from error
        if response.status_code >= 400:
            raise SourceError(f"the CT search answered {response.status_code}", response.status_code)
        try:
            body = response.json()
        except ValueError as error:
            raise SourceError(
                "the CT search answered something that is not JSON (crt.sh sometimes returns HTML)"
            ) from error
        if not isinstance(body, list):
            raise SourceError("unexpected CT answer shape")
        return [entry for entry in body[:MAX_ENTRIES] if isinstance(entry, dict)]


def entry_view(entry: dict[str, Any], now: datetime) -> dict[str, Any]:
    """One CT entry: the names it covers, split from `name_value`, and its validity."""
    names = sorted({n.strip().lower() for n in str(entry.get("name_value") or "").splitlines() if n.strip()})
    not_after = parse_time(entry.get("not_after"))
    return {
        "ct_id": entry.get("id"),
        "names": [clip(n, 253) for n in names],
        "common_name": clip(entry.get("common_name"), 253) or None,
        "issuer": clip(entry.get("issuer_name"), 300),
        "serial": clip(entry.get("serial_number"), 80) or None,
        "not_before": iso(parse_time(entry.get("not_before"))),
        "not_after": iso(not_after),
        "expired": bool(not_after and not_after < now),
        "logged_at": iso(parse_time(entry.get("entry_timestamp"))),
    }


def covers(pattern: str, name: str) -> bool:
    """Does a certificate name (possibly `*.example.com`) cover `name`? A wildcard covers one label only."""
    pattern, name = pattern.lower(), name.lower()
    if pattern == name:
        return True
    if pattern.startswith("*."):
        head, _, rest = name.partition(".")
        return bool(head) and rest == pattern[2:]
    return False
