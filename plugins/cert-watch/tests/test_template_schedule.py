# SPDX-License-Identifier: Apache-2.0
"""The template carries no cron (its `defaults` is closed): the schedule the README documents is the
project's own, and must be one the core accepts.

Played from a core checkout, as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    TRANIBOR_CORE=$PWD uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/cert-watch/tests
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
README = TEMPLATE / "README.md"


def _manifest() -> dict[str, Any]:
    loaded = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _documented_schedules() -> list[dict[str, Any]]:
    """The `schedules:` block of the README's YAML fence."""
    blocks = re.findall(r"```yaml\n(.*?)```", README.read_text(encoding="utf-8"), flags=re.S)
    found = [yaml.safe_load(b) for b in blocks if b.lstrip().startswith("schedules:")]
    assert len(found) == 1
    schedules = found[0]["schedules"]
    assert isinstance(schedules, list)
    return schedules


def test_the_template_has_no_cron_and_asks_for_the_tracker_that_schedules_need() -> None:
    manifest = _manifest()
    assert "schedules" not in manifest["defaults"]
    assert manifest["requires"]["connectors"]["tracker"] == "internal"
    assert manifest["defaults"]["default_workflow"] == "cert-review"


def test_the_documented_schedule_starts_the_template_s_workflow() -> None:
    (schedule,) = _documented_schedules()
    assert schedule["workflow"] == _manifest()["defaults"]["default_workflow"]


def test_the_core_accepts_the_manifest_and_the_documented_schedule() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(os.environ.get("TRANIBOR_CORE") or Path.cwd())
    schemas = root / "packages" / "contracts" / "schemas"
    if not (schemas / "template.schema.json").is_file():
        pytest.skip("not in a core checkout (TRANIBOR_CORE)")
    jsonschema.validate(_manifest(), json.loads((schemas / "template.schema.json").read_text(encoding="utf-8")))
    contracts = pytest.importorskip("tranibor_contracts.project", reason="the core's project contract")
    for schedule in _documented_schedules():
        parsed = contracts.ScheduleConfig.model_validate(schedule)
        assert parsed.name == "weekly-cert-review"
        assert len(parsed.cron.split()) == 5
