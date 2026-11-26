# SPDX-License-Identifier: Apache-2.0
"""`servers/lan-unifi.yaml` and the server agree. The profile's own checks (governance floor, compensation
rendering) are in the conformance suite; this is what is specific to this descriptor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from tranibor_lan_unifi.app import build_app
from tranibor_lan_unifi.config import Settings

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = ROOT / "servers" / "lan-unifi.yaml"
PROCEDURE = ROOT / "procedures" / "connector.mcp.lan-unifi.md"


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def test_the_descriptor_implements_the_profile_and_the_plugin_requires_it() -> None:
    assert descriptor()["implements"] == ["lan/lan@1"]
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert plugin["requires"]["plugins"] == [{"name": "lan", "version": ">=0.1.0,<1.0.0"}]


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/lan-unifi-mcp@sha256:0{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    properties = d["config_schema"]["properties"]
    assert set(d["config_schema"]["required"]) <= set(properties)
    assert set(d["secret_fields"]) == {"api_key", "site_manager_api_key"}
    assert set(d["egress"]["from_fields"]) == {"base_url", "site_manager_url"}
    assert all(
        p.get("title") and p.get("description") and p.get("x-help") and p.get("x-link") for p in properties.values()
    )
    assert {p["x-env"] for p in properties.values()} == {
        "UNIFI_BASE_URL",
        "UNIFI_API_KEY",
        "UNIFI_CA_CERTIFICATE",
        "UNIFI_SITE_MANAGER_URL",
        "UNIFI_SITE_MANAGER_API_KEY",
    }


def test_the_procedure_describes_every_secret_field() -> None:
    front = yaml.safe_load(PROCEDURE.read_text().split("---")[1])
    assert set(descriptor()["secret_fields"]) <= set(front["fields"])
    assert set(front["fields"]) <= set(descriptor()["config_schema"]["properties"])


def test_what_the_official_api_cannot_do_is_declared_with_its_governance_all_the_same() -> None:
    operations = {op["name"]: op for op in descriptor()["operations"]}
    assert operations["block_client"]["human_approval"] is True
    assert operations["block_client"]["compensation"]["operation"] == "unblock_client"
    assert operations["unblock_client"]["compensation"]["operation"] == "block_client"
    assert operations["upgrade_firmware"]["human_approval"] is True


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"LAN_UNIFI_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    with pytest.raises(ValueError, match="UNIFI_"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))


def test_the_descriptor_schemas_are_the_profile_s() -> None:
    """The validator proves acceptance; this pins the stronger fact that the server's schemas are copies."""
    profile = yaml.safe_load((ROOT.parent / "lan" / "profiles" / "lan.yaml").read_text())
    operations = {op["name"]: op for op in descriptor()["operations"]}
    for tool in profile["tools"]:
        declared = operations[tool["name"]]
        for ours, theirs in (("input_schema", "input"), ("output_schema", "output")):
            schema = {k: v for k, v in declared[ours].items() if k != "$defs"}
            assert schema == tool[theirs], (tool["name"], ours)
            for name, body in declared[ours].get("$defs", {}).items():
                assert body == profile["$defs"][name], (tool["name"], ours, name)
