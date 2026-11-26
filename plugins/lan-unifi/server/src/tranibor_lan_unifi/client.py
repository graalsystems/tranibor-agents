# SPDX-License-Identifier: Apache-2.0
"""Thin clients of the two official UniFi APIs. Neither is ever given a redirect to follow, and neither
key is ever logged or put in an error.

- `Controller`: the Network Integration API of a console, `<base_url>/proxy/network/integration/v1/...`,
  header `X-API-KEY`, pages `{offset, limit, count, totalCount, data}` (`limit` at most 200).
  Reference: the OpenAPI document of Network 11.0.97 published at https://developer.ui.com/network/.
  Through the official cloud connector (`console_id` given) the same paths are served by
  `https://api.ui.com/v1/connector/consoles/<id>/network/integration/...`, header `X-API-Key` (a Site Manager
  key); the connector proxies to the console's `http://127.0.0.1/proxy/<path>`. Its limits (Site Manager API
  v1.0.0): 100 requests per minute per console, 25 s per request, 10 MB per response, console firmware
  >= 5.0.3. They are kept under client-side: see `Throttle`, `CLOUD_TIMEOUT_S`, `CLOUD_BODY_MAX`.
- `SiteManager`: the cloud API at `https://api.ui.com/v1/...`, header `X-API-Key`, pages `{data, nextToken}`.
  Reference: https://developer.ui.com/site-manager/ (rate limited: 10,000 requests per minute for a stable
  version of an API key).
"""

from __future__ import annotations

import asyncio
import re
import ssl
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from .errors import Failure

TIMEOUT_S = 15.0
PAGE_MAX = 200  # the Integration API refuses a larger `limit`
#: The connector kills a proxied request after 25 s; give up first, so the error is ours and says why.
CLOUD_TIMEOUT_S = 20.0
#: The connector allows 100 requests per minute per console: stay under, with room for another caller of the key.
CLOUD_RATE_PER_MIN = 80
#: The longest a call waits for a free slot before it fails `rate_limited` rather than hold the agent's turn.
CLOUD_MAX_WAIT_S = 20.0
#: The connector refuses an upstream body above 10 MB.
CLOUD_BODY_MAX = 10 * 1024 * 1024
CONSOLE_ID = re.compile(r"^[A-Za-z0-9:_-]{8,128}$")


class Throttle:
    """A sliding window: at most `rate` requests in any `window` seconds, for one console.

    A call that would exceed it waits for the oldest request to leave the window, up to `max_wait`; beyond that
    it fails `rate_limited` without sending anything. The clock and the sleep are injectable for tests."""

    def __init__(
        self,
        rate: int = CLOUD_RATE_PER_MIN,
        window: float = 60.0,
        max_wait: float = CLOUD_MAX_WAIT_S,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rate, self.window, self.max_wait = rate, window, max_wait
        self._monotonic, self._sleep = monotonic, sleep
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._monotonic()
            while self._sent and now - self._sent[0] >= self.window:
                self._sent.popleft()
            if len(self._sent) >= self.rate:
                wait = self._sent[0] + self.window - now
                if wait > self.max_wait:
                    raise Failure(
                        "rate_limited",
                        f"the connector allows 100 requests per minute per console and {self.rate} are already "
                        f"used: retry in {int(wait) + 1} s (nothing was sent)",
                    )
                await self._sleep(wait)
                now = self._monotonic()
                while self._sent and now - self._sent[0] >= self.window:
                    self._sent.popleft()
            self._sent.append(now)


class _Upstream:
    label = "UniFi"
    key_header = "X-API-KEY"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
        timeout: float = TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self.transport = transport
        self.verify = verify
        self.timeout = timeout
        self.throttle: Throttle | None = None
        self.body_max: int | None = None

    def _client(self) -> httpx.AsyncClient:
        if self.transport is not None:
            return httpx.AsyncClient(transport=self.transport, timeout=self.timeout, follow_redirects=False)
        return httpx.AsyncClient(verify=self.verify, timeout=self.timeout, follow_redirects=False)

    async def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        headers = {self.key_header: self._key, "Accept": "application/json"}
        if self.throttle is not None:
            await self.throttle.acquire()
        try:
            async with self._client() as client:
                response = await client.request(
                    method, f"{self.base_url}{path}", params=params, json=body, headers=headers
                )
        except httpx.TimeoutException:
            raise Failure("timeout", f"{self.label} did not answer within {self.timeout:.0f} s") from None
        except httpx.HTTPError as error:
            raise Failure("upstream_error", f"{self.label} could not be reached ({type(error).__name__})") from None
        status = response.status_code
        if status == 413 or (self.body_max is not None and len(response.content) > self.body_max):
            raise Failure(
                "response_too_large", f"{self.label} refuses a response above 10 MB: ask for a smaller page", status
            )
        if status in (408, 504):
            raise Failure("timeout", f"{self.label} gave up waiting for the console (25 s at most)", status)
        if status in (200, 201):
            try:
                return response.json() if response.content else {}
            except ValueError:
                raise Failure("upstream_error", f"{self.label} answered something that is not JSON", status) from None
        if status == 401:
            raise Failure("unauthorized", f"{self.label} refused the API key", status)
        if status == 403:
            raise Failure("forbidden", f"the API key's rights do not cover this call on {self.label}", status)
        if status == 404:
            raise Failure("not_found", f"{self.label} has no such object", status)
        if status == 429:
            raise Failure("rate_limited", f"{self.label} is rate limiting this key: retry in a minute", status)
        if status in (400, 422):
            raise Failure("invalid_arguments", f"{self.label} refused the request as malformed", status)
        raise Failure("upstream_error", f"{self.label} answered HTTP {status}", status)

    async def get(self, path: str, params: Mapping[str, str | int] | None = None) -> dict[str, Any]:
        body = await self.request("GET", path, params)
        if not isinstance(body, dict):
            raise Failure("upstream_error", f"{self.label} answered an unexpected shape", 200)
        return body


class Controller(_Upstream):
    label = "the UniFi console"
    key_header = "X-API-KEY"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
        *,
        console_id: str | None = None,
        throttle: Throttle | None = None,
    ) -> None:
        """Local: `base_url` is the console's. Cloud: `base_url` is the Site Manager's (https://api.ui.com),
        `api_key` a Site Manager key and `console_id` the console's id (`GET /v1/hosts`): only the base and the
        header differ, every path after `/integration` is the same."""
        if console_id is None:
            super().__init__(f"{base_url.rstrip('/')}/proxy/network/integration", api_key, transport, verify)
            return
        if not CONSOLE_ID.fullmatch(console_id):
            raise Failure("invalid_arguments", "console_id is not a console id (see GET /v1/hosts of the Site Manager)")
        super().__init__(
            f"{base_url.rstrip('/')}/v1/connector/consoles/{quote(console_id, safe=':')}/network/integration",
            api_key,
            transport,
            verify,
            timeout=CLOUD_TIMEOUT_S,
        )
        self.label = "the UniFi cloud connector"
        self.key_header = "X-API-Key"
        self.throttle = throttle or Throttle()
        self.body_max = CLOUD_BODY_MAX

    async def collect(
        self, path: str, params: Mapping[str, str] | None = None, limit: int = 100
    ) -> tuple[list[dict[str, Any]], int]:
        """Up to `limit` items of a paginated list, and the total the console holds."""
        items: list[dict[str, Any]] = []
        total = 0
        offset = 0
        while len(items) < limit:
            page = await self.get(
                path, {**(params or {}), "offset": offset, "limit": min(PAGE_MAX, limit - len(items))}
            )
            data = page.get("data")
            rows = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
            items.extend(rows)
            raw_total = page.get("totalCount")
            total = raw_total if isinstance(raw_total, int) else len(items)
            offset += len(rows)
            if not rows or offset >= total:
                break
        return items[:limit], total


class SiteManager(_Upstream):
    label = "the UniFi Site Manager"
    key_header = "X-API-Key"

    async def collect(
        self, path: str, params: Mapping[str, str | int] | None = None, max_pages: int = 5, page_size: int = 100
    ) -> list[dict[str, Any]]:
        """The `data` of up to `max_pages` pages, following `nextToken`."""
        items: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(max_pages):
            query: dict[str, str | int] = {**(params or {}), "pageSize": page_size}
            if token:
                query["nextToken"] = token
            page = await self.get(path, query)
            data = page.get("data")
            items.extend(row for row in data if isinstance(row, dict)) if isinstance(data, list) else None
            raw = page.get("nextToken")
            token = raw if isinstance(raw, str) and raw else None
            if token is None:
                break
        return items
