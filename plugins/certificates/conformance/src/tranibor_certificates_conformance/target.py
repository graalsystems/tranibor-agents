# SPDX-License-Identifier: Apache-2.0
"""What the suite needs to know about the implementation it checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ToolFailed(Exception):
    """A tool answered an error (the server refused, or the source did)."""


@dataclass
class Target:
    """One implementation, seen through its MCP tools.

    - `call(tool, arguments)` returns the tool's structured result or raises `ToolFailed(message)`.
    - `served()` returns {tool name: served input schema}.
    - `descriptor`: the server's `servers/<name>.yaml`.
    - `foreign_id`: an id the implementation must refuse because it is outside what it is configured to look at
      (a host nobody configured, a namespace outside the allow-list). `unknown_id`: an id inside the scope that
      names no certificate.
    - `refusal_is_local`: True when `foreign_id` is refused by the server itself, before any request leaves it
      (the allow-list test then requires `upstream_calls()` not to move); False for a source with no allow-list
      the server can apply to an id (a log).
    - `upstream_calls()`: how many requests the implementation has sent to its source so far.
    - `window_days`: a window in which the fake holds at least one certificate and not all of them.
    """

    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    served: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    descriptor: dict[str, Any]
    foreign_id: str
    unknown_id: str
    upstream_calls: Callable[[], int]
    refusal_is_local: bool = True
    window_days: int = 30


class McpTarget(Target):
    """A `Target` over an in-process `mcp.server.mcpserver.MCPServer`."""

    def __init__(
        self,
        server: Any,
        *,
        descriptor_path: Path | str,
        foreign_id: str,
        unknown_id: str,
        upstream_calls: Callable[[], int],
        refusal_is_local: bool = True,
        window_days: int = 30,
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
            foreign_id=foreign_id,
            unknown_id=unknown_id,
            upstream_calls=upstream_calls,
            refusal_is_local=refusal_is_local,
            window_days=window_days,
        )
