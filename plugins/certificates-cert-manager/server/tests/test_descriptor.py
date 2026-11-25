# SPDX-License-Identifier: Apache-2.0
"""`servers/certificates-cert-manager.yaml` and the server agree. The profile's own checks (read-only, every tool
declared) are in the conformance suite; this is what is specific to this descriptor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from tranibor_certificates_cert_manager.app import build_app
from tranibor_certificates_cert_manager.config import Settings

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = ROOT / "servers" / "certificates-cert-manager.yaml"
PROCEDURE = ROOT / "procedures" / "connector.mcp.certificates-cert-manager.md"


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def test_the_descriptor_implements_the_profile_and_the_plugin_requires_it() -> None:
    assert descriptor()["implements"] == ["certificates/certificates@1"]
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert [r["name"] for r in plugin["requires"]["plugins"]] == ["certificates"]


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    image = r"ghcr\.io/graalsystems/tranibor-agents/certificates-cert-manager-mcp@sha256:0{64}"
    assert re.fullmatch(image, d["image"])
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    properties = d["config_schema"]["properties"]
    assert set(d["config_schema"]["required"]) == {"kube_api_url", "kube_token"}
    assert d["secret_fields"] == ["kube_token"]
    assert {p["x-env"] for p in properties.values()} == {
        "CERTIFICATES_CERT_MANAGER_KUBE_API_URL",
        "CERTIFICATES_CERT_MANAGER_KUBE_CA_CERT",
        "CERTIFICATES_CERT_MANAGER_KUBE_TOKEN",
        "CERTIFICATES_CERT_MANAGER_NAMESPACES",
    }
    assert d["egress"] == {"from_fields": ["kube_api_url"]}
    # Every field tells the administrator where to find the value (ADR 0224 section 5).
    assert all(p.get("title") and p.get("x-help") and p.get("x-link") for p in properties.values())


def test_the_procedure_describes_every_secret_field() -> None:
    front = yaml.safe_load(PROCEDURE.read_text().split("---")[1])
    assert set(descriptor()["secret_fields"]) <= set(front["fields"])
    assert set(front["fields"]) <= set(descriptor()["config_schema"]["properties"])


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"CERTIFICATES_CERT_MANAGER_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without it, the same environment is a cluster nobody configured: it refuses to start.
    with pytest.raises(ValueError, match="CERTIFICATES_CERT_MANAGER_"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))
