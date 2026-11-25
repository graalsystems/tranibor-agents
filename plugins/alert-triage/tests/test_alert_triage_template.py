# SPDX-License-Identifier: Apache-2.0
"""The `alert-triage` template: its routing and its silence action render against what the work item holds.

The review forms' answers become the work item's fields, and the action's parameters are rendered over
`fields` and `work_item` only. A workflow that reads another name is refused by the platform at run time
(`parameter not found`), so these tests render the real action, and read the routing, from the real file.

Played from a core checkout, as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    TRANIBOR_CORE=$PWD uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/alert-triage/tests
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
WORKFLOW_FILE = TEMPLATE / "workflows" / "alert-triage.yaml"
MATCHERS = '{"alertname": "FlappingCPU", "service": "search"}'
#: What the trigger maps, then what the silence review form settles.
FIELDS = {
    "alertname": "FlappingCPU",
    "severity": "warning",
    "silence_matchers": MATCHERS,
    "silence_minutes": "60",
    "silence_comment": "Known flapping around the threshold",
}


def _workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _transition(name: str) -> dict[str, Any]:
    return next(t for t in _workflow()["transitions"] if t["id"] == name)


def test_the_silence_action_renders_with_the_fields_the_work_item_holds() -> None:
    effects = pytest.importorskip("tranibor_api.effects", reason="the core's renderer")
    action = _transition("t-silence")["action"]
    context = {"fields": FIELDS, "work_item": {"id": "w", "key": "ALERT-1", "title": "t"}}
    params = effects.render(action["params"], context)
    assert params == {"matchers": MATCHERS, "minutes": "60", "comment": "Known flapping around the threshold"}
    call = effects.render(action["effects"][0]["with"], {"params": params})
    assert call["connector"] == "grafana"
    assert call["operation"] == "create_silence"
    assert json.loads(call["arguments"]["matchers"]) == {"alertname": "FlappingCPU", "service": "search"}
    assert call["arguments"]["duration_minutes"] == "60"
    title = effects.render(action["title"], context)
    assert title == "Silence FlappingCPU for 60 min"


def test_the_compensation_expires_the_silence_the_effect_created() -> None:
    effects = pytest.importorskip("tranibor_api.effects", reason="the core's renderer")
    compensate = _transition("t-silence")["action"]["effects"][0]["compensate"]
    assert compensate["with"]["operation"] == "expire_silence"
    rendered = effects.render(compensate["with"], {"params": {}, "effects": [], "result": {"silence_id": "sil-1"}})
    assert rendered["arguments"] == {"silence_id": "sil-1"}


def test_only_a_silence_decision_reaches_the_silence_form() -> None:
    silence, text = _transition("t-review-silence"), _transition("t-review-text")
    assert silence["when"] == {"decision": "silence"}
    assert "when" not in text, "the text branch is the default: it must come after, without a condition"
    order = [t["id"] for t in _workflow()["transitions"]]
    assert order.index("t-review-silence") < order.index("t-review-text")
    assert silence["from"] == text["from"] == "awaiting_review"


def test_the_silence_is_approved_by_a_second_person() -> None:
    approval = _transition("t-silence")["action"]["approval"]
    assert approval["separation_of_duties"] is True
    assert approval["step_up_minutes"] <= 10
    assert _transition("t-silence")["on_reject"] == "declined"


def test_the_triager_answers_what_the_routing_reads() -> None:
    agent = yaml.safe_load((PLUGIN / "agents" / "on-call-triager.yaml").read_text(encoding="utf-8"))
    text = agent["agent"]["spec"]["instructions"]
    assert set(_transition("t-triage")["outputs"]) == {"alert_triage_markdown", "decision"}
    for decision in ("escalate", "silence", "fix_rule", "none"):
        assert f"`{decision}`" in text


def test_the_review_form_matchers_pattern_accepts_a_silence_the_server_accepts() -> None:
    import re

    form = _transition("t-review-silence")["task"]["form"]["properties"]["silence_matchers"]
    assert re.match(form["pattern"], MATCHERS)
    assert not re.match(form["pattern"], '{"service": "search"}')


def test_the_core_s_schemas_accept_the_manifest_and_the_workflow() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(os.environ.get("TRANIBOR_CORE") or Path.cwd())
    schemas = root / "packages" / "contracts" / "schemas"
    if not (schemas / "template.schema.json").is_file():
        pytest.skip("not in a core checkout (TRANIBOR_CORE)")
    template = json.loads((schemas / "template.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(yaml.safe_load((TEMPLATE / "manifest.yaml").read_text(encoding="utf-8")), template)
    workflow = json.loads((schemas / "workflow.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(_workflow(), workflow)
