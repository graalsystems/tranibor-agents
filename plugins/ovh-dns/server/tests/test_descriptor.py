# SPDX-License-Identifier: Apache-2.0
"""`servers/ovh-dns.yaml` and the server agree: what is served is declared, with its governance."""

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

from tranibor_ovh_dns.app import build_app
from tranibor_ovh_dns.config import Settings

from .conftest import Mcp

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "ovh-dns.yaml"
PROCEDURE = Path(__file__).resolve().parents[2] / "procedures" / "connector.mcp.ovh-dns.md"
APPROVED = {"delete_record", "refresh_zone"}
WRITES = {"create_record", "update_record", "delete_record", "refresh_zone"}


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
        assert set(op.get("platform_arguments", [])) == ({"action_key"} if name == "create_record" else set()), name
        for argument in op.get("platform_arguments", []):
            # Served, and optional: the platform validates the agent's arguments before it fills its own.
            assert argument in served[name].input_schema.get("properties", {})
            assert argument not in served[name].input_schema.get("required", [])


def test_the_writes_others_can_feel_need_a_person() -> None:
    assert {n for n, op in operations().items() if op.get("human_approval")} == APPROVED
    assert all(op["access"] == "write" for n, op in operations().items() if op.get("human_approval"))
    assert not any("compensation" in op for op in operations().values() if op["access"] == "read")


def test_compensations_name_declared_operations() -> None:
    compensations = {n: op["compensation"]["operation"] for n, op in operations().items() if "compensation" in op}
    assert compensations == {"update_record": "update_record", "delete_record": "create_record"}
    assert set(compensations.values()) <= set(operations())


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/ovh-dns-mcp@sha256:0{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    assert d["egress"] == {"hosts": ["eu.api.ovh.com", "ca.api.ovh.com", "api.us.ovhcloud.com"]}
    properties = d["config_schema"]["properties"]
    assert set(d["config_schema"]["required"]) == set(properties)
    assert set(d["secret_fields"]) == {"application_secret", "consumer_key"}
    assert {p["x-env"] for p in properties.values()} == {
        "OVH_DNS_ENDPOINT",
        "OVH_DNS_APPLICATION_KEY",
        "OVH_DNS_APPLICATION_SECRET",
        "OVH_DNS_CONSUMER_KEY",
        "OVH_DNS_ZONES",
    }
    assert properties["endpoint"]["enum"] == ["ovh-eu", "ovh-ca", "ovh-us"]


def test_the_egress_hosts_are_the_ones_the_client_calls() -> None:
    from urllib.parse import urlparse

    from tranibor_ovh_dns.config import ENDPOINTS

    assert {urlparse(url).hostname for url in ENDPOINTS.values()} == set(descriptor()["egress"]["hosts"])
    assert set(ENDPOINTS) == set(descriptor()["config_schema"]["properties"]["endpoint"]["enum"])


def test_the_procedure_describes_every_secret_field() -> None:
    front = yaml.safe_load(PROCEDURE.read_text().split("---")[1])
    assert set(descriptor()["secret_fields"]) <= set(front["fields"])
    assert set(front["fields"]) <= set(descriptor()["config_schema"]["properties"])


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"OVH_DNS_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without it, the same environment is a real account nobody configured: it refuses to start.
    with pytest.raises(ValueError, match="OVH_DNS_"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))


def _render(template: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
    env = SandboxedEnvironment(undefined=StrictUndefined)
    return {key: env.from_string(value).render(result=result) for key, value in template.items()}


async def test_the_declared_compensations_undo_their_write(mcp: Mcp) -> None:
    """Render each compensation's arguments with the write's result, as the platform does (every argument a
    string through a sandboxed Jinja with undefined names refused), and run it."""

    async def one(sub: str, field_type: str) -> dict[str, Any]:
        return (await mcp.call("list_records", zone="example.com", sub_domain=sub, field_type=field_type))["records"][0]

    before = await one("shop", "A")
    update = await mcp.call("update_record", zone="example.com", record_id=before["id"], target="192.0.2.99", ttl=300)
    undo = operations()["update_record"]["compensation"]
    await mcp.call(undo["operation"], **_render(undo["arguments"], update))
    assert await one("shop", "A") == before

    old = await one("old-api", "CNAME")
    deleted = await mcp.call("delete_record", zone="example.com", record_id=old["id"])
    recreate = operations()["delete_record"]["compensation"]
    restored = await mcp.call(recreate["operation"], **_render(recreate["arguments"], deleted))
    assert restored["created"] is True
    now = await one("old-api", "CNAME")
    assert {k: now[k] for k in ("field_type", "sub_domain", "target", "ttl")} == {
        k: old[k] for k in ("field_type", "sub_domain", "target", "ttl")
    }
    # Undoing twice is harmless: the record exists, nothing is written.
    again = await mcp.call(recreate["operation"], **_render(recreate["arguments"], deleted))
    assert again["created"] is False


async def test_a_compensation_of_a_default_ttl_record_renders_as_zero(mcp: Mcp, fake: Any) -> None:
    """A record whose TTL is the zone's default has `ttl` null at OVHcloud: it travels as 0."""
    shop = (await mcp.call("list_records", zone="example.com", sub_domain="shop"))["records"][0]
    await mcp.call("update_record", zone="example.com", record_id=shop["id"], ttl=0)
    update = await mcp.call("update_record", zone="example.com", record_id=shop["id"], ttl=300)
    assert update["previous"]["ttl"] == 0
    undo = operations()["update_record"]["compensation"]
    await mcp.call(undo["operation"], **_render(undo["arguments"], update))
    assert fake.records["example.com"][shop["id"]]["ttl"] is None
