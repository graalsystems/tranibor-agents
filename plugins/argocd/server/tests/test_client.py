# SPDX-License-Identifier: Apache-2.0
"""The client against the wire: auth header, paths, query strings, error mapping, TLS settings."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from tranibor_argocd.client import ArgoCDClient
from tranibor_argocd.config import Settings
from tranibor_argocd.errors import ArgoCDError

BASE = "https://argocd.example.com"
TOKEN = "fake-token-not-real"


def make(**overrides: object) -> ArgoCDClient:
    settings = Settings(base_url=BASE, token=SecretStr(TOKEN), projects=("shop",), **overrides)  # type: ignore[arg-type]
    return ArgoCDClient(settings)


@respx.mock
async def test_bearer_token_and_the_projects_query() -> None:
    route = respx.get(f"{BASE}/api/v1/applications").mock(return_value=httpx.Response(200, json={"items": [{"a": 1}]}))
    client = make()
    assert await client.list_applications(["shop", "billing"]) == [{"a": 1}]
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.url.params.get_list("projects") == ["shop", "billing"]
    await client.aclose()


@respx.mock
async def test_each_call_uses_its_documented_path_and_verb() -> None:
    for method, path in [
        ("GET", "/api/v1/applications/app-1"),
        ("GET", "/api/v1/applications/app-1/resource-tree"),
        ("GET", "/api/v1/applications/app-1/managed-resources"),
        ("GET", "/api/v1/applications/app-1/events"),
        ("GET", "/api/v1/applications/app-1/syncwindows"),
        ("GET", "/api/v1/applications/app-1/revisions/abc123/metadata"),
        ("POST", "/api/v1/applications/app-1/sync"),
        ("POST", "/api/v1/applications/app-1/rollback"),
        ("DELETE", "/api/v1/applications/app-1/operation"),
    ]:
        respx.route(method=method, url=f"{BASE}{path}").mock(return_value=httpx.Response(200, json={}))
    client = make()
    await client.get_application("app-1", refresh="hard")
    await client.resource_tree("app-1")
    await client.managed_resources("app-1")
    await client.events("app-1", resource_name="web", resource_namespace="prod")
    await client.sync_windows("app-1")
    await client.revision_metadata("app-1", "abc123")
    await client.sync("app-1", {"name": "app-1"})
    await client.rollback("app-1", {"name": "app-1", "id": 1})
    await client.terminate_operation("app-1")
    seen = {(c.request.method, c.request.url.path) for c in respx.calls}
    assert len(seen) == 9
    first = respx.calls[0].request
    assert first.url.params["refresh"] == "hard"
    assert respx.calls[3].request.url.params["resourceName"] == "web"
    await client.aclose()


@respx.mock
async def test_a_name_cannot_leave_its_path_segment() -> None:
    route = respx.route(url__startswith=f"{BASE}/api/v1/applications/").mock(return_value=httpx.Response(200, json={}))
    client = make()
    await client.get_application("../../settings/clusters")
    assert route.calls.last.request.url.raw_path.startswith(b"/api/v1/applications/..%2F..%2Fsettings%2Fclusters")
    await client.aclose()


@respx.mock
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401, json={"error": "invalid session", "message": "invalid session"}), "401: invalid session"),
        (httpx.Response(403, json={"message": "permission denied: applications, sync"}), "403: permission denied"),
        (httpx.Response(500, text="<html>boom</html>"), "Argo CD answered 500"),
    ],
)
async def test_errors_are_mapped_without_echoing_a_body(response: httpx.Response, expected: str) -> None:
    respx.get(f"{BASE}/api/v1/applications/x").mock(return_value=response)
    client = make()
    with pytest.raises(ArgoCDError, match=expected) as raised:
        await client.get_application("x")
    assert raised.value.status == response.status_code
    assert "html" not in str(raised.value)
    await client.aclose()


@respx.mock
async def test_unreachable_server_never_leaks_the_url_or_token() -> None:
    respx.get(f"{BASE}/api/v1/applications/x").mock(side_effect=httpx.ConnectError("boom " + TOKEN))
    client = make()
    with pytest.raises(ArgoCDError) as raised:
        await client.get_application("x")
    assert TOKEN not in str(raised.value)
    await client.aclose()


@respx.mock
async def test_session_cookies_are_not_replayed() -> None:
    route = respx.get(f"{BASE}/api/v1/applications/x").mock(
        return_value=httpx.Response(200, json={}, headers={"Set-Cookie": "argocd.token=abc; Path=/"})
    )
    client = make()
    await client.get_application("x")
    await client.get_application("x")
    assert "cookie" not in route.calls.last.request.headers
    await client.aclose()
