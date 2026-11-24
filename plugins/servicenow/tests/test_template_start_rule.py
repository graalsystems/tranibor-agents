# SPDX-License-Identifier: Apache-2.0
"""A project born of `itsm-incident-triage` opens the queue's new incidents: the template ships its start rule.

The core opens a plugin tracker's item, by the queue poll or a delivery, only when it carries `agent-ready`
or matches one of the project's start rules (ADR 0113 of the core); the ServiceNow server's items carry no
label. On the Diametral dev cluster (core 0.29.0, servicenow 0.2.8) a project from the template polled the
queue and opened nothing until the rule was set by hand (graalsystems/tranibor-agents#38). From core 0.29.2
a template ships the rule (`defaults.start_rules`, graalsystems/tranibor#2200), and the plugin's floor says so.

Played from a core checkout, as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/servicenow/tests
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
MANIFEST = PLUGIN / "templates" / "itsm-incident-triage" / "manifest.yaml"
RULE = {"item_type": "incident", "states": ["New"]}


def _manifest() -> dict[str, Any]:
    loaded = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_template_ships_the_rule_that_opens_new_incidents() -> None:
    assert _manifest()["defaults"].get("start_rules") == [RULE]


def test_the_plugin_needs_a_core_that_accepts_the_rule() -> None:
    """A core before 0.29.2 refuses `defaults.start_rules` (its template schema's `defaults` is closed)."""
    core = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))["metadata"]["core"]
    floor = next(part for part in core.split(",") if part.startswith(">="))
    assert tuple(int(n) for n in floor.removeprefix(">=").split(".")) >= (0, 29, 2), core


def test_the_core_s_schemas_accept_the_rule() -> None:
    """The manifest against the core's own `template.schema.json`, and the rule as a project's start rule."""
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(os.environ.get("TRANIBOR_CORE") or Path.cwd())
    schemas = root / "packages" / "contracts" / "schemas"
    if not (schemas / "template.schema.json").is_file():
        pytest.skip("not in a core checkout (TRANIBOR_CORE)")
    template = json.loads((schemas / "template.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(_manifest(), template)
    contracts = pytest.importorskip("tranibor_contracts.project", reason="the core's project contract")
    rule = contracts.StartRule.model_validate(RULE)
    assert rule.model_dump(mode="json")["item_type"] == "incident"
