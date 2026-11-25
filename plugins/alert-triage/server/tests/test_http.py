# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` behind the platform's bearer token, `/healthz` open; the start-up rules."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tranibor_alert_triage.app import build_app
from tranibor_alert_triage.config import Settings

from .conftest import NOW

TOKEN = "platform-token-for-tests"
#: The Host the platform's Service gives the server: not localhost, and accepted.
BASE = "http://grafana.tranibor-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}


def _app(**env: str) -> TestClient:
    settings = Settings.from_env({"ALERT_TRIAGE_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN, **env})
    return TestClient(build_app(settings, clock=lambda: NOW), base_url=BASE)


def _tools_list(client: TestClient, authorization: str) -> int:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {**MCP_HEADERS, "Authorization": authorization}
    return client.post("/mcp", headers=headers, json=body).status_code


def test_healthz_is_open() -> None:
    with _app() as client:
        assert client.get("/healthz").json() == {"status": "ok", "fake": True, "version": "0.1.0"}


@pytest.mark.parametrize(
    "authorization",
    ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "],
)
def test_mcp_without_the_platform_token_is_refused(authorization: str) -> None:
    with _app() as client:
        assert _tools_list(client, authorization) == 401
        assert client.post("/mcp/", headers={**MCP_HEADERS, "Authorization": authorization}, json={}).status_code == 401


def test_the_platform_token_lists_the_tools_and_outputs_say_they_are_untrusted() -> None:
    with _app() as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}, json=body)
        assert response.status_code == 200
        tools = response.json()["result"]["tools"]
        assert len(tools) == 12
        carriers = {t["name"] for t in tools if "untrusted" in t["outputSchema"]["properties"]}
        # What carries text a third party wrote says so; the writes' answers are ours.
        assert {"list_firing_alerts", "get_alert_rule", "query_logs", "list_annotations"} <= carriers
        assert not carriers & {"create_silence", "expire_silence"}


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        {"ALERT_TRIAGE_FAKE": "1"},
        {"ALERT_TRIAGE_FAKE": "1", "TRANIBOR_SERVER_TOKEN": ""},
        {"ALERT_TRIAGE_FAKE": "1", "TRANIBOR_SERVER_INSECURE_DEV": "true"},
        {"TRANIBOR_SERVER_INSECURE_DEV": "1", "GRAFANA_BASE_URL": "https://grafana.example.com", "GRAFANA_TOKEN": "x"},
    ):
        with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV"])
def test_insecure_dev_runs_the_fake_only_and_accepts_every_call(variable: str) -> None:
    app = build_app(Settings.from_env({"ALERT_TRIAGE_FAKE": "1", variable: "1"}))
    with TestClient(app, base_url=BASE) as client:
        assert _tools_list(client, "") == 200


def test_a_real_stack_needs_its_url_and_token() -> None:
    with pytest.raises(ValueError, match="GRAFANA_BASE_URL"):
        Settings.from_env({"TRANIBOR_SERVER_TOKEN": TOKEN})
    with pytest.raises(ValueError, match="https://"):
        Settings.from_env({"TRANIBOR_SERVER_TOKEN": TOKEN, "GRAFANA_BASE_URL": "http://grafana.example.com"})
    with pytest.raises(ValueError, match="GRAFANA_TOKEN"):
        Settings.from_env({"TRANIBOR_SERVER_TOKEN": TOKEN, "GRAFANA_BASE_URL": "https://grafana.example.com"})
    env = {"TRANIBOR_SERVER_TOKEN": TOKEN, "GRAFANA_BASE_URL": "https://grafana.example.com/", "GRAFANA_TOKEN": "t"}
    with TestClient(build_app(Settings.from_env(env))) as client:
        assert client.get("/healthz").json()["fake"] is False


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN"])
def test_the_token_comes_from_the_current_core_s_variable_or_an_older_core_s(variable: str) -> None:
    """The core gives the pod `TRANIBOR_SERVER_TOKEN` since its rename; an older core, `CHOREGOS_SERVER_TOKEN`."""
    app = build_app(Settings.from_env({"ALERT_TRIAGE_FAKE": "1", variable: TOKEN}))
    with TestClient(app, base_url=BASE) as client:
        assert _tools_list(client, f"Bearer {TOKEN}") == 200
        assert _tools_list(client, f"Bearer {TOKEN}x") == 401


def test_the_current_core_s_variable_wins_over_an_older_core_s() -> None:
    env = {"ALERT_TRIAGE_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN, "CHOREGOS_SERVER_TOKEN": "stale"}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE) as client:
        assert _tools_list(client, f"Bearer {TOKEN}") == 200
        assert _tools_list(client, "Bearer stale") == 401


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GRAFANA_MAX_RANGE_HOURS", "25"),
        ("GRAFANA_MAX_RANGE_HOURS", "0"),
        ("GRAFANA_MAX_LINES", "1001"),
        ("GRAFANA_MAX_SILENCE_MINUTES", "1441"),
        ("GRAFANA_MAX_SILENCE_MINUTES", "soon"),
    ],
)
def test_the_caps_cannot_exceed_their_ceilings(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        Settings.from_env({"ALERT_TRIAGE_FAKE": "1", name: value})
