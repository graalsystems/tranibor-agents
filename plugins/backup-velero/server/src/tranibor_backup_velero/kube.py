# SPDX-License-Identifier: Apache-2.0
"""A thin client of the Kubernetes API, as one service account.

Only the paths the tools need are built here (`velero.io/v1` custom resources in the Velero namespace, and
namespaces), so the surface is auditable in one file. The token goes to `kube_api_url` only: no redirect is
followed. The cluster's CA (`kube_ca_cert`, PEM) is the only trust anchor when given. Neither the token nor a
response body is logged or put in an error: a Kubernetes `Status` message is kept (it names the verb and the
resource, never a secret) and truncated.

API shapes: https://kubernetes.io/docs/reference/using-api/api-concepts/ (list `limit` / `continue`,
`labelSelector`, `Status` objects) and https://velero.io/docs/main/api-types/.
"""

from __future__ import annotations

import ssl
from collections.abc import Mapping
from typing import Any

import httpx

from .config import Settings
from .errors import Failure

TIMEOUT_S = 15.0
PAGE = 500
MAX_PAGES = 10
VELERO_GROUP = "/apis/velero.io/v1"


def _status_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    return str(message)[:300] if message else ""


class Kube:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        token = self.settings.kube_token.get_secret_value() if self.settings.kube_token else "fake-token"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        verify: ssl.SSLContext | bool = True
        if self.transport is None and self.settings.kube_ca_cert:
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # verifies the certificate and the host name
                context.load_verify_locations(cadata=self.settings.kube_ca_cert)
            except (ssl.SSLError, ValueError):
                raise Failure("not_configured", "kube_ca_cert is not a valid PEM certificate bundle") from None
            verify = context
        return httpx.AsyncClient(
            base_url=self.settings.kube_api_url or "https://kubernetes.fake.example.com",
            headers=headers,
            transport=self.transport,
            verify=verify if self.transport is None else True,
            timeout=TIMEOUT_S,
            follow_redirects=False,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: dict[str, Any] | None = None,
        what: str = "the Kubernetes API",
    ) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.request(method, path, params=params, json=body)
        except httpx.HTTPError as error:
            raise Failure("upstream_error", f"{what} could not be reached ({type(error).__name__})") from None
        if response.is_success:
            try:
                parsed = response.json()
            except ValueError:
                raise Failure("upstream_error", f"{what} answered something that is not JSON") from None
            return parsed if isinstance(parsed, dict) else {}
        detail = _status_message(response)
        suffix = f": {detail}" if detail else ""
        status = response.status_code
        if status == 401:
            raise Failure("unauthorized", f"{what} refused the service account's token{suffix}", status)
        if status == 403:
            raise Failure("forbidden", f"the service account may not {method} {path}{suffix}", status)
        if status == 404:
            raise Failure("not_found", f"{path} does not exist{suffix}", status)
        if status == 409:
            raise Failure("conflict", f"{path} already exists or changed{suffix}", status)
        if status == 429:
            raise Failure("rate_limited", f"{what} is rate limiting the server", status)
        raise Failure("upstream_error", f"{what} answered {status}{suffix}", status)

    async def get(self, path: str, what: str = "the Kubernetes API") -> dict[str, Any]:
        return await self.request("GET", path, what=what)

    async def list(self, path: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        """Every item of a collection, following `metadata.continue` up to `MAX_PAGES` pages."""
        items: list[dict[str, Any]] = []
        token = ""
        for _ in range(MAX_PAGES):
            params = {"limit": str(PAGE)}
            if label_selector:
                params["labelSelector"] = label_selector
            if token:
                params["continue"] = token
            page = await self.request("GET", path, params=params)
            items += [i for i in page.get("items") or [] if isinstance(i, dict)]
            token = str((page.get("metadata") or {}).get("continue") or "")
            if not token:
                break
        return items

    # --- Velero custom resources (the Velero namespace only) -------------------------------------------------

    def velero(self, plural: str, name: str | None = None) -> str:
        base = f"{VELERO_GROUP}/namespaces/{self.settings.velero_namespace}/{plural}"
        return f"{base}/{name}" if name else base
