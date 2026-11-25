# SPDX-License-Identifier: Apache-2.0
"""The `lan-incident` template, the agent and the profile agree with each other and with the core's schemas.

Played from a core checkout that knows profiles (stories S86-01/S86-02), as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/lan/tests
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "lan-incident"
PROFILE = PLUGIN / "profiles" / "lan.yaml"
VENDORS = ("unifi", "ubiquiti", "omada", "tp-link", "meraki", "aruba", "mikrotik")


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _schemas() -> Path:
    root = Path(os.environ.get("TRANIBOR_CORE") or Path.cwd())
    schemas = root / "packages" / "contracts" / "schemas"
    if not (schemas / "template.schema.json").is_file():
        pytest.skip("not in a core checkout (TRANIBOR_CORE)")
    return schemas


def test_the_manifest_and_the_workflow_follow_the_core_s_schemas() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schemas = _schemas()
    for schema, document in (
        ("template.schema.json", TEMPLATE / "manifest.yaml"),
        ("workflow.schema.json", TEMPLATE / "workflows" / "lan-incident.yaml"),
    ):
        jsonschema.validate(_load(document), json.loads((schemas / schema).read_text(encoding="utf-8")))


def test_the_agent_uses_only_the_profile_s_tools_and_names_no_vendor() -> None:
    profile_tools = {tool["name"] for tool in _load(PROFILE)["tools"]}
    agent = _load(PLUGIN / "agents" / "lan-steward.yaml")["agent"]["spec"]
    assert [server["profile"] for server in agent["mcp_servers"]] == ["lan/lan@1"]
    assert "connector" not in agent["mcp_servers"][0]
    assert set(agent["mcp_servers"][0]["tools"]) == profile_tools
    assert agent["tools"] == {"catalogue": [], "extensions": [], "web": []}
    text = agent["instructions"].lower()
    for vendor in VENDORS:
        assert vendor not in text


def test_the_skill_and_the_profile_name_no_vendor() -> None:
    for path in (PLUGIN / "skills" / "lan-troubleshooting" / "SKILL.md", PROFILE):
        text = path.read_text(encoding="utf-8").lower()
        for vendor in VENDORS:
            assert vendor not in text.replace("lan-unifi", ""), (path.name, vendor)


def test_the_template_names_no_connector_and_no_platform_action() -> None:
    manifest = _load(TEMPLATE / "manifest.yaml")
    assert manifest["requires"]["connectors"] == {}
    assert "org_connectors" not in manifest["requires"]
    workflow = _load(TEMPLATE / "workflows" / "lan-incident.yaml")
    assert all("action" not in transition for transition in workflow["transitions"])


def test_the_workflow_runs_from_the_queue_to_done_through_the_administrator() -> None:
    workflow = _load(TEMPLATE / "workflows" / "lan-incident.yaml")
    steps = {t["id"]: (t["from"], t["to"], t["by"]) for t in workflow["transitions"]}
    assert steps["t-diagnose"] == ("queue", "awaiting_review", "steward")
    assert steps["t-review"] == ("awaiting_review", "done", "network_admin")


def test_every_action_kind_the_workflow_allows_is_a_profile_write_or_none() -> None:
    profile = _load(PROFILE)["tools"]
    writes = {t["name"] for t in profile if t["access"] == "write"}
    workflow = _load(TEMPLATE / "workflows" / "lan-incident.yaml")
    gate = next(g for t in workflow["transitions"] for g in t.get("gates", []) if isinstance(g, dict))
    assert set(gate["params"]["values"]["action_kind"]) == writes | {"none"}


def test_the_writes_that_cut_someone_off_carry_human_approval_in_the_profile() -> None:
    tools = {t["name"]: t for t in _load(PROFILE)["tools"]}
    for name in ("restart_device", "power_cycle_port", "block_client", "upgrade_firmware"):
        assert tools[name]["human_approval"] is True, name
    assert tools["unblock_client"]["human_approval"] is False
