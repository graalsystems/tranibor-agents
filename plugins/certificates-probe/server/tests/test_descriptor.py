# SPDX-License-Identifier: Apache-2.0
"""`servers/certificates-probe.yaml` and the server agree. The profile's own checks (read-only, every tool
declared) are in the conformance suite; this is what is specific to this descriptor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from tranibor_certificates_probe.app import build_app
from tranibor_certificates_probe.config import Settings

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = ROOT / "servers" / "certificates-probe.yaml"
PROCEDURE = ROOT / "procedures" / "connector.mcp.certificates-probe.md"


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
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/certificates-probe-mcp@sha256:0{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"]) == (8080, "mcp-http", {"mcp": "/mcp", "health": "/healthz"})
    properties = d["config_schema"]["properties"]
    assert set(d["config_schema"]["required"]) == set(properties) == {"hosts"}
    assert not d.get("secret_fields")
    assert properties["hosts"]["x-env"] == "CERTIFICATES_PROBE_HOSTS"
    assert d["egress"] == {"from_fields": ["hosts"]}
    assert set(d["egress"]["from_fields"]) <= set(properties)
    # Every field tells the administrator where to find the value (ADR 0224 section 5).
    assert all(p.get("x-help") and p.get("x-link") and p.get("title") for p in properties.values())


def test_the_procedure_describes_the_fields() -> None:
    front = yaml.safe_load(PROCEDURE.read_text().split("---")[1])
    assert set(front["fields"]) <= set(descriptor()["config_schema"]["properties"])


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"CERTIFICATES_PROBE_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without it, the same environment is a configuration nobody gave: it refuses to start.
    with pytest.raises(ValueError, match="CERTIFICATES_PROBE_"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))


def test_the_descriptor_s_schemas_are_the_profile_s() -> None:
    """The server declares what the profile declares, so the core's comparison finds them equal (the schemas
    are copied, with the definitions each one points to inlined)."""
    profile = yaml.safe_load((ROOT.parent / "certificates" / "profiles" / "certificates.yaml").read_text())
    declared = {op["name"]: op for op in descriptor()["operations"]}
    for tool in profile["tools"]:
        for key, source in (("input_schema", "input"), ("output_schema", "output")):
            served = dict(declared[tool["name"]][key])
            served.pop("$defs", None)
            assert served == tool[source], f"{tool['name']}: {key}"
