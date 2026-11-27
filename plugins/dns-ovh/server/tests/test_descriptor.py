# SPDX-License-Identifier: Apache-2.0
"""`servers/dns-ovh.yaml` and the server agree. The profile's own checks (governance floor, compensation by
replay) are in the conformance suite; this is what is specific to this descriptor."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from jsonschema import Draft202012Validator
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from tranibor_dns_ovh.app import build_app
from tranibor_dns_ovh.config import ENDPOINTS, Settings

from .conftest import Mcp

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = ROOT / "servers" / "dns-ovh.yaml"
PROFILE = ROOT.parent / "dns" / "profiles" / "dns.yaml"
PROCEDURE = ROOT / "procedures" / "connector.mcp.dns-ovh.md"


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def test_the_descriptor_implements_the_profile_and_the_plugin_requires_it() -> None:
    assert descriptor()["implements"] == ["dns/dns@1"]
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert [p["name"] for p in plugin["requires"]["plugins"]] == ["dns"]


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/dns-ovh-mcp@sha256:0{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    assert d["egress"] == {"hosts": ["eu.api.ovh.com", "ca.api.ovh.com", "api.us.ovhcloud.com"]}
    properties = d["config_schema"]["properties"]
    assert set(d["config_schema"]["required"]) == set(properties)
    assert set(d["secret_fields"]) == {"application_secret", "consumer_key"}
    assert {p["x-env"] for p in properties.values()} == {
        "DNS_OVH_ENDPOINT",
        "DNS_OVH_APPLICATION_KEY",
        "DNS_OVH_APPLICATION_SECRET",
        "DNS_OVH_CONSUMER_KEY",
        "DNS_OVH_ZONES",
    }
    assert properties["endpoint"]["enum"] == ["ovh-eu", "ovh-ca", "ovh-us"]
    # Every field tells the administrator where to find the value (ADR 0224 §5); the config is flat.
    assert all(p.get("x-help") for p in properties.values())
    assert all(p["type"] in ("string", "array") for p in properties.values())


def test_the_egress_hosts_are_the_ones_the_client_calls() -> None:
    assert {urlparse(url).hostname for url in ENDPOINTS.values()} == set(descriptor()["egress"]["hosts"])
    assert set(ENDPOINTS) == set(descriptor()["config_schema"]["properties"]["endpoint"]["enum"])


def test_the_procedure_describes_every_secret_field() -> None:
    front = yaml.safe_load(PROCEDURE.read_text().split("---")[1])
    assert set(descriptor()["secret_fields"]) <= set(front["fields"])
    assert set(front["fields"]) <= set(descriptor()["config_schema"]["properties"])


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"DNS_OVH_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without it, the same environment is a real account nobody configured: it refuses to start.
    with pytest.raises(ValueError, match="DNS_OVH_"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))


# ---- the declared schemas (ADR 0224): what `plugin validate` compares with the profile --------------------


def _profile() -> dict[str, Any]:
    loaded = yaml.safe_load(PROFILE.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _with_defs(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """The profile's schema as an operation carries it: with the `$defs` it points to, and only those."""
    wanted: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.rsplit("/", 1)[-1] not in wanted:
                wanted.add(ref.rsplit("/", 1)[-1])
                walk(defs[ref.rsplit("/", 1)[-1]])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    out = copy.deepcopy(schema)
    walk(out)
    if wanted:
        out["$defs"] = {name: copy.deepcopy(defs[name]) for name in sorted(wanted)}
    return out


def test_every_operation_declares_the_profiles_schemas() -> None:
    profile = _profile()
    tools = {t["name"]: t for t in profile["tools"]}
    operations = {o["name"]: o for o in descriptor()["operations"]}
    assert set(operations) == set(tools)
    for name, operation in operations.items():
        assert operation["input_schema"] == _with_defs(tools[name]["input"], profile["$defs"]), name
        assert operation["output_schema"] == _with_defs(tools[name]["output"], profile["$defs"]), name


async def test_the_served_tools_take_what_the_profile_takes(server: MCPServer) -> None:
    from mcp import Client

    profile = _profile()
    async with Client(server) as client:
        served = {t.name: t.input_schema for t in (await client.list_tools()).tools}
    for tool in profile["tools"]:
        given = served[tool["name"]]
        allowed = set(given.get("properties", {})) - set(tool.get("platform_arguments", []))
        assert set(tool["input"].get("properties", {})) <= allowed, tool["name"]
        assert set(given.get("required", [])) <= set(tool["input"].get("required", [])), tool["name"]


async def test_what_the_tools_return_matches_the_declared_output_schemas(mcp: Mcp) -> None:
    schemas = {o["name"]: o["output_schema"] for o in descriptor()["operations"]}

    async def checked(tool: str, **arguments: Any) -> dict[str, Any]:
        result = await mcp.call(tool, **arguments)
        errors = [e.message for e in Draft202012Validator(schemas[tool]).iter_errors(result)]
        assert not errors, (tool, errors, result)
        return result

    await checked("get_provider")
    await checked("list_zones")
    await checked("get_zone", zone="example.com")
    listed = await checked("list_records", zone="example.com", type="A")
    record = listed["records"][0]
    await checked("get_record", zone="example.com", id=record["id"])
    await checked("export_zone", zone="example.com")
    await checked("plan_changes", zone="example.com", desired=[{"type": "A", "name": "fresh", "value": "192.0.2.7"}])
    created = await checked("create_record", zone="example.com", name="fresh", type="A", value="192.0.2.7")
    updated = await checked("update_record", zone="example.com", id=created["id"], ttl=3600)
    await checked("delete_record", zone="example.com", id=updated["id"])
    await checked("publish", zone="example.com")
