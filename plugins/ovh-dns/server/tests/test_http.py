# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` (streamable HTTP) and `/healthz`, all but the health probe behind the platform's
bearer token (ADR 0094)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from tranibor_ovh_dns import __version__
from tranibor_ovh_dns.app import build_app
from tranibor_ovh_dns.config import Settings

TOKEN = "platform-token-for-tests"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://ovh-dns.choregos-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}


@pytest.fixture
def http() -> Iterator[TestClient]:
    env = {"OVH_DNS_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE, headers=BEARER) as client:
        yield client


def _tools_list(client: TestClient, headers: dict[str, str]) -> int:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=body).status_code


def test_healthz_is_open() -> None:
    env = {"OVH_DNS_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE) as client:
        assert client.get("/healthz").json() == {"status": "ok", "fake": True, "version": __version__}


@pytest.mark.parametrize(
    "authorization",
    ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "],
)
def test_mcp_without_the_platform_token_is_refused(http: TestClient, authorization: str) -> None:
    assert _tools_list(http, {"Authorization": authorization}) == 401


def test_mcp_with_the_platform_token_answers(http: TestClient) -> None:
    assert _tools_list(http, {}) == 200


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        {"OVH_DNS_FAKE": "1"},
        {"OVH_DNS_FAKE": "1", "TRANIBOR_SERVER_TOKEN": ""},
        {"OVH_DNS_FAKE": "1", "TRANIBOR_SERVER_INSECURE_DEV": "true"},
        {
            "OVH_DNS_ENDPOINT": "ovh-eu",
            "OVH_DNS_APPLICATION_KEY": "ak-for-tests",
            "OVH_DNS_APPLICATION_SECRET": "as-for-tests",
            "OVH_DNS_CONSUMER_KEY": "ck-for-tests",
            "OVH_DNS_ZONES": "example.com",
            "TRANIBOR_SERVER_INSECURE_DEV": "1",
        },
    ):
        with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV"])
def test_insecure_dev_runs_the_fake_without_a_token(variable: str) -> None:
    settings = Settings.from_env({"OVH_DNS_FAKE": "1", variable: "1"})
    with TestClient(build_app(settings), base_url=BASE) as client:
        assert _tools_list(client, {}) == 200


@pytest.mark.parametrize("variable", ["TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN"])
def test_the_token_comes_from_the_current_core_s_variable_or_an_older_core_s(variable: str) -> None:
    settings = Settings.from_env({"OVH_DNS_FAKE": "1", variable: TOKEN})
    with TestClient(build_app(settings), base_url=BASE) as client:
        assert _tools_list(client, BEARER) == 200
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}x"}) == 401


def test_a_real_account_needs_all_of_its_configuration() -> None:
    base = {"TRANIBOR_SERVER_TOKEN": TOKEN}
    with pytest.raises(ValueError, match="OVH_DNS_APPLICATION_KEY"):
        build_app(Settings.from_env({**base, "OVH_DNS_ENDPOINT": "ovh-eu"}))
    with pytest.raises(ValueError, match="OVH_DNS_ENDPOINT"):
        build_app(Settings.from_env({**base, "OVH_DNS_ENDPOINT": "ovh-moon"}))
    with pytest.raises(ValueError, match="not a zone name"):
        Settings.from_env({**base, "OVH_DNS_ZONES": "not a zone"})
