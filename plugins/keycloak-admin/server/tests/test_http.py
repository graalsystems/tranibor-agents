# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` behind the platform's bearer token, `/healthz` open; the start-up rules."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tranibor_keycloak_admin.app import build_app
from tranibor_keycloak_admin.config import Settings

from .conftest import BASE, MCP_HEADERS, TOKEN, Mcp

WRITES = {
    "create_user",
    "add_user_to_group",
    "remove_user_from_group",
    "disable_user",
    "enable_user",
    "logout_user",
    "send_required_actions_email",
}
READS = {
    "find_users",
    "get_user",
    "list_user_groups",
    "list_groups",
    "get_group_members",
    "list_user_sessions",
    "list_clients",
    "get_realm_summary",
    "list_admin_events",
    "list_login_events",
    "dormant_users",
    "users_without_group",
}


def test_healthz(http: TestClient) -> None:
    assert http.get("/healthz", headers={"Authorization": ""}).json() == {
        "status": "ok",
        "fake": True,
        "version": "0.1.0",
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


def test_the_tools_served_are_the_reads_and_the_writes_and_nothing_else(mcp: Mcp) -> None:
    served = {t["name"] for t in mcp.rpc("tools/list", {})["tools"]}
    assert served == READS | WRITES
    assert not {name for name in served if "delete" in name or "impersonat" in name or "secret" in name}
    assert not {name for name in served if "credential" in name or "password" in name or "realm_setting" in name}


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        {"KEYCLOAK_FAKE": "1"},
        {"KEYCLOAK_FAKE": "1", "TRANIBOR_SERVER_TOKEN": ""},
        {"KEYCLOAK_FAKE": "1", "TRANIBOR_SERVER_INSECURE_DEV": "true"},
        {"TRANIBOR_SERVER_INSECURE_DEV": "1"},
    ):
        with pytest.raises(ValueError, match=r"TRANIBOR_SERVER_TOKEN|KEYCLOAK_"):
            build_app(Settings.from_env(env))


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV"])
def test_insecure_dev_runs_the_fake_realm_only(variable: str) -> None:
    app = build_app(Settings.from_env({"KEYCLOAK_FAKE": "1", variable: "1"}))
    with TestClient(app, base_url=BASE) as client:
        assert Mcp(client).call("get_realm_summary")["realm"]["realm"] == "demo"


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN"])
def test_the_token_comes_from_the_current_core_s_variable_or_an_older_core_s(variable: str) -> None:
    with TestClient(build_app(Settings.from_env({"KEYCLOAK_FAKE": "1", variable: TOKEN})), base_url=BASE) as client:
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}"}) == 200
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}x"}) == 401


def test_a_real_realm_needs_its_four_fields() -> None:
    with pytest.raises(ValueError, match=r"KEYCLOAK_REALM|KEYCLOAK_CLIENT"):
        Settings.from_env({"TRANIBOR_SERVER_TOKEN": TOKEN, "KEYCLOAK_BASE_URL": "https://sso.example.com"})


def test_tools_over_http_return_typed_results_and_typed_errors(mcp: Mcp) -> None:
    result = mcp.call("find_users", search="alice")
    assert result["users"][0]["username"] == "alice.martin"
    assert mcp.error("get_user", username="nobody").startswith("not_found: ")
    assert mcp.error("get_user", user_id="..").startswith("invalid_arguments: ")


def test_a_write_over_http_goes_through_the_fake_and_is_visible_to_the_next_read(mcp: Mcp) -> None:
    from .conftest import uid

    done = mcp.call("disable_user", user_id=uid("david.leroy"))
    assert done["changed"] is True
    assert mcp.call("get_user", user_id=uid("david.leroy"))["user"]["enabled"] is False
