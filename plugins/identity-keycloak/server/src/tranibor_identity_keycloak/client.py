# SPDX-License-Identifier: Apache-2.0
"""The Keycloak Admin REST API client.

Authentication: OAuth 2.0 client credentials of a confidential client whose service account holds the
`realm-management` roles it needs, in each realm it administers
(https://www.keycloak.org/docs/latest/server_admin/#_service_accounts).
The token comes from the realm's token endpoint, `POST {base}/realms/{realm}/protocol/openid-connect/token`
with `grant_type=client_credentials` (the client's own realm: `token_realm`, else the first administered realm), is
cached until shortly before it expires, and is renewed once on a 401. The admin API is
`{base}/admin/realms/{realm}/...` (https://www.keycloak.org/docs-api/latest/rest-api/index.html).

The client follows no redirect, sends the token only to `base_url`, and never puts a body Keycloak returned
into an error message.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from .config import Settings
from .errors import Failure

log = structlog.get_logger("tranibor_identity_keycloak")

#: A token is renewed this many seconds before Keycloak says it expires.
TOKEN_MARGIN_S = 20
TIMEOUT_S = 30.0


@dataclass
class _Token:
    value: str
    expires_at: float


class KeycloakClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(transport=transport, timeout=TIMEOUT_S, follow_redirects=False)
        self._token: _Token | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _access_token(self, renew: bool = False) -> str:
        async with self._lock:
            now = time.monotonic()
            if not renew and self._token is not None and now < self._token.expires_at:
                return self._token.value
            s = self._settings
            try:
                response = await self._http.post(
                    f"{s.root_url}/realms/{s.issuing_realm}/protocol/openid-connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": s.client_id,
                        "client_secret": s.client_secret.get_secret_value(),
                    },
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as error:
                raise Failure(
                    "upstream_error", f"Keycloak's token endpoint is unreachable ({type(error).__name__})"
                ) from None
            if response.status_code in (400, 401, 403):
                raise Failure(
                    "auth_failed",
                    "Keycloak refused the client credentials: check the client id and secret, that the client is "
                    "enabled, confidential, has service accounts enabled, and lives in the realm named by "
                    "token_realm (or the first realm)",
                    response.status_code,
                )
            if response.status_code != 200:
                raise Failure(
                    "upstream_error", f"Keycloak's token endpoint answered {response.status_code}", response.status_code
                )
            try:
                body = response.json()
                value = str(body["access_token"])
                expires_in = int(body.get("expires_in", 60))
            except (ValueError, KeyError, TypeError):
                raise Failure("upstream_error", "Keycloak's token endpoint answered no access token") from None
            self._token = _Token(value, now + max(expires_in - TOKEN_MARGIN_S, 0))
            log.info("token_obtained", expires_in=expires_in)
            return value

    async def request(
        self,
        method: str,
        realm: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """One call to `/admin/realms/{realm}{path}`; a 2xx response, else a typed `Failure`.

        `realm` must already have passed the allow-list (`Tools._dir`); the client refuses a realm it was not
        configured with as a second line of defence, before any request."""
        s = self._settings
        if realm not in s.realms:
            raise Failure("invalid_arguments", f"realm {realm!r} is not in this server's allow-list")
        url = f"{s.root_url}/admin/realms/{realm}{path}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        response: httpx.Response | None = None
        for attempt in range(2):
            token = await self._access_token(renew=attempt == 1)
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=clean,
                    json=json,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except httpx.HTTPError as error:
                raise Failure("upstream_error", f"Keycloak is unreachable ({type(error).__name__})") from None
            if response.status_code == 401 and attempt == 0:
                log.info("token_rejected_renewing")
                continue
            break
        assert response is not None
        status = response.status_code
        if 200 <= status < 300:
            return response
        if status == 401:
            raise Failure("auth_failed", "Keycloak refused the renewed token", status)
        if status == 403:
            raise Failure(
                "forbidden",
                f"Keycloak refused {method} {_shape(path)}: the service account lacks a realm-management role "
                "(view-users, view-realm, view-clients, view-events, query-users, query-groups; manage-users for "
                "writes)",
                status,
            )
        if status == 404:
            raise Failure("not_found", f"Keycloak has no {_shape(path)}", status)
        if status == 409:
            raise Failure("conflict", f"Keycloak refused {method} {_shape(path)}: a conflicting object exists", status)
        if status == 429:
            raise Failure("rate_limited", "Keycloak is rate limiting this client", status)
        raise Failure("upstream_error", f"Keycloak answered {status} to {method} {_shape(path)}", status)

    async def get(self, realm: str, path: str, **params: Any) -> Any:
        return (await self.request("GET", realm, path, params=params)).json()

    async def get_page(
        self, realm: str, path: str, *, limit: int, page: int = 100, **params: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        """Up to `limit` objects of a `first`/`max` paginated list, and whether the list went on."""
        found: list[dict[str, Any]] = []
        first = 0
        while len(found) <= limit:
            size = min(page, limit - len(found) + 1)
            rows = await self.get(realm, path, first=first, max=size, **params)
            if not isinstance(rows, list):
                raise Failure("upstream_error", f"Keycloak answered a non-list to GET {_shape(path)}")
            found.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < size:
                return found, False
            first += size
        return found[:limit], len(found) > limit


def _shape(path: str) -> str:
    """The path with identifiers left out, for messages and logs."""
    parts = []
    for part in path.split("/"):
        parts.append("{id}" if len(part) == 36 and part.count("-") == 4 else part)
    return "/".join(parts)
