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
    - `site_id`: a site the instance serves and the suite may read.
    - `device_id`: an ONLINE device of that site (the reads look at it).
    - `offline_device_id`: a device that is not ONLINE, or "" when the site has none.
    - `restart_device_id`: a device the suite may restart.
    - `poe_device_id`, `poe_port`: a switch and a PoE port the suite may power-cycle.
    - `client_id`: a client the suite may block and unblock, when the provider supports it.
    - `missing_id`: a well-formed id that exists nowhere.
    - `upstream_calls()`: how many requests the implementation has sent to its provider so far; a tool that
      answers `not_supported` requires it not to move.
    """

    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    served: Callable[[], Awaitable[dict[str, dict[str, Any]]]]
    descriptor: dict[str, Any]
    site_id: str
    device_id: str
    offline_device_id: str
    restart_device_id: str
    poe_device_id: str
    poe_port: int
    client_id: str
    missing_id: str
    upstream_calls: Callable[[], int]


class McpTarget(Target):
    """A `Target` over an in-process `mcp.server.mcpserver.MCPServer`."""

    def __init__(
        self,
        server: Any,
        *,
        descriptor_path: Path | str,
        site_id: str,
        device_id: str,
        offline_device_id: str,
        restart_device_id: str,
        poe_device_id: str,
        poe_port: int,
        client_id: str,
        missing_id: str,
        upstream_calls: Callable[[], int],
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
            site_id=site_id,
            device_id=device_id,
            offline_device_id=offline_device_id,
            restart_device_id=restart_device_id,
            poe_device_id=poe_device_id,
            poe_port=poe_port,
            client_id=client_id,
            missing_id=missing_id,
            upstream_calls=upstream_calls,
        )
