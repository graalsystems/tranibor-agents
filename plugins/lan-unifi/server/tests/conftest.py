# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from tranibor_lan_conformance import McpTarget

from tranibor_lan_unifi.app import Runtime, build_app, build_server
from tranibor_lan_unifi.config import Settings
from tranibor_lan_unifi.fake import AP_LOBBY, AP_ROOM_12, AP_ROOM_14, MISSING, SITE, STAFF_LAPTOP, SWITCH, FakeUnifi

NOW = datetime(2026, 11, 25, 9, 17, tzinfo=UTC)
TOKEN = "platform-token-for-tests"
#: The Host the platform's Service gives the server: not localhost, and accepted.
BASE = "http://unifi.tranibor-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}


def clock() -> datetime:
    return NOW


class Mcp:
    """Calls the server over HTTP as the platform does: with its bearer token."""

    def __init__(self, http: TestClient) -> None:
        self.http = http
        self.ids = 0

    @property
    def fake(self) -> FakeUnifi:
        fake = self.http.app.state.runtime.fake  # type: ignore[attr-defined]
        assert isinstance(fake, FakeUnifi)
        return fake

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ids += 1
        body = {"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params}
        response = self.http.post("/mcp", headers=MCP_HEADERS, json=body)
        assert response.status_code == 200, response.text
        answer = response.json()
        assert "result" in answer, answer
        result = answer["result"]
        assert isinstance(result, dict)
        return result

    def call(self, tool: str, /, **arguments: Any) -> dict[str, Any]:
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        assert not result.get("isError"), result["content"]
        structured = result["structuredContent"]
        assert isinstance(structured, dict)
        return structured

    def error(self, tool: str, /, **arguments: Any) -> str:
        """The typed error, `<code>: <why>`, without the SDK's `Error executing tool <tool>: `."""
        result = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        assert result.get("isError"), result
        text = str(result["content"][0]["text"])
        prefix = f"Error executing tool {tool}: "
        assert text.startswith(prefix), text
        return text.removeprefix(prefix)


@pytest.fixture
def http() -> Iterator[TestClient]:
    env = {"LAN_UNIFI_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN}
    app = build_app(Settings.from_env(env), clock=clock)
    with TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def mcp(http: TestClient) -> Mcp:
    return Mcp(http)


DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "lan-unifi.yaml"


@pytest.fixture(params=["local", "cloud"])
def lan_target(request: pytest.FixtureRequest) -> McpTarget:
    """What the profile's conformance suite drives: this server, in memory, over the UniFi fake, reached
    locally and through the cloud connector (the same suite, the same tools, only the way in differs)."""
    env = {"LAN_UNIFI_FAKE": "1", "UNIFI_CONNECTION": request.param}
    runtime = Runtime(Settings.from_env(env), clock=clock)
    fake = runtime.fake
    assert isinstance(fake, FakeUnifi)
    return McpTarget(
        build_server(runtime),
        descriptor_path=DESCRIPTOR,
        site_id=SITE,
        device_id=AP_ROOM_12,
        offline_device_id=AP_ROOM_14,
        restart_device_id=AP_LOBBY,
        poe_device_id=SWITCH,
        poe_port=5,
        client_id=STAFF_LAPTOP,
        missing_id=MISSING,
        # Every request the server sends counts: a `not_supported` answer must send none.
        upstream_calls=lambda: len(fake.calls),
    )
