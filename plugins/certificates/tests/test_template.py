# SPDX-License-Identifier: Apache-2.0
"""The `cert-review` template, the agent and the profile agree with each other and with the core's schemas.

Played from a core checkout that knows profiles (stories S86-01/S86-02), as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/certificates/tests
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "cert-review"
PROFILE = PLUGIN / "profiles" / "certificates.yaml"


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
        ("workflow.schema.json", TEMPLATE / "workflows" / "cert-review.yaml"),
    ):
        jsonschema.validate(_load(document), json.loads((schemas / schema).read_text(encoding="utf-8")))


def test_the_agent_uses_only_the_profile_s_tools_and_names_no_provider() -> None:
    profile_tools = {tool["name"] for tool in _load(PROFILE)["tools"]}
    agent = _load(PLUGIN / "agents" / "cert-watch.yaml")["agent"]["spec"]
    assert [server["profile"] for server in agent["mcp_servers"]] == ["certificates/certificates@1"]
    assert "connector" not in agent["mcp_servers"][0]
    assert set(agent["mcp_servers"][0]["tools"]) == profile_tools
    assert agent["tools"] == {"catalogue": [], "extensions": [], "web": []}
    text = agent["instructions"].lower()
    for provider in ("cert-manager", "certmanager", "crt.sh", "letsencrypt", "let's encrypt"):
        assert provider not in text


def test_the_profile_has_no_write_tool() -> None:
    assert {tool["access"] for tool in _load(PROFILE)["tools"]} == {"read"}


def test_the_template_names_no_connector_and_no_platform_action() -> None:
    manifest = _load(TEMPLATE / "manifest.yaml")
    assert manifest["requires"]["connectors"] == {"tracker": "internal"}
    assert "org_connectors" not in manifest["requires"]
    workflow = _load(TEMPLATE / "workflows" / "cert-review.yaml")
    assert all("action" not in transition for transition in workflow["transitions"])


def test_the_workflow_runs_from_the_queue_to_done_through_the_platform_team() -> None:
    workflow = _load(TEMPLATE / "workflows" / "cert-review.yaml")
    steps = {t["id"]: (t["from"], t["to"], t["by"]) for t in workflow["transitions"]}
    assert steps["t-review"] == ("queue", "awaiting_read", "watch")
    assert steps["t-read"] == ("awaiting_read", "done", "platform_team")


def _documented_schedules() -> list[dict[str, Any]]:
    """The `schedules:` block of the README's YAML fence."""
    blocks = re.findall(r"```yaml\n(.*?)```", (TEMPLATE / "README.md").read_text(encoding="utf-8"), flags=re.S)
    found = [yaml.safe_load(b) for b in blocks if b.lstrip().startswith("schedules:")]
    assert len(found) == 1
    schedules = found[0]["schedules"]
    assert isinstance(schedules, list)
    return schedules


def test_the_template_has_no_cron_and_the_documented_schedule_starts_its_workflow() -> None:
    manifest = _load(TEMPLATE / "manifest.yaml")
    assert "schedules" not in manifest["defaults"]
    (schedule,) = _documented_schedules()
    assert schedule["workflow"] == manifest["defaults"]["default_workflow"]


def test_the_core_accepts_the_documented_schedule() -> None:
    _schemas()
    contracts = pytest.importorskip("tranibor_contracts.project", reason="the core's project contract")
    for schedule in _documented_schedules():
        parsed = contracts.ScheduleConfig.model_validate(schedule)
        assert len(parsed.cron.split()) == 5
