# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult

from tranibor_cert_watch.app import Runtime, build_server
from tranibor_cert_watch.config import Settings
from tranibor_cert_watch.fake import FakeCt, FakeKube, FakeProber

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({"CERT_WATCH_FAKE": "1"})


@pytest.fixture
def fake_kube() -> FakeKube:
    return FakeKube(NOW)


@pytest.fixture
def fake_ct() -> FakeCt:
    return FakeCt(NOW)


@pytest.fixture
def runtime(settings: Settings, fake_kube: FakeKube, fake_ct: FakeCt) -> Runtime:
    """The server's own runtime on the fakes, with a fixed clock so every day count is exact."""
    return Runtime(
        settings,
        kube_transport=httpx.MockTransport(fake_kube),
        ct_transport=httpx.MockTransport(fake_ct),
        prober=FakeProber(NOW),
        clock=lambda: NOW,
    )


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
