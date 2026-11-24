# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` behind the platform's bearer token, `/healthz` open; the start-up rules."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from choregos_microsoft_365.app import build_app
from choregos_microsoft_365.config import Settings

from .conftest import BASE, MCP_HEADERS, TOKEN, Mcp


def test_healthz(http: TestClient) -> None:
    assert http.get("/healthz", headers={"Authorization": ""}).json() == {
        "status": "ok",
        "fake": True,
        "version": "0.1.1",
    }


def _tools_list(client: TestClient, headers: dict[str, str]) -> int:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=body).status_code


@pytest.mark.parametrize(
    "authorization",
    ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "],
)
def test_mcp_without_the_platform_token_is_refused(http: TestClient, authorization: str) -> None:
    assert _tools_list(http, {"Authorization": authorization}) == 401
    assert http.post("/mcp/", headers={**MCP_HEADERS, "Authorization": authorization}, json={}).status_code == 401


def test_a_persons_token_does_not_replace_the_platforms(http: TestClient) -> None:
    """The person's token reaches Graph; it never authenticates a caller to the server."""
    headers = {"Authorization": "", "X-Choregos-User-Token": TOKEN}
    assert _tools_list(http, headers) == 401


def test_the_tools_are_listed_without_a_person(mcp: Mcp) -> None:
    """The platform discovers the tools with its own token: no person is needed to list them."""
    tools = mcp.rpc("tools/list", {})["tools"]
    assert {t["name"] for t in tools} == {"list_events_with", "search_mail_with", "search_chats_with"}
    for tool in tools:
        assert tool["outputSchema"]["properties"]["source"]
        assert "as_of" in tool["outputSchema"]["required"]


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        {"M365_FAKE": "1"},
        {"M365_FAKE": "1", "TRANIBOR_SERVER_TOKEN": ""},
        {"M365_FAKE": "1", "TRANIBOR_SERVER_INSECURE_DEV": "true"},
        {"TRANIBOR_SERVER_INSECURE_DEV": "1"},
        {},
    ):
        with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV"])
def test_insecure_dev_runs_the_fake_tenant_only_and_still_needs_a_person(variable: str) -> None:
    app = build_app(Settings.from_env({"M365_FAKE": "1", variable: "1"}))
    with TestClient(app, base_url=BASE) as client:
        mcp = Mcp(client)
        assert mcp.call("list_events_with", within_hours=4)["events"] is not None
        assert mcp.error("list_events_with", person=None, within_hours=4).startswith("identity_unavailable: ")


def test_a_real_tenant_needs_no_configuration() -> None:
    """Nothing to configure on the server: it starts with the platform's token alone, and is not the fake."""
    with TestClient(build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": TOKEN}))) as client:
        assert client.get("/healthz").json()["fake"] is False


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN"])
def test_the_token_comes_from_the_current_core_s_variable_or_an_older_core_s(variable: str) -> None:
    """The core gives the pod `TRANIBOR_SERVER_TOKEN` since its rename (core ADR 0176); an older core,
    `CHOREGOS_SERVER_TOKEN`. Reading only the older name, the server refused to start on core 0.29.0
    (graalsystems/tranibor-agents#35, as servicenow 0.2.4 did on the Diametral dev cluster)."""
    with TestClient(build_app(Settings.from_env({"M365_FAKE": "1", variable: TOKEN})), base_url=BASE) as client:
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}"}) == 200
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}x"}) == 401


def test_the_current_core_s_variable_wins_over_an_older_core_s() -> None:
    env = {"M365_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN, "CHOREGOS_SERVER_TOKEN": "stale"}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE) as client:
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}"}) == 200
        assert _tools_list(client, {"Authorization": "Bearer stale"}) == 401
