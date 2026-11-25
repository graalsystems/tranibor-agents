# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` (streamable HTTP) and `/healthz`, all but the probe behind the platform's token."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from tranibor_argocd.app import build_app
from tranibor_argocd.config import Settings

TOKEN = "platform-token-for-tests"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://argocd.tranibor-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}


@pytest.fixture
def http() -> Iterator[TestClient]:
    env = {"ARGOCD_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE, headers=BEARER) as client:
        yield client


def _call(client: TestClient, headers: dict[str, str], method: str = "tools/list", params: object = None) -> object:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=body)


def test_healthz_is_open() -> None:
    env = {"ARGOCD_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE) as client:
        assert client.get("/healthz").json() == {"status": "ok", "fake": True, "version": "0.1.0"}


@pytest.mark.parametrize(
    "authorization", ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "]
)
def test_mcp_without_the_platform_token_is_refused(http: TestClient, authorization: str) -> None:
    assert _call(http, {"Authorization": authorization}).status_code == 401  # type: ignore[attr-defined]


def test_mcp_with_the_platform_token_lists_and_calls(http: TestClient) -> None:
    assert _call(http, {}).status_code == 200  # type: ignore[attr-defined]
    answer = _call(http, {}, "tools/call", {"name": "list_applications", "arguments": {"project": "shop"}})
    assert answer.status_code == 200  # type: ignore[attr-defined]
    assert "shop-web" in answer.text  # type: ignore[attr-defined]


def test_the_server_refuses_to_start_without_the_platform_token() -> None:
    with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
        build_app(Settings.from_env({"ARGOCD_FAKE": "1"}))
