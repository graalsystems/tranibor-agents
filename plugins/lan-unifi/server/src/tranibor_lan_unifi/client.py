# SPDX-License-Identifier: Apache-2.0
"""Thin clients of the two official UniFi APIs. Neither is ever given a redirect to follow, and neither
key is ever logged or put in an error.

- `Controller`: the Network Integration API of a console, `<base_url>/proxy/network/integration/v1/...`,
  header `X-API-KEY`, pages `{offset, limit, count, totalCount, data}` (`limit` at most 200).
  Reference: the OpenAPI document of Network 10.1.68 published at https://developer.ui.com/network/.
- `SiteManager`: the cloud API at `https://api.ui.com/v1/...`, header `X-API-Key`, pages `{data, nextToken}`.
  Reference: https://developer.ui.com/site-manager/ (rate limited: 10,000 requests per minute for a stable
  version of an API key).
"""

from __future__ import annotations

import ssl
from collections.abc import Mapping
from typing import Any

import httpx

from .errors import Failure

TIMEOUT_S = 15.0
PAGE_MAX = 200  # the Integration API refuses a larger `limit`


class _Upstream:
    label = "UniFi"
    key_header = "X-API-KEY"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self.transport = transport
        self.verify = verify

    def _client(self) -> httpx.AsyncClient:
        if self.transport is not None:
            return httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT_S, follow_redirects=False)
        return httpx.AsyncClient(verify=self.verify, timeout=TIMEOUT_S, follow_redirects=False)

    async def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        headers = {self.key_header: self._key, "Accept": "application/json"}
        try:
            async with self._client() as client:
                response = await client.request(
                    method, f"{self.base_url}{path}", params=params, json=body, headers=headers
                )
        except httpx.HTTPError as error:
            raise Failure("upstream_error", f"{self.label} could not be reached ({type(error).__name__})") from None
        status = response.status_code
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
            raise Failure("rate_limited", f"{self.label} is rate limiting this key", status)
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
    ) -> None:
        super().__init__(f"{base_url.rstrip('/')}/proxy/network/integration", api_key, transport, verify)

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
