# SPDX-License-Identifier: Apache-2.0
"""The former name, `choregos`, during the window of the core's ADR 0226.

The server writes `tranibor` in what it leaves on an instance (a work note's `[tranibor:<key>]`, a status
note's `[tranibor-status:<marker>]`, a record's `correlation_id = tranibor:<key>`), and still FINDS what an
earlier version wrote there under the former name: a replayed action is the same action, never a second
note or a second record. A Business Rule installed from an earlier procedure sends `X-Choregos-Secret`, and
the core hands the status marker as `choregos:status` until its window closes (then `tranibor:status`):
both are read. A file or a command that still names the former package or command keeps working.
"""

from __future__ import annotations

import importlib
import json
import sys
import tomllib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tranibor_servicenow.app import build_app
from tranibor_servicenow.config import FAKE_WEBHOOK_SECRET, Settings
from tranibor_servicenow.fake import FakeServiceNow, sys_id_of

from .conftest import Mcp

SERVER = Path(__file__).resolve().parents[1]
EVENT = {"table": "incident", "sys_id": sys_id_of("INC0010001"), "number": "INC0010001", "sys_mod_count": 1}


def written_before(fake: FakeServiceNow, sys_id: str) -> None:
    """Rewrites the record's work notes as a version before ADR 0226 left them: under the former name."""
    for row in fake.journal(sys_id):
        for new, former in (("[tranibor:", "[choregos:"), ("[tranibor-status:", "[choregos-status:")):
            row["value"] = row["value"].replace(new, former)


# ─────────────────────────── what the server writes, and finds ───────────────────────────


async def test_a_work_note_is_marked_tranibor_and_one_marked_choregos_is_found(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010003")
    first = await mcp.call("add_work_note", table="incident", sys_id=sys_id, text="Asked the caller.", action_key="a7")
    assert first["marker"] == "[tranibor:a7]"
    written_before(fake, sys_id)
    again = await mcp.call("add_work_note", table="incident", sys_id=sys_id, text="Asked the caller.", action_key="a7")
    assert again == {"table": "incident", "sys_id": sys_id, "written": False, "marker": "[choregos:a7]"}
    assert len([e for e in fake.journal(sys_id) if e["element"] == "work_notes"]) == 1


async def test_a_tracker_comment_written_before_is_not_written_again(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010011")
    await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c9")
    written_before(fake, sys_id)
    again = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c9")
    assert again == {"key": "INC0010011", "id": "[choregos:c9]", "written": False}
    assert len(fake.journal(sys_id)) == 1


async def test_a_change_filed_under_the_former_correlation_id_is_found(mcp: Mcp, fake: FakeServiceNow) -> None:
    arguments = {
        "short_description": "Raise the VPN idle timeout",
        "description": "From INC0010006.",
        "implementation_plan": "Set idle timeout to 3600 s.",
        "backout_plan": "Set it back to 600 s.",
        "test_plan": "Stay idle 15 minutes.",
        "action_key": "act-7",
    }
    seeded = len(fake.rows("change_request"))
    first = await mcp.call("create_change_request", **arguments)
    assert first["correlation_id"] == "tranibor:act-7"
    fake.tables["change_request"][first["record"]["sys_id"]]["correlation_id"] = "choregos:act-7"
    again = await mcp.call("create_change_request", **arguments)
    assert (again["created"], again["correlation_id"]) == (False, "choregos:act-7")
    assert again["record"]["sys_id"] == first["record"]["sys_id"]
    assert "not_kept" not in again
    assert len(fake.rows("change_request")) == seeded + 1


async def test_an_incident_filed_under_the_former_correlation_id_is_found(mcp: Mcp, fake: FakeServiceNow) -> None:
    seeded = len(fake.rows("incident"))
    first = await mcp.call("tracker_create_item", title="Printer jam", body="Floor 2.", action_key="k-3")
    assert fake.tables["incident"][first["sys_id"]]["correlation_id"] == "tranibor:k-3"
    fake.tables["incident"][first["sys_id"]]["correlation_id"] = "choregos:k-3"
    again = await mcp.call("tracker_create_item", title="Printer jam", body="Floor 2.", action_key="k-3")
    assert (again["key"], again["created"]) == (first["key"], False)
    assert len(fake.rows("incident")) == seeded + 1


@pytest.mark.parametrize("handed", ["choregos:status", "tranibor:status"])
async def test_a_status_written_before_is_not_written_again(mcp: Mcp, fake: FakeServiceNow, handed: str) -> None:
    """The core of the window hands `choregos:status`, the core past it `tranibor:status`: a status note an
    earlier version tagged `[choregos-status:choregos:status]` is the same note under both."""
    sys_id = sys_id_of("INC0010011")
    await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Triage", marker="choregos:status")
    assert fake.journal(sys_id)[0]["value"].endswith("[tranibor-status:choregos:status]")
    written_before(fake, sys_id)
    same = await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Triage", marker=handed)
    assert same == {"key": "INC0010011", "written": False}
    moved = await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Review", marker=handed)
    assert moved["written"] is True
    notes = [row["value"] for row in fake.journal(sys_id)]
    assert len(notes) == 2 and f"Review\n\n[tranibor-status:{handed}]" in notes


async def test_fetch_item_reads_the_markers_under_either_name(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010011")
    await mcp.call("tracker_comment", key="INC0010011", markdown="Old.", action_key="old")
    await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Triage", marker="choregos:status")
    written_before(fake, sys_id)
    await mcp.call("tracker_comment", key="INC0010011", markdown="New.", action_key="new")
    item = await mcp.call("tracker_fetch_item", key="INC0010011")
    assert {c["marker"] for c in item["comments"]} == {"old", "choregos:status", "new"}


# ─────────────────────────── the Business Rule's secret ───────────────────────────


@pytest.mark.parametrize("name", ["X-Tranibor-Secret", "X-Choregos-Secret", "x-tranibor-secret"])
async def test_the_secret_is_read_under_either_header(mcp: Mcp, name: str) -> None:
    body = json.dumps(EVENT)
    parsed = await mcp.call("tracker_parse_event", headers={name: FAKE_WEBHOOK_SECRET}, body=body)
    assert parsed["verified"] is True


async def test_the_new_header_is_read_first(mcp: Mcp) -> None:
    """As the core reads its own: the new header when it is set, the former only when it is absent or empty."""
    body = json.dumps(EVENT)
    wrong_new = {"X-Tranibor-Secret": "nope", "X-Choregos-Secret": FAKE_WEBHOOK_SECRET}
    assert (await mcp.call("tracker_parse_event", headers=wrong_new, body=body))["verified"] is False
    empty_new = {"X-Tranibor-Secret": "", "X-Choregos-Secret": FAKE_WEBHOOK_SECRET}
    assert (await mcp.call("tracker_parse_event", headers=empty_new, body=body))["verified"] is True


@pytest.mark.parametrize("name", ["X-Tranibor-Secret", "X-Choregos-Secret"])
def test_the_events_route_reads_either_header(name: str) -> None:
    env = {"SERVICENOW_FAKE": "1", "SERVICENOW_WEBHOOK_SECRET": "s3cret", "TRANIBOR_SERVER_TOKEN": "t0ken"}
    event = {"table": "incident", "sys_id": sys_id_of("INC0010001"), "number": "INC0010001", "sys_mod_count": 2}
    with TestClient(
        build_app(Settings.from_env(env)),
        base_url="http://servicenow.tranibor-plugins.svc:8080",
        headers={"Authorization": "Bearer t0ken"},
    ) as client:
        assert client.post("/events", headers={name: "s3cret"}, content=json.dumps(event)).status_code == 200
        assert client.post("/events", headers={name: "other"}, content=json.dumps(event)).status_code == 401


# ─────────────────────────── the former package and command ───────────────────────────


def test_the_former_import_name_is_the_package() -> None:
    """`choregos_servicenow.app:build_app` (the core's in-process demonstration file names it) builds the
    same application, with the same classes."""
    for name in [m for m in sys.modules if m == "choregos_servicenow" or m.startswith("choregos_servicenow.")]:
        del sys.modules[name]
    with pytest.warns(DeprecationWarning, match="tranibor_servicenow"):
        former = importlib.import_module("choregos_servicenow")
    assert former is importlib.import_module("tranibor_servicenow")
    assert importlib.import_module("choregos_servicenow.app").build_app is build_app
    assert importlib.import_module("choregos_servicenow.config").Settings is Settings


def test_both_commands_start_the_server() -> None:
    project = tomllib.loads((SERVER / "pyproject.toml").read_text())
    scripts = project["project"]["scripts"]
    assert scripts["tranibor-servicenow"] == scripts["choregos-servicenow"] == "tranibor_servicenow.app:main"
    assert {"src/tranibor_servicenow", "src/choregos_servicenow"} <= set(
        project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    )
    assert '"/app/.venv/bin/tranibor-servicenow"' in (SERVER / "Dockerfile").read_text()


def test_the_procedure_s_rule_sends_the_header_the_server_reads_first() -> None:
    """A new instance gets the rule of the procedure, which sends the new header and reads the new
    properties, the former ones when they are not set."""
    from tranibor_servicenow.webhook import SECRET_HEADERS

    procedure = (SERVER.parent / "procedures" / "connector.mcp.servicenow.md").read_text()
    assert "request.setRequestHeader('X-Tranibor-Secret', secret);" in procedure
    assert SECRET_HEADERS[0] == "x-tranibor-secret"
    assert "gs.getProperty('tranibor.webhook_url') || gs.getProperty('choregos.webhook_url')" in procedure
