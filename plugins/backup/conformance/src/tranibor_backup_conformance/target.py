# SPDX-License-Identifier: Apache-2.0
"""What the suite needs to know about the implementation it checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ToolFailed(Exception):
    """A tool answered an error (the server refused, or the backup system did)."""


@dataclass
class Target:
    """One implementation, seen through its MCP tools.

    - `call(tool, arguments)` returns the tool's structured result or raises `ToolFailed(message)`.
    - `served()` returns {tool name: served input schema}.
    - `descriptor`: the server's `servers/<name>.yaml`.
    - `schedule`: a schedule the suite may trigger (it writes one more backup under it).
    - `backup`, `source`: a restorable backup and a part of it the suite may restore into an isolated target
      (it restores, then cleans up; it writes nowhere else).
    - `unrestorable_backup`: a backup that is NOT restorable (failed, say); `None` when the system has none.
    - `target_prefix`: what every target the implementation derives starts with (its allowed rule).
    - `outside_targets`: targets that exist (or could) but are outside the allowed rule, which must be refused.
    - `upstream_calls()`: how many requests the implementation has sent to its backup system so far; the
      refusal test requires it not to move on a refused call.
    """

    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    served: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    descriptor: dict[str, Any]
    schedule: str
    backup: str
    source: str
    target_prefix: str
    upstream_calls: Callable[[], int]
    unrestorable_backup: str | None = None
    outside_targets: list[str] = field(default_factory=lambda: ["kube-system", "default", "../etc"])


class McpTarget(Target):
    """A `Target` over an in-process `mcp.server.mcpserver.MCPServer`."""

    def __init__(
        self,
        server: Any,
        *,
        descriptor_path: Path | str,
        schedule: str,
        backup: str,
        source: str,
        target_prefix: str,
        upstream_calls: Callable[[], int],
        unrestorable_backup: str | None = None,
        outside_targets: list[str] | None = None,
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
            schedule=schedule,
            backup=backup,
            source=source,
            target_prefix=target_prefix,
            upstream_calls=upstream_calls,
            unrestorable_backup=unrestorable_backup,
            outside_targets=outside_targets if outside_targets is not None else ["kube-system", "default", "../etc"],
        )
