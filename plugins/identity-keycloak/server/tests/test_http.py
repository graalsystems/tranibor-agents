# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` behind the platform's bearer token, `/healthz` open; the start-up rules."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tranibor_identity_keycloak.app import build_app
from tranibor_identity_keycloak.config import Settings

from .conftest import BASE, MCP_HEADERS, TOKEN, HttpMcp, uid


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
def test_insecure_dev_runs_the_fake_only(variable: str) -> None:
    app = build_app(Settings.from_env({"KEYCLOAK_FAKE": "1", variable: "1"}))
    with TestClient(app, base_url=BASE) as client:
        assert HttpMcp(client).call("get_provider")["directories"] == ["demo"]


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN"])
def test_the_token_comes_from_the_current_core_s_variable_or_an_older_core_s(variable: str) -> None:
    with TestClient(build_app(Settings.from_env({"KEYCLOAK_FAKE": "1", variable: TOKEN})), base_url=BASE) as client:
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}"}) == 200
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}x"}) == 401


def test_a_real_server_needs_its_fields() -> None:
    with pytest.raises(ValueError, match=r"KEYCLOAK_REALMS|KEYCLOAK_CLIENT"):
        Settings.from_env({"TRANIBOR_SERVER_TOKEN": TOKEN, "KEYCLOAK_BASE_URL": "https://sso.example.com"})


def test_the_instance_name_is_the_one_the_platform_gives(http: TestClient) -> None:
    app = build_app(
        Settings.from_env({"KEYCLOAK_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN, "TRANIBOR_INSTANCE_NAME": "idp-eu"})
    )
    with TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        assert HttpMcp(client).call("get_provider") == {
            "instance": "idp-eu",
            "provider": "keycloak",
            "directories": ["demo"],
        }


def test_tools_over_http_return_typed_results_and_typed_errors(web: HttpMcp) -> None:
    result = web.call("find_users", directory="demo", search="alice")
    assert result["users"][0]["username"] == "alice.martin"
    assert web.error("get_user", directory="demo", username="nobody").startswith("not_found: ")
    assert web.error("get_user", directory="demo", user_id="..").startswith("invalid_arguments: ")
    assert web.error("get_user", directory="private", username="alice.martin").startswith("invalid_arguments: ")


def test_a_write_over_http_goes_through_the_fake_and_is_visible_to_the_next_read(web: HttpMcp) -> None:
    done = web.call("disable_user", directory="demo", user_id=uid("david.leroy"))
    assert done["changed"] is True
    assert web.call("get_user", directory="demo", user_id=uid("david.leroy"))["user"]["enabled"] is False
