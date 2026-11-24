# SPDX-License-Identifier: Apache-2.0
"""The apply step of `incident-triage` renders against the work item the platform actually holds.

The platform's plugin tracker (`tranibor_adapters.tracker.plugin`) gives the server's `fields` to the work
item only inside `fields.tracker` (S51-04): `answers` holds them as text, by name. A work item opened by
the poll or the webhook carries `fields.tracker` and, once the service desk has settled the review, the
form's answers — never a top-level `fields.sys_id`. The apply step read `{{ fields.sys_id }}`: on the
operator's dev cluster (S80-20, 2026-11-24) the system transition was refused (`parameter not found`),
`action_succeeded` failed, and the incident went to `needs_human` with nothing written.

Played from a core checkout, as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/servicenow/tests
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "itsm-incident-triage"
WORKFLOW_FILE = TEMPLATE / "workflows" / "incident-triage.yaml"
SYS_ID = "c268f7e4c3b453107e71becdd40131a8"
GROUP = "d625dccec0a8016700a222a0f7900d06"
#: The review form's answers, as the service desk settles them.
REVIEW = {"category": "software", "impact": "3", "urgency": "3", "assignment_group": GROUP, "triage_note": "Reviewed."}


def _work_item_fields() -> dict[str, Any]:
    """What the work item holds: the core's own `tracker` block over what `tracker_fetch_item` returns."""
    ticket_fields = pytest.importorskip("tranibor_adapters.tracker.ticket_fields", reason="the core's fields")
    server_fields = {"sys_id": SYS_ID, "table": "incident", "category": "inquiry", "impact": "2", "urgency": "2"}
    tracker = ticket_fields.tracker_block(
        kind="servicenow",
        key="INC0010039",
        reporter=ticket_fields.named_person("Tranibor Integration"),
        creator=ticket_fields.named_person(None),
        labels=[],
        answers=ticket_fields.answers(sorted(server_fields.items())),
    )
    return {"tracker": tracker, **REVIEW}


def _apply() -> dict[str, Any]:
    workflow = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    return next(t for t in workflow["transitions"] if t["id"] == "t-apply")["action"]


def test_the_apply_step_renders_with_the_fields_the_work_item_holds() -> None:
    effects = pytest.importorskip("tranibor_api.effects", reason="the core's renderer")
    context = {"fields": _work_item_fields(), "work_item": {"id": "w", "key": "INC0010039", "title": "t"}}
    params = effects.render(_apply()["params"], context)
    assert params == {
        "sys_id": SYS_ID,
        "category": "software",
        "impact": "3",
        "urgency": "3",
        "assignment_group": GROUP,
        "note": "Reviewed.",
    }
    calls = [effects.render(e["with"], {"params": params}) for e in _apply()["effects"]]
    assert [c["operation"] for c in calls] == ["update_record", "add_work_note"]
    assert {c["arguments"]["sys_id"] for c in calls} == {SYS_ID}
    written = {"category": "software", "impact": "3", "urgency": "3", "assignment_group": GROUP}
    assert calls[0]["arguments"]["fields"] == written


def test_the_work_item_has_no_top_level_sys_id() -> None:
    """The shape the step must read: the server's fields only inside `fields.tracker.answers`."""
    fields = _work_item_fields()
    assert "sys_id" not in fields
    assert fields["tracker"]["answers"]["sys_id"] == SYS_ID
