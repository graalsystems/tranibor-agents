# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from tranibor_certificates_conformance import McpTarget

from tranibor_certificates_probe.app import Runtime, build_server
from tranibor_certificates_probe.config import Settings
from tranibor_certificates_probe.fake import FakeProber

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "certificates-probe.yaml"


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({"CERTIFICATES_PROBE_FAKE": "1"})


@pytest.fixture
def runtime(settings: Settings) -> Runtime:
    return Runtime(settings)


@pytest.fixture
def fake(runtime: Runtime) -> FakeProber:
    assert runtime.fake is not None
    return runtime.fake


@pytest.fixture
def server(runtime: Runtime) -> MCPServer:
    return build_server(runtime)


@pytest.fixture
def certificates_target(server: MCPServer, fake: FakeProber) -> McpTarget:
    """What the profile's conformance suite drives: this server over the seeded probe."""
    return McpTarget(
        server,
        descriptor_path=DESCRIPTOR,
        # A host nobody configured: refused before any connection is made.
        foreign_id="not-configured.example.org:443",
        # A configured host that does not answer: it names no certificate.
        unknown_id="down.example.com:443",
        upstream_calls=lambda: fake.calls,
    )


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
def mcp(server: MCPServer) -> Mcp:
    return Mcp(server)
