# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient

from tranibor_unifi.app import build_app
from tranibor_unifi.config import Settings
from tranibor_unifi.fake import FakeUnifi

NOW = datetime(2026, 11, 25, 9, 17, tzinfo=UTC)
TOKEN = "platform-token-for-tests"
#: The Host the platform's Service gives the server: not localhost, and accepted.
BASE = "http://unifi.choregos-plugins.svc:8080"
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
    env = {"UNIFI_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN}
    app = build_app(Settings.from_env(env), clock=clock)
    with TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def mcp(http: TestClient) -> Mcp:
    return Mcp(http)
