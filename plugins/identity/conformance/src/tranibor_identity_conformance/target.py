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
    - `directory`: a directory of the implementation's allow-list that holds at least one user and one group,
      and where the suite may create accounts named `conf-...` (it never deletes: the profile has no deletion,
      it disables them). `foreign_directory`: a directory that exists at the provider (or in its fake) but is
      NOT in the allow-list.
    - `upstream_calls()`: how many requests the implementation has sent to its provider so far; the
      allow-list test requires it not to move on a refused call.
    - `mail_configured`: whether the directory can send mail (`send_setup_email` is then expected to send).
    """

    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    served: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    descriptor: dict[str, Any]
    directory: str
    foreign_directory: str
    upstream_calls: Callable[[], int]
    mail_configured: bool = True


class McpTarget(Target):
    """A `Target` over an in-process `mcp.server.mcpserver.MCPServer`."""

    def __init__(
        self,
        server: Any,
        *,
        descriptor_path: Path | str,
        directory: str,
        foreign_directory: str,
        upstream_calls: Callable[[], int],
        mail_configured: bool = True,
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
            directory=directory,
            foreign_directory=foreign_directory,
            upstream_calls=upstream_calls,
            mail_configured=mail_configured,
        )
