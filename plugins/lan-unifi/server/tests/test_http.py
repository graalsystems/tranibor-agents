# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` behind the platform's bearer token, `/healthz` open; the start-up rules."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tranibor_lan_unifi.app import build_app
from tranibor_lan_unifi.config import Settings

from .conftest import BASE, MCP_HEADERS, TOKEN, Mcp


def test_healthz(http: TestClient) -> None:
    assert http.get("/healthz", headers={"Authorization": ""}).json() == {
        "status": "ok",
        "fake": True,
        "cloud": True,
        "version": "0.1.0",
    }


def _tools_list(client: TestClient, headers: dict[str, str]) -> int:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=body).status_code


@pytest.mark.parametrize(
    "authorization", ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "]
)
def test_mcp_without_the_platform_token_is_refused(http: TestClient, authorization: str) -> None:
    assert _tools_list(http, {"Authorization": authorization}) == 401
    assert http.post("/mcp/", headers={**MCP_HEADERS, "Authorization": authorization}, json={}).status_code == 401


def test_the_tools_are_listed_with_a_typed_output(mcp: Mcp) -> None:
    tools = mcp.rpc("tools/list", {})["tools"]
    assert len(tools) == 23
    for tool in tools:
        assert tool["outputSchema"]["properties"]["source"]
        assert "as_of" in tool["outputSchema"]["required"]


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        {"LAN_UNIFI_FAKE": "1"},
        {"LAN_UNIFI_FAKE": "1", "TRANIBOR_SERVER_TOKEN": ""},
        {"LAN_UNIFI_FAKE": "1", "TRANIBOR_SERVER_INSECURE_DEV": "true"},
        {"TRANIBOR_SERVER_INSECURE_DEV": "1", "UNIFI_BASE_URL": "https://unifi.example.com", "UNIFI_API_KEY": "k" * 20},
    ):
        with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV"])
def test_insecure_dev_runs_the_fake_only(variable: str) -> None:
    with TestClient(build_app(Settings.from_env({"LAN_UNIFI_FAKE": "1", variable: "1"})), base_url=BASE) as client:
        assert Mcp(client).call("list_sites")["total"] == 1


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN"])
def test_the_token_comes_from_the_current_core_s_variable_or_an_older_core_s(variable: str) -> None:
    """The core gives the pod `TRANIBOR_SERVER_TOKEN` since its rename (core ADR 0176); an older core,
    `CHOREGOS_SERVER_TOKEN` (graalsystems/tranibor-agents#35: reading only one refused to start)."""
    with TestClient(build_app(Settings.from_env({"LAN_UNIFI_FAKE": "1", variable: TOKEN})), base_url=BASE) as client:
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}"}) == 200
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}x"}) == 401


def test_the_current_core_s_variable_wins_over_an_older_core_s() -> None:
    env = {"LAN_UNIFI_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN, "CHOREGOS_SERVER_TOKEN": "stale"}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE) as client:
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}"}) == 200
        assert _tools_list(client, {"Authorization": "Bearer stale"}) == 401
