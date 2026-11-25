# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from tranibor_certificates_conformance import McpTarget

from tranibor_certificates_cert_manager.app import Runtime, build_server
from tranibor_certificates_cert_manager.config import Settings
from tranibor_certificates_cert_manager.fake import FakeKube

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "certificates-cert-manager.yaml"
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
#: The seeded namespaces; anything else (kube-system) is outside the allow-list the target runs with.
NAMESPACES = "shop,mail,legacy"


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env(
        {"CERTIFICATES_CERT_MANAGER_FAKE": "1", "CERTIFICATES_CERT_MANAGER_NAMESPACES": NAMESPACES}
    )


@pytest.fixture
def runtime(settings: Settings) -> Runtime:
    """The server's own runtime on the fake, with a fixed clock so every day count is exact."""
    return Runtime(settings, clock=lambda: NOW)


@pytest.fixture
def fake(runtime: Runtime) -> FakeKube:
    assert runtime.fake is not None
    return runtime.fake


@pytest.fixture
def server(runtime: Runtime) -> MCPServer:
    return build_server(runtime)


@pytest.fixture
def certificates_target(server: MCPServer, fake: FakeKube) -> McpTarget:
    """What the profile's conformance suite drives: this server over the cert-manager fake."""
    return McpTarget(
        server,
        descriptor_path=DESCRIPTOR,
        # A namespace outside the allow-list: refused before any request leaves.
        foreign_id="kube-system/anything",
        unknown_id="shop/does-not-exist",
        upstream_calls=lambda: len(fake.requests),
    )


class Mcp:
    """Calls a tool in-process through the MCP protocol and returns its structured result.

    One client session per call: an async fixture's teardown runs in another task, which the
    session's task group refuses."""

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
