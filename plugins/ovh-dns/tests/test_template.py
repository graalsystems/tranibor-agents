# SPDX-License-Identifier: Apache-2.0
"""The `dns-change` template and the plugin's manifest agree with each other and with the core's schemas.

Played from a core checkout, as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/ovh-dns/tests
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "dns-change"


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
        ("workflow.schema.json", TEMPLATE / "workflows" / "dns-change.yaml"),
        ("plugin.schema.json", PLUGIN / "plugin.yaml"),
        ("plugin-server.schema.json", PLUGIN / "servers" / "ovh-dns.yaml"),
    ):
        jsonschema.validate(_load(document), json.loads((schemas / schema).read_text(encoding="utf-8")))


def test_the_workflow_calls_only_declared_operations_of_the_plugin_s_server() -> None:
    declared = {op["name"] for op in _load(PLUGIN / "servers" / "ovh-dns.yaml")["operations"]}
    workflow = _load(TEMPLATE / "workflows" / "dns-change.yaml")
    called = {
        effect["with"]["operation"]
        for transition in workflow["transitions"]
        for effect in (transition.get("action") or {}).get("effects", [])
    }
    assert called == {"refresh_zone"} and called <= declared


def test_the_agent_may_only_call_declared_operations_and_never_the_refresh_alone() -> None:
    declared = {op["name"] for op in _load(PLUGIN / "servers" / "ovh-dns.yaml")["operations"]}
    agent = _load(PLUGIN / "agents" / "dns-steward.yaml")["agent"]["spec"]
    tools = {tool for server in agent["mcp_servers"] for tool in server["tools"]}
    assert tools <= declared
    assert agent["tools"] == {"catalogue": [], "extensions": [], "web": []}


def test_the_workflow_runs_from_the_queue_to_a_published_zone() -> None:
    workflow = _load(TEMPLATE / "workflows" / "dns-change.yaml")
    steps = {t["id"]: (t["from"], t["to"], t["by"]) for t in workflow["transitions"]}
    assert steps["t-plan"] == ("queue", "awaiting_review", "steward")
    assert steps["t-review"] == ("awaiting_review", "applied", "zone_owner")
    assert steps["t-refresh"] == ("applied", "done", "platform")
