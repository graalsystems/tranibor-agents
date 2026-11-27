# SPDX-License-Identifier: Apache-2.0
"""A small client of the OVHcloud API v1, signed the way the vendor documents it.

Every authenticated request carries four headers:

- `X-Ovh-Application`: the application key;
- `X-Ovh-Consumer`: the consumer key;
- `X-Ovh-Timestamp`: the OVHcloud server's clock, not ours: `GET /auth/time` (no authentication) gives it,
  and the offset from our own clock is kept;
- `X-Ovh-Signature`: `"$1$" + sha1_hex(application_secret + "+" + consumer_key + "+" + METHOD + "+" + URL
  + "+" + BODY + "+" + TIMESTAMP)`, URL being the full URL requested (query string included), BODY the
  request body exactly as sent (empty for a GET).

Sources: help.ovhcloud.com "first steps with the OVHcloud APIs" (the formula), the public schema
https://eu.api.ovh.com/1.0/auth.json (`/auth/time`, `/auth/credential`) and
https://eu.api.ovh.com/1.0/domain.json (the `/domain/zone/...` routes).

NOT confirmed in the documentation: the exact wording of the error OVHcloud answers for a skewed clock. The
client therefore re-reads `/auth/time` and retries ONCE when an answer of 400 or 401 mentions a signature or
a timestamp; any other error is returned as it is.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from .config import Settings
from .errors import OvhError

log = structlog.get_logger("tranibor_dns_ovh")

MAX_ERROR_TEXT = 300
SKEW_HINT = re.compile(r"signature|timestamp", re.IGNORECASE)


def signature(secret: str, consumer: str, method: str, url: str, body: str, timestamp: int) -> str:
    """The `X-Ovh-Signature` value."""
    material = "+".join((secret, consumer, method, url, body, str(timestamp)))
    return "$1$" + hashlib.sha1(material.encode(), usedforsecurity=False).hexdigest()


class OvhClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.base = settings.base_url
        self._clock = clock
        self._delta: int | None = None
        self._http = httpx.AsyncClient(transport=transport, timeout=30.0, follow_redirects=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _sync_clock(self) -> int:
        try:
            response = await self._http.get(f"{self.base}/auth/time")
            self._delta = int(response.text.strip()) - int(self._clock())
        except (httpx.HTTPError, ValueError) as error:
            raise OvhError(0, "OVHcloud's clock (/auth/time) could not be read") from error
        return self._delta

    async def request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """One signed call; the decoded JSON answer, or `None` for an empty one."""
        url = f"{self.base}{path}" + (f"?{urlencode(query)}" if query else "")
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        for attempt in (1, 2):
            delta = self._delta if self._delta is not None else await self._sync_clock()
            timestamp = int(self._clock()) + delta
            headers = {
                "X-Ovh-Application": self.settings.application_key,
                "X-Ovh-Consumer": self.settings.consumer_key.get_secret_value(),
                "X-Ovh-Timestamp": str(timestamp),
                "X-Ovh-Signature": signature(
                    self.settings.application_secret.get_secret_value(),
                    self.settings.consumer_key.get_secret_value(),
                    method,
                    url,
                    payload,
                    timestamp,
                ),
                "Accept": "application/json",
            }
            if body is not None:
                headers["Content-Type"] = "application/json"
            try:
                response = await self._http.request(method, url, headers=headers, content=payload or None)
            except httpx.HTTPError as error:
                raise OvhError(0, f"OVHcloud could not be reached ({type(error).__name__})") from error
            if response.is_success:
                return response.json() if response.content.strip() else None
            message = _message(response)
            if attempt == 1 and response.status_code in (400, 401) and SKEW_HINT.search(message):
                log.info("clock_resync", status=response.status_code)
                self._delta = None
                continue
            raise OvhError(response.status_code, _explain(response.status_code, message))
        raise OvhError(0, "unreachable")  # pragma: no cover


def _message(response: httpx.Response) -> str:
    """OVHcloud's error text (`{"class": ..., "message": ...}`), shortened; third-party text."""
    try:
        decoded = response.json()
        text = str(decoded.get("message", "")) if isinstance(decoded, dict) else str(decoded)
    except ValueError:
        text = response.text
    return " ".join(text.split())[:MAX_ERROR_TEXT]


def _explain(status: int, message: str) -> str:
    meaning = {
        400: "the request was refused as invalid",
        401: "the credentials were refused (application key, secret or consumer key wrong, expired or revoked)",
        403: "the consumer key's access rules do not allow this call",
        404: "not found",
        409: "conflict",
        429: "rate limited",
    }.get(status, "OVHcloud failed" if status >= 500 else "refused")
    return f"OVHcloud answered {status}: {meaning}" + (f" ({message})" if message else "")
