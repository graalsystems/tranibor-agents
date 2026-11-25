# SPDX-License-Identifier: Apache-2.0
"""`servers/cert-watch.yaml` and the server agree: what is served is declared, and all of it is a read."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from mcp import Client
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from tranibor_cert_watch.app import build_app
from tranibor_cert_watch.config import Settings

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "cert-watch.yaml"


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


async def test_every_served_tool_is_declared_and_nothing_else(server: MCPServer) -> None:
    async with Client(server) as client:
        served = {tool.name for tool in (await client.list_tools()).tools}
    assert served == set(operations())


def test_the_server_declares_no_write_at_all() -> None:
    for name, op in operations().items():
        assert op["access"] == "read", name
        assert not {"human_approval", "compensation", "platform_arguments"} & set(op), name
    assert "implements" not in descriptor()
    assert "webhook" not in descriptor()


def test_operation_descriptions_fit_the_schema() -> None:
    assert all(len(op.get("description", "")) <= 500 for op in operations().values())


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/cert-watch-mcp@sha256:[0-9a-f]{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    properties = d["config_schema"]["properties"]
    # One field per URL: `host:port` lists cannot be platform egress, the server enforces them itself.
    assert d["egress"] == {"from_fields": ["kube_api_url", "ct_url"]}
    assert set(d["egress"]["from_fields"]) <= set(properties)
    assert d["secret_fields"] == ["kube_token"]
    assert set(d["secret_fields"]) <= set(properties)
    assert {p["x-env"] for p in properties.values()} == {
        "CERT_WATCH_KUBE_API_URL",
        "CERT_WATCH_KUBE_CA_CERT",
        "CERT_WATCH_KUBE_TOKEN",
        "CERT_WATCH_NAMESPACES",
        "CERT_WATCH_DOMAINS",
        "CERT_WATCH_PROBE_HOSTS",
        "CERT_WATCH_CT_URL",
    }
    assert set(d["config_schema"]["required"]) <= set(properties)


def test_the_declared_fake_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    """For the live eval matrix the platform runs an eval instance with exactly these variables and its own
    token, nothing of the configuration. None is the platform's (`TRANIBOR_*`, `CHOREGOS_*`) nor one a
    configuration field reaches."""
    d = descriptor()
    assert d["fake"] == {"env": {"CERT_WATCH_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without them, the same environment is a real instance nobody configured: it refuses to start.
    with pytest.raises(ValueError, match="CERT_WATCH_KUBE_API_URL"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))
