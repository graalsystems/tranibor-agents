# SPDX-License-Identifier: Apache-2.0
"""What the suite needs to know about the implementation it checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ToolFailed(Exception):
    """A tool answered an error (the server refused, or the provider did)."""


@dataclass
class Target:
    """One implementation, seen through its MCP tools.

    - `call(tool, arguments)` returns the tool's structured result or raises `ToolFailed(message)`.
    - `served()` returns {tool name: served input schema}.
    - `descriptor`: the server's `servers/<name>.yaml`.
    - `scope` / `foreign_scope`: a scope of the implementation's allow-list, and one that exists at the provider
      (or in its fake) but is NOT in the allow-list.
    - `drifted_app`: an application of `scope` that is OutOfSync, has auto-sync off and no operation running; the
      suite syncs it (it must be a throwaway one on a live controller).
    - `history_app`: an application of `scope` with auto-sync off and at least two deployments in its history;
      the suite rolls it back and undoes that.
    - `auto_sync_app`: an application of `scope` with auto-sync on; the suite pauses it and resumes it.
    - `foreign_app`: an application of `foreign_scope`.
    - `running_app`, `denied_app`: optional; an application with an operation running, and one that a window
      forbids to sync now. The suite checks a sync is refused on them when given.
    - `upstream_writes()`: how many requests that change something the implementation has sent to its provider
      so far; the allow-list test requires it not to move on a refused call.
    """

    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    served: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    descriptor: dict[str, Any]
    scope: str
    foreign_scope: str
    drifted_app: str
    history_app: str
    auto_sync_app: str
    foreign_app: str
    upstream_writes: Callable[[], int]
    running_app: str | None = None
    denied_app: str | None = None


class McpTarget(Target):
    """A `Target` over an in-process `mcp.server.mcpserver.MCPServer`."""

    def __init__(
        self,
        server: Any,
        *,
        descriptor_path: Path | str,
        scope: str,
        foreign_scope: str,
        drifted_app: str,
        history_app: str,
        auto_sync_app: str,
        foreign_app: str,
        upstream_writes: Callable[[], int],
        running_app: str | None = None,
        denied_app: str | None = None,
    ) -> None:
        import yaml
        from mcp import Client

        async def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            async with Client(server) as client:
                result = await client.call_tool(tool, arguments)
            if result.is_error:
                first = result.content[0] if result.content else ""
                raise ToolFailed(str(getattr(first, "text", first)))
            assert result.structured_content is not None, f"{tool} returned no structured content"
            return dict(result.structured_content)

        async def served() -> dict[str, dict[str, Any]]:
            async with Client(server) as client:
                return {t.name: dict(t.input_schema) for t in (await client.list_tools()).tools}

        loaded = yaml.safe_load(Path(descriptor_path).read_text())
        super().__init__(
            call=call,
            served=served,
            descriptor=loaded,
            scope=scope,
            foreign_scope=foreign_scope,
            drifted_app=drifted_app,
            history_app=history_app,
            auto_sync_app=auto_sync_app,
            foreign_app=foreign_app,
            upstream_writes=upstream_writes,
            running_app=running_app,
            denied_app=denied_app,
        )
