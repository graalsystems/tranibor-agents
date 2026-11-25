# SPDX-License-Identifier: Apache-2.0
"""`servers/dns-godaddy.yaml` and the server agree. The profile's own checks (governance floor, compensation by
replay) are in the conformance suite; this is what is specific to this descriptor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from starlette.testclient import TestClient

from tranibor_dns_godaddy.app import build_app
from tranibor_dns_godaddy.config import ENVIRONMENTS, Settings

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = ROOT / "servers" / "dns-godaddy.yaml"
PROCEDURE = ROOT / "procedures" / "connector.mcp.dns-godaddy.md"


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
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/dns-godaddy-mcp@sha256:0{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    assert d["egress"] == {"hosts": ["api.godaddy.com", "api.ote-godaddy.com"]}
    properties = d["config_schema"]["properties"]
    assert set(d["config_schema"]["required"]) == set(properties)
    assert set(d["secret_fields"]) == {"token"}
    assert {p["x-env"] for p in properties.values()} == {
        "DNS_GODADDY_ENVIRONMENT",
        "DNS_GODADDY_TOKEN",
        "DNS_GODADDY_ZONES",
    }
    assert properties["environment"]["enum"] == ["production", "ote"]
    # Every field tells the administrator where to find the value (ADR 0224 §5); the config is flat.
    assert all(p.get("x-help") for p in properties.values())
    assert all(p["type"] in ("string", "array") for p in properties.values())


def test_the_egress_hosts_are_the_ones_the_client_calls() -> None:
    assert {urlparse(url).hostname for url in ENVIRONMENTS.values()} == set(descriptor()["egress"]["hosts"])
    assert set(ENVIRONMENTS) == set(descriptor()["config_schema"]["properties"]["environment"]["enum"])


def test_the_procedure_describes_every_secret_field() -> None:
    front = yaml.safe_load(PROCEDURE.read_text().split("---")[1])
    assert set(descriptor()["secret_fields"]) <= set(front["fields"])
    assert set(front["fields"]) <= set(descriptor()["config_schema"]["properties"])


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"DNS_GODADDY_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without it, the same environment is a real account nobody configured: it refuses to start.
    with pytest.raises(ValueError, match="DNS_GODADDY_"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))
