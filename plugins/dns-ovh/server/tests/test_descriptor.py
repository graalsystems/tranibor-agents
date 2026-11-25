# SPDX-License-Identifier: Apache-2.0
"""`servers/dns-ovh.yaml` and the server agree. The profile's own checks (governance floor, compensation by
replay) are in the conformance suite; this is what is specific to this descriptor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from starlette.testclient import TestClient

from tranibor_dns_ovh.app import build_app
from tranibor_dns_ovh.config import ENDPOINTS, Settings

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = ROOT / "servers" / "dns-ovh.yaml"
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
