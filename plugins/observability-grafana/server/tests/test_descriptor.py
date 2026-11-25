# SPDX-License-Identifier: Apache-2.0
"""`servers/observability-grafana.yaml` and the server agree: what is served is declared, with its governance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from mcp import Client
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from tranibor_observability_grafana.app import Runtime, build_app, build_server
from tranibor_observability_grafana.config import Settings

from .conftest import NOW, Mcp, make_runtime

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "observability-grafana.yaml"
READS = {
    "get_provider",
    "list_firing_alerts",
    "get_alert_rule",
    "list_silences",
    "list_sources",
    "query_metrics",
    "query_logs",
    "list_dashboards",
    "get_dashboard",
    "list_annotations",
}
ALWAYS_A_PERSON = {"create_silence"}
PLATFORM_ARGUMENTS = {"create_silence": {"action_key"}, "add_annotation": {"action_key"}}
COMPENSATIONS = {
    "create_silence": {"operation": "expire_silence", "arguments": {"silence_id": "{{ result.silence_id }}"}},
}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


def server() -> MCPServer:
    return build_server(make_runtime())


async def test_every_served_tool_is_declared_and_nothing_else() -> None:
    async with Client(server()) as client:
        served = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(served) == set(operations())
    for name, op in operations().items():
        assert op["access"] in ("read", "write"), name
        assert (op["access"] == "read") == (name in READS), name
        schema = served[name].input_schema
        assert set(op.get("platform_arguments", [])) == PLATFORM_ARGUMENTS.get(name, set()), name
        for argument in op.get("platform_arguments", []):
            # Served, and optional: the platform validates the agent's arguments before it fills its own.
            assert argument in schema.get("properties", {}), name
            assert argument not in schema.get("required", []), name


def test_only_the_silence_needs_a_person() -> None:
    approved = {name for name, op in operations().items() if op.get("human_approval")}
    assert approved == ALWAYS_A_PERSON
    assert all(operations()[name]["access"] == "write" for name in approved)


def test_the_compensation_names_a_declared_operation() -> None:
    compensations = {name: op["compensation"] for name, op in operations().items() if "compensation" in op}
    assert compensations == COMPENSATIONS
    assert all(c["operation"] in operations() for c in compensations.values())


def test_the_agent_may_not_call_what_the_platform_alone_undoes() -> None:
    agent = yaml.safe_load((DESCRIPTOR.parents[2] / "observability" / "agents" / "alert-triage.yaml").read_text())
    tools = set(agent["agent"]["spec"]["mcp_servers"][0]["tools"])
    assert tools <= set(operations())
    assert tools == READS | {"create_silence", "add_annotation"}


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(
        r"ghcr\.io/graalsystems/tranibor-agents/observability-grafana-mcp@sha256:[0-9a-f]{64}", d["image"]
    )
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    assert d["egress"] == {"from_fields": ["base_url"]}
    properties = d["config_schema"]["properties"]
    assert set(d["secret_fields"]) == {"token"} <= set(properties)
    assert {p["x-env"] for p in properties.values()} == {
        "GRAFANA_BASE_URL",
        "GRAFANA_TOKEN",
        "GRAFANA_ORG_ID",
        "GRAFANA_DATASOURCES",
        "GRAFANA_MAX_RANGE_HOURS",
        "GRAFANA_MAX_LINES",
        "GRAFANA_MAX_SILENCE_MINUTES",
    }
    # The schema's ceilings are the server's.
    assert properties["max_range_hours"]["maximum"] == 24
    assert properties["max_lines"]["maximum"] == 1000
    assert properties["max_silence_minutes"]["maximum"] == 1440


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    """`fake` (core #1372): for the live eval matrix the platform runs an eval instance of the image with
    exactly these variables and its own token, nothing of the configuration."""
    d = descriptor()
    assert d["fake"] == {"env": {"OBSERVABILITY_GRAFANA_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    configurable = {p["x-env"] for p in d["config_schema"]["properties"].values()}
    assert not variables & configurable
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without them, the same environment is a real stack nobody configured: it refuses to start.
    try:
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))
    except ValueError as refused:
        assert "GRAFANA_BASE_URL" in str(refused)
    else:
        raise AssertionError("a real stack without its URL must not start")


async def test_the_declared_compensation_undoes_its_write(runtime: Runtime, mcp: Mcp) -> None:
    """Render the compensation's arguments with the write's result, as the platform does (every string
    through a sandboxed Jinja with undefined names refused), and run it."""

    def render(template: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
        env = SandboxedEnvironment(undefined=StrictUndefined)
        return {key: env.from_string(value).render(result=result) for key, value in template.items()}

    created = await mcp.call(
        "create_silence",
        matchers={"alertname": "FlappingCPU", "service": "search"},
        duration_minutes=30,
        comment="noise",
    )
    undo = operations()["create_silence"]["compensation"]
    undone = await mcp.call(undo["operation"], **render(undo["arguments"], created))
    assert undone["expired"] is True
    silences = await mcp.call("list_silences", state="active")
    assert created["silence_id"] not in {s["id"] for s in silences["silences"]}
    assert NOW  # the fake is seeded relative to a fixed clock
