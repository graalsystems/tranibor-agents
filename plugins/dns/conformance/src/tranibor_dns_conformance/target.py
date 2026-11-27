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
    - `zone`: a zone of the implementation's allow-list the suite may write records in (it creates records
      named `conf-...` and removes them). `foreign_zone`: a zone that exists at the provider (or in its fake)
      but is NOT in the allow-list.
    - `upstream_calls()`: how many requests the implementation has sent to its provider so far; the
      allow-list test requires it not to move on a refused call.
    - `stages_changes`: True for a provider that holds writes until `publish`, False for one that applies
      them at once (its `publish` is then a no-op).
    - `ttl`, `other_ttl`: two TTLs the provider accepts.
    """

    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    served: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    descriptor: dict[str, Any]
    zone: str
    foreign_zone: str
    upstream_calls: Callable[[], int]
    stages_changes: bool
    ttl: int = 3600
    other_ttl: int = 7200


class McpTarget(Target):
    """A `Target` over an in-process `mcp.server.mcpserver.MCPServer`."""

    def __init__(
        self,
        server: Any,
        *,
        descriptor_path: Path | str,
        zone: str,
        foreign_zone: str,
        upstream_calls: Callable[[], int],
        stages_changes: bool,
        ttl: int = 3600,
        other_ttl: int = 7200,
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
            zone=zone,
            foreign_zone=foreign_zone,
            upstream_calls=upstream_calls,
            stages_changes=stages_changes,
            ttl=ttl,
            other_ttl=other_ttl,
        )
