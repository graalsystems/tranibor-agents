# SPDX-License-Identifier: Apache-2.0
"""The `alert-triage` template, the agent and the profile agree with each other and with the core's schemas.

Played from a core checkout that knows profiles (stories S86-01/S86-02), as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/observability/tests
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "alert-triage"
PROFILE = PLUGIN / "profiles" / "observability.yaml"


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
        ("workflow.schema.json", TEMPLATE / "workflows" / "alert-triage.yaml"),
    ):
        jsonschema.validate(_load(document), json.loads((schemas / schema).read_text(encoding="utf-8")))


def test_the_agent_uses_only_the_profile_s_tools_and_names_no_provider() -> None:
    profile_tools = {tool["name"] for tool in _load(PROFILE)["tools"]}
    agent = _load(PLUGIN / "agents" / "alert-triage.yaml")["agent"]["spec"]
    assert [server["profile"] for server in agent["mcp_servers"]] == ["observability/observability@1"]
    assert "connector" not in agent["mcp_servers"][0]
    assert set(agent["mcp_servers"][0]["tools"]) <= profile_tools
    assert agent["tools"] == {"catalogue": [], "extensions": [], "web": []}
    text = agent["instructions"].lower()
    for provider in ("grafana", "prometheus", "datadog", "loki"):
        assert provider not in text


def test_the_template_names_no_connector_and_no_platform_action() -> None:
    manifest = _load(TEMPLATE / "manifest.yaml")
    # only the internal tracker a webhook trigger needs: no organisation connector, no provider
    assert manifest["requires"]["connectors"] == {"tracker": "internal"}
    assert "org_connectors" not in manifest["requires"]
    workflow = _load(TEMPLATE / "workflows" / "alert-triage.yaml")
    assert all("action" not in transition for transition in workflow["transitions"])


def test_the_workflow_runs_from_the_alert_to_done_through_the_owner() -> None:
    workflow = _load(TEMPLATE / "workflows" / "alert-triage.yaml")
    steps = {t["id"]: (t["from"], t["to"], t["by"]) for t in workflow["transitions"]}
    assert steps["t-triage"] == ("alert", "awaiting_review", "triager")
    assert steps["t-review"] == ("awaiting_review", "done", "on_call")
