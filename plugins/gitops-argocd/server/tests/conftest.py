# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from tranibor_gitops_conformance import McpTarget

from tranibor_gitops_argocd.app import Runtime, build_server
from tranibor_gitops_argocd.config import Settings
from tranibor_gitops_argocd.fake import FakeArgoCD


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({"GITOPS_ARGOCD_FAKE": "1"})


@pytest.fixture
def runtime(settings: Settings) -> Runtime:
    return Runtime(settings)


@pytest.fixture
def fake(runtime: Runtime) -> FakeArgoCD:
    assert runtime.fake is not None
    return runtime.fake


@pytest.fixture
def server(runtime: Runtime) -> MCPServer:
    return build_server(runtime)


class Mcp:
    """Calls a tool in-process through the MCP protocol and returns its structured result.

    One client session per call: an async fixture's teardown runs in another task, which the
    session's task group refuses."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server

    async def raw(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        async with Client(self.server) as client:
            return await client.call_tool(name, arguments)

    async def tool_names(self) -> set[str]:
        async with Client(self.server) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

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


DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "gitops-argocd.yaml"


@pytest.fixture
def gitops_target(server: MCPServer, fake: FakeArgoCD) -> McpTarget:
    return McpTarget(
        server,
        descriptor_path=DESCRIPTOR,
        scope="shop",
        foreign_scope="platform",
        drifted_app="shop-api",
        history_app="shop-web",
        auto_sync_app="billing-ledger",
        foreign_app="platform-dns",
        upstream_writes=lambda: fake.writes,
        running_app="shop-jobs",
        denied_app="billing-reports",
    )
