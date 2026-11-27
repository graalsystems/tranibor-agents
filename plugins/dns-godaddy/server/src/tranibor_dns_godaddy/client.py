# SPDX-License-Identifier: Apache-2.0
"""A small client of GoDaddy's Domains API v3 (developer.godaddy.com).

Authentication is `Authorization: Bearer <personal access token>`: the documentation says the older
`sso-key <key>:<secret>` scheme is deprecated for Domains and does not work with v3. Nothing is signed.

Rate limit: the v3 overview states 60 requests per minute per credential (the separate rate-limits page says
the limits are not fixed). On a 429 the client waits for `Retry-After` (seconds; capped at `MAX_WAIT`) and
retries, at most `RETRIES` times; it never loops longer. A missing or odd `Retry-After` waits `DEFAULT_WAIT`.

NOT confirmed against a real call: the exact JSON of GoDaddy's error bodies (`{"code": ..., "message": ...}`
is what the documentation shows), the code returned for an account that is not eligible for domain
management (the documentation says such an account gets a 403 with a distinct code), and the OTE host.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from .config import Settings
from .errors import GoDaddyError

log = structlog.get_logger("tranibor_dns_godaddy")

MAX_ERROR_TEXT = 300
RETRIES = 2
MAX_WAIT = 30.0
DEFAULT_WAIT = 5.0


class GoDaddyClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.base = settings.base_url
        self._sleep = sleep
        self._http = httpx.AsyncClient(transport=transport, timeout=30.0, follow_redirects=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        """One call; the decoded JSON answer, or `None` for an empty one."""
        headers = {
            "Authorization": f"Bearer {self.settings.token.get_secret_value()}",
            "Accept": "application/json",
        }
        for attempt in range(RETRIES + 1):
            try:
                response = await self._http.request(
                    method, f"{self.base}{path}", params=query, headers=headers, json=body
                )
            except httpx.HTTPError as error:
                raise GoDaddyError(0, f"GoDaddy could not be reached ({type(error).__name__})") from error
            if response.is_success:
                return response.json() if response.content.strip() else None
            if response.status_code == 429 and attempt < RETRIES:
                wait = _retry_after(response)
                log.info("rate_limited", wait=wait)
                await self._sleep(wait)
                continue
            raise GoDaddyError(response.status_code, _explain(response))
        raise GoDaddyError(0, "unreachable")  # pragma: no cover


def _retry_after(response: httpx.Response) -> float:
    try:
        return min(max(float(response.headers.get("Retry-After", "")), 0.0), MAX_WAIT)
    except ValueError:
        return DEFAULT_WAIT


def _detail(response: httpx.Response) -> tuple[str, str]:
    """GoDaddy's error code and text, shortened; third-party text."""
    try:
        decoded = response.json()
    except ValueError:
        return "", " ".join(response.text.split())[:MAX_ERROR_TEXT]
    if not isinstance(decoded, dict):
        return "", " ".join(str(decoded).split())[:MAX_ERROR_TEXT]
    return str(decoded.get("code", ""))[:80], " ".join(str(decoded.get("message", "")).split())[:MAX_ERROR_TEXT]


def _explain(response: httpx.Response) -> str:
    status = response.status_code
    code, message = _detail(response)
    meaning = {
        400: "the request was refused as invalid",
        401: "the token was refused (wrong, expired or revoked)",
        403: "the token lacks a scope (domains.domain:read, domains.dns:update) for this call, or the GoDaddy "
        "account is not eligible for domain management through the API",
        404: "not found",
        409: "conflict (a record that cannot be changed, such as SOA or NS, or one that already exists)",
        422: "the record was refused as invalid",
        429: "rate limited",
    }.get(status, "GoDaddy failed" if status >= 500 else "refused")
    detail = ", ".join(part for part in (code, message) if part)
    return f"GoDaddy answered {status}: {meaning}" + (f" ({detail})" if detail else "")
