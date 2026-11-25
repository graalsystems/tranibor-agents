# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from starlette.testclient import TestClient
from tranibor_identity_conformance import McpTarget

from tranibor_identity_keycloak.app import Runtime, build_app, build_server
from tranibor_identity_keycloak.config import Settings
from tranibor_identity_keycloak.fake import FakeKeycloak, _id
from tranibor_identity_keycloak.tools import Tools

#: The fake realms are seeded relative to this hour.
NOW = datetime(2026, 11, 26, 9, 17, tzinfo=UTC)
TOKEN = "platform-token-for-tests"
BASE = "http://keycloak.tranibor-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}
DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "identity-keycloak.yaml"
ENV = {"KEYCLOAK_FAKE": "1", "TRANIBOR_SERVER_TOKEN": TOKEN}


def clock() -> datetime:
    return NOW


def uid(name: str) -> str:
    return _id(name)


class HttpMcp:
    """Calls the server over HTTP as the platform does, with its bearer token."""

    def __init__(self, http: TestClient) -> None:
        self.http = http
        self.ids = 0

    @property
    def fake(self) -> FakeKeycloak:
        fake = self.http.app.state.runtime.fake  # type: ignore[attr-defined]
        assert isinstance(fake, FakeKeycloak)
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


class Mcp:
    """Calls a tool in-process through the MCP protocol and returns its structured result."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server

    async def raw(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        async with Client(self.server) as client:
            return await client.call_tool(name, arguments)

    async def call(self, tool: str, /, **arguments: Any) -> dict[str, Any]:
        result = await self.raw(tool, arguments)
        assert not result.is_error, result.content
        assert result.structured_content is not None
        return dict(result.structured_content)

    async def error(self, tool: str, /, **arguments: Any) -> str:
        result = await self.raw(tool, arguments)
        assert result.is_error, result.structured_content
        text = result.content[0]
        return str(getattr(text, "text", text))


@pytest.fixture
def http() -> Iterator[TestClient]:
    app = build_app(Settings.from_env(ENV), clock=clock)
    with TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def web(http: TestClient) -> HttpMcp:
    return HttpMcp(http)


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(Settings.from_env(ENV), clock=clock)


@pytest.fixture
def tools(runtime: Runtime) -> Tools:
    return runtime.tools


@pytest.fixture
def fake(runtime: Runtime) -> FakeKeycloak:
    assert runtime.fake is not None
    return runtime.fake


@pytest.fixture
def server(runtime: Runtime) -> MCPServer:
    return build_server(runtime)


@pytest.fixture
def mcp(server: MCPServer) -> Mcp:
    return Mcp(server)


@pytest.fixture
def identity_target(server: MCPServer, fake: FakeKeycloak) -> McpTarget:
    """What the profile's conformance suite drives: this server over the Keycloak fake."""
    return McpTarget(
        server,
        descriptor_path=DESCRIPTOR,
        directory="demo",
        foreign_directory="private",
        # The token requests count too: a refused call must send nothing at all.
        upstream_calls=lambda: fake.upstream_calls,
        mail_configured=True,
    )
