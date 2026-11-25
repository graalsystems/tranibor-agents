# SPDX-License-Identifier: Apache-2.0
"""The Argo CD REST API (`/api/v1/...`), behind the rules of this server.

Endpoints, from the swagger of Argo CD v2.14 (`assets/swagger.json`) and the API docs
(https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/):

- `GET /api/v1/applications?projects=<p>&projects=<q>`          list, restricted to AppProjects
- `GET /api/v1/applications/{name}[?refresh=normal|hard]`       one Application (`refresh` also refreshes it)
- `GET /api/v1/applications/{name}/resource-tree`               the resource tree
- `GET /api/v1/applications/{name}/managed-resources`           live and target state of each managed resource
- `GET /api/v1/applications/{name}/events`                      Kubernetes events (see `events` below)
- `GET /api/v1/applications/{name}/syncwindows`                 `canSync`, `activeWindows`, `assignedWindows`
- `GET /api/v1/applications/{name}/revisions/{revision}/metadata`  author, date, message of a revision
- `POST /api/v1/applications/{name}/sync`                       body `ApplicationSyncRequest`
- `POST /api/v1/applications/{name}/rollback`                   body `ApplicationRollbackRequest`
- `DELETE /api/v1/applications/{name}/operation`                terminate the running operation

Authentication is `Authorization: Bearer <token>` of a local account's API key (the user-management docs:
`argocd account generate-token`). Errors are the gateway's `{"error", "code", "message"}`.

This client has no method that deletes an Application, patches a spec, or touches a project or RBAC: there
is nothing to call by mistake.

Not confirmed by the swagger (written from the server's behaviour, to check on a live Argo CD, see
`tests/live`): the swagger types `ResourceNode` without its `group`, `kind`, `name`, `namespace`, `uid`
(they come from the embedded `ResourceRef`); `liveState`, `targetState`, `normalizedLiveState` and
`predictedLiveState` of a managed resource are JSON texts; `events` of an Application with none of
`resourceName` / `resourceUID` returns the events of the Application object itself.
"""

from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from .config import Settings
from .errors import ArgoCDError

log = structlog.get_logger("tranibor_argocd.client")


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        text = body.get("message") or body.get("error")
        if text:
            return f"Argo CD answered {response.status_code}: {str(text)[:500]}"
    return f"Argo CD answered {response.status_code}"


def _segment(value: str) -> str:
    """One path segment, never with a slash or a dot-dot a caller could use to leave the Application path."""
    return quote(value, safe="")


class ArgoCDClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        verify: ssl.SSLContext | bool = True
        if settings.ca_cert:
            verify = ssl.create_default_context(cadata=settings.ca_cert)
        self._http = httpx.AsyncClient(
            base_url=settings.server_url,
            transport=transport,
            verify=verify,
            timeout=httpx.Timeout(30.0),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {settings.token.get_secret_value()}",
            },
        )

    @property
    def settings(self) -> Settings:
        return self._settings

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        # Stateless: no session cookie of an earlier call is sent back.
        self._http.cookies.clear()
        try:
            response = await self._http.request(method, path, params=params, json=json)
        except httpx.HTTPError as error:
            raise ArgoCDError(f"Argo CD unreachable: {type(error).__name__}") from error
        if response.status_code >= 400:
            raise ArgoCDError(_error_message(response), response.status_code)
        return response.json() if response.content else {}

    # ─────────────────────────── reads ───────────────────────────

    async def list_applications(self, projects: list[str]) -> list[dict[str, Any]]:
        body = await self._request("GET", "/api/v1/applications", params={"projects": projects})
        return list(body.get("items") or [])

    async def get_application(self, name: str, refresh: str | None = None) -> dict[str, Any]:
        params = {"refresh": refresh} if refresh else None
        return dict(await self._request("GET", f"/api/v1/applications/{_segment(name)}", params=params))

    async def resource_tree(self, name: str) -> dict[str, Any]:
        return dict(await self._request("GET", f"/api/v1/applications/{_segment(name)}/resource-tree"))

    async def managed_resources(self, name: str) -> list[dict[str, Any]]:
        body = await self._request("GET", f"/api/v1/applications/{_segment(name)}/managed-resources")
        return list(body.get("items") or [])

    async def events(
        self, name: str, resource_name: str | None = None, resource_namespace: str | None = None
    ) -> list[dict[str, Any]]:
        params = {
            k: v for k, v in {"resourceName": resource_name, "resourceNamespace": resource_namespace}.items() if v
        }
        body = await self._request("GET", f"/api/v1/applications/{_segment(name)}/events", params=params or None)
        return list(body.get("items") or [])

    async def sync_windows(self, name: str) -> dict[str, Any]:
        return dict(await self._request("GET", f"/api/v1/applications/{_segment(name)}/syncwindows"))

    async def revision_metadata(self, name: str, revision: str) -> dict[str, Any]:
        path = f"/api/v1/applications/{_segment(name)}/revisions/{_segment(revision)}/metadata"
        return dict(await self._request("GET", path))

    # ─────────────────────────── writes ───────────────────────────

    async def sync(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
        return dict(await self._request("POST", f"/api/v1/applications/{_segment(name)}/sync", json=request))

    async def rollback(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
        return dict(await self._request("POST", f"/api/v1/applications/{_segment(name)}/rollback", json=request))

    async def terminate_operation(self, name: str) -> dict[str, Any]:
        return dict(await self._request("DELETE", f"/api/v1/applications/{_segment(name)}/operation"))
