# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` (streamable HTTP) and `/healthz`, all but the health probe behind the platform's
bearer token (ADR 0094)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from tranibor_certificates_ct import __version__
from tranibor_certificates_ct.app import build_app
from tranibor_certificates_ct.config import Settings
from tranibor_certificates_ct.envs import INSECURE_DEV_ENVS, TOKEN_ENVS

TOKEN = "platform-token-for-tests"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
#: The Host the platform's Service gives the server: not localhost, and accepted.
BASE = "http://certificates-ct.tranibor-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}
FAKE = {"CERTIFICATES_CT_FAKE": "1"}
#: A real (non-fake) configuration: insecure dev mode is the fake's alone.
REAL = {"CERTIFICATES_CT_DOMAINS": "example.com", "CERTIFICATES_CT_FAKE": ""}


@pytest.fixture
def http() -> Iterator[TestClient]:
    with TestClient(
        build_app(Settings.from_env({**FAKE, "TRANIBOR_SERVER_TOKEN": TOKEN})), base_url=BASE, headers=BEARER
    ) as c:
        yield c


def _tools_list(client: TestClient, headers: dict[str, str]) -> int:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=body).status_code


def test_token_variable_names_are_the_platform_s() -> None:
    assert TOKEN_ENVS == ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
    assert INSECURE_DEV_ENVS == ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")


def test_healthz(http: TestClient) -> None:
    assert http.get("/healthz", headers={"Authorization": ""}).json() == {
        "status": "ok",
        "fake": True,
        "version": __version__,
    }


@pytest.mark.parametrize(
    "authorization",
    ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "],
)
def test_mcp_without_the_platform_token_is_refused(http: TestClient, authorization: str) -> None:
    assert _tools_list(http, {"Authorization": authorization}) == 401
    assert http.post("/mcp/", headers={**MCP_HEADERS, "Authorization": authorization}, json={}).status_code == 401


def test_mcp_with_the_platform_token_answers(http: TestClient) -> None:
    assert _tools_list(http, {}) == 200


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        FAKE,
        {**FAKE, "TRANIBOR_SERVER_TOKEN": ""},
        {**FAKE, "TRANIBOR_SERVER_TOKEN": "", "CHOREGOS_SERVER_TOKEN": ""},
        {**FAKE, "TRANIBOR_SERVER_INSECURE_DEV": "true"},
        {**REAL, "TRANIBOR_SERVER_INSECURE_DEV": "1"},
    ):
        with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


@pytest.mark.parametrize("variable", INSECURE_DEV_ENVS)
def test_insecure_dev_runs_the_fake_without_a_token(variable: str) -> None:
    with TestClient(build_app(Settings.from_env({**FAKE, variable: "1"})), base_url=BASE) as client:
        assert _tools_list(client, {}) == 200


@pytest.mark.parametrize("variable", TOKEN_ENVS)
def test_the_token_comes_from_the_current_core_s_variable_or_an_older_core_s(variable: str) -> None:
    with TestClient(build_app(Settings.from_env({**FAKE, variable: TOKEN})), base_url=BASE) as client:
        assert _tools_list(client, BEARER) == 200
        assert _tools_list(client, {"Authorization": f"Bearer {TOKEN}x"}) == 401


def test_the_current_core_s_variable_wins_over_an_older_core_s() -> None:
    env = {**FAKE, "TRANIBOR_SERVER_TOKEN": TOKEN, "CHOREGOS_SERVER_TOKEN": "stale"}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE) as client:
        assert _tools_list(client, BEARER) == 200
        assert _tools_list(client, {"Authorization": "Bearer stale"}) == 401


def test_mcp_over_streamable_http(http: TestClient) -> None:
    response = http.post(
        "/mcp",
        headers=MCP_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_certificates", "arguments": {}},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["certificates"]
