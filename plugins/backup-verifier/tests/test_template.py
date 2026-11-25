# SPDX-License-Identifier: Apache-2.0
"""The nightly template stays honest: no cron it cannot carry, the branch on the verdict, the core's schemas.

Played from a core checkout, as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/backup-verifier/tests
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "backup-nightly"


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_template_ships_no_start_rule() -> None:
    """A nightly run is a project schedule: a start rule has no time, and would need a newer core."""
    assert "start_rules" not in _load(TEMPLATE / "manifest.yaml")["defaults"]


def test_the_readme_documents_the_schedule_entry() -> None:
    readme = (TEMPLATE / "README.md").read_text(encoding="utf-8")
    assert "schedules:" in readme
    assert "cron:" in readme
    assert "workflow: backup-nightly" in readme


def test_red_goes_to_review_and_green_straight_to_done() -> None:
    workflow = _load(TEMPLATE / "workflows" / "backup-nightly.yaml")
    from_queue = [t for t in workflow["transitions"] if t["from"] == "queue"]
    assert [t["id"] for t in from_queue] == ["t-red", "t-green"], "Red must come first: first match wins"
    assert from_queue[0]["when"] == {"backup_verdict": "Red"}
    assert from_queue[0]["to"] == "awaiting_owner"
    assert "when" not in from_queue[1]
    assert from_queue[1]["to"] == "done"


def test_the_review_is_a_human_task_of_the_platform_owner() -> None:
    workflow = _load(TEMPLATE / "workflows" / "backup-nightly.yaml")
    review = next(t for t in workflow["transitions"] if t["id"] == "t-review")
    assert workflow["actors"][review["by"]]["type"] == "human"
    assert review["task"]["attest"]


def test_the_workflow_names_the_agent_the_plugin_ships() -> None:
    workflow = _load(TEMPLATE / "workflows" / "backup-nightly.yaml")
    agents = {a["agent"] for a in workflow["actors"].values() if a["type"] == "agent"}
    shipped = _load(PLUGIN / "agents" / "backup-verifier.yaml")["agent"]["slug"]
    assert agents == {shipped}


def test_the_core_s_template_schema_accepts_the_manifest() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(os.environ.get("TRANIBOR_CORE") or Path.cwd())
    schemas = root / "packages" / "contracts" / "schemas"
    if not (schemas / "template.schema.json").is_file():
        pytest.skip("not in a core checkout (TRANIBOR_CORE)")
    schema = json.loads((schemas / "template.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(_load(TEMPLATE / "manifest.yaml"), schema)
