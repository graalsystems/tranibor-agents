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
    - `alert_labels`: the exact labels of ONE alert that is firing and not silenced, `alertname` included; the
      suite silences it (briefly) and expires the silence. `rule_id`: the rule that alert comes from.
    - `metrics_source`, `logs_source`: ids of sources of the allow-list; `metrics_expr`, `logs_expr`: expressions
      that answer something over [`start`, `end`], a window the provider accepts.
    - `foreign_source`: a source that exists at the provider (or in its fake) but is NOT in the allow-list.
    - `foreign_silence_id`: a silence a person made, which the server must refuse to expire (None to skip).
    - `upstream_calls()`: how many requests the implementation has sent to its provider so far; the allow-list
      test requires it not to move on a refused call.
    """

    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    served: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    descriptor: dict[str, Any]
    alert_labels: dict[str, str]
    rule_id: str
    metrics_source: str
    logs_source: str
    metrics_expr: str
    logs_expr: str
    start: str
    end: str
    foreign_source: str
    upstream_calls: Callable[[], int]
    foreign_silence_id: str | None = None


class McpTarget(Target):
    """A `Target` over an in-process `mcp.server.mcpserver.MCPServer`."""

    def __init__(
        self,
        server: Any,
        *,
        descriptor_path: Path | str,
        alert_labels: dict[str, str],
        rule_id: str,
        metrics_source: str,
        logs_source: str,
        metrics_expr: str,
        logs_expr: str,
        start: str,
        end: str,
        foreign_source: str,
        upstream_calls: Callable[[], int],
        foreign_silence_id: str | None = None,
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
            alert_labels=alert_labels,
            rule_id=rule_id,
            metrics_source=metrics_source,
            logs_source=logs_source,
            metrics_expr=metrics_expr,
            logs_expr=logs_expr,
            start=start,
            end=end,
            foreign_source=foreign_source,
            upstream_calls=upstream_calls,
            foreign_silence_id=foreign_silence_id,
        )
