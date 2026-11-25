# SPDX-License-Identifier: Apache-2.0
"""`servers/argocd.yaml` and the server agree: what is served is declared, with its governance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from mcp import Client
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from tranibor_argocd.app import build_app
from tranibor_argocd.config import Settings

from .conftest import Mcp

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "argocd.yaml"
NEEDS_A_PERSON = {"sync_application", "terminate_operation", "rollback_application"}
WRITES = NEEDS_A_PERSON | {"refresh_application"}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


async def test_every_served_tool_is_declared_and_nothing_else(server: MCPServer) -> None:
    async with Client(server) as client:
        served = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(served) == set(operations())
    for name, op in operations().items():
        assert op["access"] == ("write" if name in WRITES else "read"), name
        schema = served[name].input_schema
        wanted = {"action_key"} if name == "sync_application" else set()
        assert set(op.get("platform_arguments", [])) == wanted, name
        for argument in wanted:
            assert argument in schema.get("properties", {}), name
            assert argument not in schema.get("required", []), name


def test_the_impactful_writes_need_a_person_and_refresh_does_not() -> None:
    approved = {name for name, op in operations().items() if op.get("human_approval")}
    assert approved == NEEDS_A_PERSON
    assert "human_approval" not in operations()["refresh_application"]


def test_the_compensation_of_a_rollback_is_a_rollback() -> None:
    compensations = {name: op["compensation"] for name, op in operations().items() if "compensation" in op}
    assert set(compensations) == {"rollback_application"}
    assert compensations["rollback_application"]["operation"] == "rollback_application"


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/argocd-mcp@sha256:0{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    assert d["egress"] == {"from_fields": ["base_url"]}
    properties = d["config_schema"]["properties"]
    assert d["secret_fields"] == ["token"]
    assert set(d["config_schema"]["required"]) == {"base_url", "token", "projects"}
    assert {p["x-env"] for p in properties.values()} == {
        "ARGOCD_BASE_URL",
        "ARGOCD_TOKEN",
        "ARGOCD_PROJECTS",
        "ARGOCD_CA_CERT",
    }


def test_the_declared_fake_is_what_switches_the_server_to_its_fake() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"ARGOCD_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    with pytest.raises(ValueError, match=r"ARGOCD_BASE_URL|https"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))


async def test_the_declared_compensation_undoes_the_rollback(mcp: Mcp) -> None:
    """Render the compensation's arguments with the write's result, as the platform does (every string
    through a sandboxed Jinja, so each argument arrives as a string), and run it."""
    env = SandboxedEnvironment(undefined=StrictUndefined)
    undo = operations()["rollback_application"]["compensation"]
    result = await mcp.call("rollback_application", name="shop-web", history_id=1)
    arguments: dict[str, Any] = {k: env.from_string(v).render(result=result) for k, v in undo["arguments"].items()}
    assert arguments["history_id"] == "2" and arguments["prune"] == "false"
    arguments["history_id"] = int(arguments["history_id"])
    arguments["prune"] = arguments["prune"] == "true"
    back = await mcp.call(undo["operation"], **arguments)
    assert back["revision"] == result["previous_revision"]
