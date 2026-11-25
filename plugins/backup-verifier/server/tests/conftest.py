# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient

from tranibor_backup_verifier.app import Runtime, build_app
from tranibor_backup_verifier.config import Settings
from tranibor_backup_verifier.fake import FakeCluster
from tranibor_backup_verifier.tools import Tools

#: The fake cluster is seeded relative to this moment.
NOW = datetime(2026, 11, 25, 9, 17, tzinfo=UTC)
TOKEN = "platform-token-for-tests"
BASE = "http://velero.tranibor-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}
FAKE_ENV = {"VELERO_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN}


def clock() -> datetime:
    return NOW


class Mcp:
    """Calls the server over HTTP as the platform does, with its bearer token."""

    def __init__(self, http: TestClient) -> None:
        self.http = http
        self.ids = 0

    @property
    def fake(self) -> FakeCluster:
        fake = self.http.app.state.runtime.fake  # type: ignore[attr-defined]
        assert isinstance(fake, FakeCluster)
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
    app = build_app(Settings.from_env(FAKE_ENV), clock=clock)
    with TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def mcp(http: TestClient) -> Mcp:
    return Mcp(http)


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(Settings.from_env(FAKE_ENV), clock=clock)


@pytest.fixture
def tools(runtime: Runtime) -> Tools:
    return runtime.tools


@pytest.fixture
def fake(runtime: Runtime) -> FakeCluster:
    assert runtime.fake is not None
    return runtime.fake
