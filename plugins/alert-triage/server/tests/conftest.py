# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult

from tranibor_alert_triage.app import Runtime, build_server
from tranibor_alert_triage.config import Settings
from tranibor_alert_triage.fake import FakeGrafana

#: The fake stack is seeded relative to this moment.
NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def make_runtime(**env: str) -> Runtime:
    return Runtime(Settings.from_env({"ALERT_TRIAGE_FAKE": "1", **env}), clock=clock)


@pytest.fixture
def runtime() -> Runtime:
    return make_runtime()


@pytest.fixture
def fake(runtime: Runtime) -> FakeGrafana:
    assert runtime.fake is not None
    return runtime.fake


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
def mcp(runtime: Runtime) -> Mcp:
    return Mcp(build_server(runtime))


@pytest.fixture
def mcp_for() -> Callable[..., Mcp]:
    """An MCP over a fake stack configured with other settings (caps, allow-list, token)."""

    def build(**env: str) -> Mcp:
        return Mcp(build_server(make_runtime(**env)))

    return build
