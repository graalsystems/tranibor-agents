# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` behind the platform's bearer token, `/healthz` open; the start-up rules."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tranibor_backup_verifier.app import build_app
from tranibor_backup_verifier.config import Settings

from .conftest import BASE, MCP_HEADERS, TOKEN, Mcp

TOOLS = {
    "list_schedules",
    "list_backups",
    "get_backup",
    "storage_locations",
    "list_restores",
    "get_restore",
    "backup_freshness",
    "bucket_listing",
    "create_test_restore",
    "delete_test_restore_namespace",
}


def test_healthz(http: TestClient) -> None:
    assert http.get("/healthz", headers={"Authorization": ""}).json() == {
        "status": "ok",
        "fake": True,
        "version": "0.1.0",
    }


@pytest.mark.parametrize(
    "authorization",
    ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "],
)
def test_mcp_without_the_platform_token_is_refused(http: TestClient, authorization: str) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = http.post("/mcp", headers={**MCP_HEADERS, "Authorization": authorization}, json=body)
    assert response.status_code == 401


def test_the_tools_are_listed(mcp: Mcp) -> None:
    assert {t["name"] for t in mcp.rpc("tools/list", {})["tools"]} == TOOLS


def test_a_read_over_http(mcp: Mcp) -> None:
    rows = mcp.call("backup_freshness")["schedules"]
    assert {r["schedule"]: r["freshness"] for r in rows} == {
        "daily-apps": "fresh",
        "weekly-archive": "fresh",
        "hourly-db": "stale",
    }


def test_a_refusal_is_a_typed_error(mcp: Mcp) -> None:
    assert mcp.error("delete_test_restore_namespace", namespace="kube-system").startswith("refused: ")
    assert mcp.error("get_backup", name="nope-20250101000000").startswith("not_found: ")


def test_a_write_over_http(mcp: Mcp) -> None:
    done = mcp.call("create_test_restore", backup_name="daily-apps-20261125021700", source_namespace="shop")
    assert done["target_namespace"] == "restore-test-shop-20261125"


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        {"VELERO_FAKE": "1"},
        {"VELERO_FAKE": "1", "TRANIBOR_SERVER_TOKEN": ""},
        {"VELERO_FAKE": "1", "TRANIBOR_SERVER_INSECURE_DEV": "true"},
        {"TRANIBOR_SERVER_INSECURE_DEV": "1"},
        {},
    ):
        with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


def test_an_older_cores_token_name_is_still_read() -> None:
    app = build_app(Settings.from_env({"VELERO_FAKE": "1", "CHOREGOS_SERVER_TOKEN": TOKEN}))
    with TestClient(app, base_url=BASE) as client:
        assert client.post("/mcp", headers=MCP_HEADERS, json={}).status_code == 401


def test_insecure_dev_runs_only_on_the_fake() -> None:
    app = build_app(Settings.from_env({"VELERO_FAKE": "1", "TRANIBOR_SERVER_INSECURE_DEV": "1"}))
    with TestClient(app, base_url=BASE) as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert client.post("/mcp", headers=MCP_HEADERS, json=body).status_code == 200


def test_an_unconfigured_connector_starts_and_every_tool_says_what_is_missing() -> None:
    app = build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": TOKEN}))
    with TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        mcp = Mcp(client)
        assert {t["name"] for t in mcp.rpc("tools/list", {})["tools"]} == TOOLS
        assert mcp.error("list_schedules").startswith("not_configured: kube_api_url")
