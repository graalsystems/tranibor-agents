# SPDX-License-Identifier: Apache-2.0
"""The tracker profile (TRACKER_PROFILE.md), through MCP, against the fake."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from choregos_servicenow.app import Runtime
from choregos_servicenow.errors import ServiceNowError
from choregos_servicenow.fake import DATE_FORMAT, FakeServiceNow, sys_id_of

from .conftest import Mcp


async def test_fetch_item_maps_an_incident_to_a_work_item(mcp: Mcp) -> None:
    item = await mcp.call("tracker_fetch_item", key="INC0010006")
    assert item["key"] == "INC0010006"
    assert item["title"] == "VPN disconnects every 10 minutes"
    assert item["state"] == "In Progress"
    assert item["item_type"] == "incident"
    assert item["author"] == "Abel Tuter"
    assert item["url"] == (f"https://fake.service-now.test/nav_to.do?uri=incident.do?sys_id={sys_id_of('INC0010006')}")
    assert item["created_at"].endswith("Z")
    assert item["fields"]["assignment_group"] == {"value": sys_id_of("group:Network"), "display": "Network"}
    assert [c["visibility"] for c in item["comments"]] == ["internal", "customer"]  # oldest first


async def test_fetch_item_flags_a_vip_caller(mcp: Mcp) -> None:
    assert (await mcp.call("tracker_fetch_item", key="INC0010004"))["fields"]["caller_vip"] is True
    assert (await mcp.call("tracker_fetch_item", key="INC0010003"))["fields"]["caller_vip"] is False


async def test_fetch_item_on_request_items_and_changes(mcp: Mcp) -> None:
    assert (await mcp.call("tracker_fetch_item", key="RITM0010001"))["item_type"] == "sc_req_item"
    assert (await mcp.call("tracker_fetch_item", key="CHG0010001"))["state"] == "Closed"
    assert "not an incident" in await mcp.error("tracker_fetch_item", key="PRB0010001")
    assert "no incident" in await mcp.error("tracker_fetch_item", key="INC9999999")


async def test_create_item_is_idempotent_and_never_takes_priority(mcp: Mcp, fake: FakeServiceNow) -> None:
    first = await mcp.call("tracker_create_item", title="Disk full", body="On db-prod-01.", action_key="k1")
    again = await mcp.call("tracker_create_item", title="Disk full", body="On db-prod-01.", action_key="k1")
    assert first["created"] is True
    assert again == {**first, "created": False}
    assert first["key"].startswith("INC")
    assert "priority" in await mcp.error("tracker_create_item", title="x", body="y", fields={"priority": "1"})
    assert "not accepted" in await mcp.error("tracker_create_item", title="x", body="y", fields={"state": "6"})


async def test_set_state_by_label(mcp: Mcp, fake: FakeServiceNow) -> None:
    result = await mcp.call("tracker_set_state", key="INC0010003", status="on hold", label="triaged")
    assert result == {"key": "INC0010003", "changed": True, "state": "On Hold", "ignored": ["label"]}
    assert fake.tables["incident"][sys_id_of("INC0010003")]["state"] == "3"
    again = await mcp.call("tracker_set_state", key="INC0010003", status="On Hold")
    assert again["changed"] is False
    assert "not a state" in await mcp.error("tracker_set_state", key="INC0010003", status="Done-ish")
    assert "tracker_close" in await mcp.error("tracker_set_state", key="INC0010003", status="Resolved")


async def test_comment_is_an_internal_work_note_and_idempotent(mcp: Mcp, fake: FakeServiceNow) -> None:
    first = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c1")
    again = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c1")
    assert first == {"key": "INC0010011", "id": "[choregos:c1]", "written": True}
    assert again["written"] is False
    assert [e["element"] for e in fake.journal(sys_id_of("INC0010011"))] == ["work_notes"]


async def test_upsert_status_adds_a_note_only_when_the_status_changes(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010011")
    assert (await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Triage", marker="s"))["written"]
    assert not (await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Triage", marker="s"))["written"]
    assert (await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Review", marker="s"))["written"]
    assert len(fake.journal(sys_id)) == 2
    item = await mcp.call("tracker_fetch_item", key="INC0010011")
    assert {c["marker"] for c in item["comments"]} == {"s"}


async def test_close_each_kind_of_item_once(mcp: Mcp, fake: FakeServiceNow) -> None:
    closed = await mcp.call("tracker_close", key="INC0010011")
    assert closed == {"key": "INC0010011", "closed": True, "state": "Resolved"}
    incident = fake.tables["incident"][sys_id_of("INC0010011")]
    assert (incident["state"], incident["close_code"]) == ("6", "Solution provided")
    assert (await mcp.call("tracker_close", key="INC0010011"))["closed"] is False
    ritm = await mcp.call("tracker_close", key="RITM0010001", close_notes="Delivered.")
    assert ritm["state"] == "Closed Complete"
    assert (await mcp.call("tracker_close", key="CHG0010001"))["closed"] is False  # already closed


async def test_list_candidates_reads_the_configured_queue(mcp: Mcp) -> None:
    result = await mcp.call("tracker_list_candidates")
    # Service Desk (the fake's default queue): open incidents and request items, the most recently updated
    # first across the tables.
    assert result == {
        "keys": [
            "INC0012399",
            "INC0010005",
            "INC0010004",
            "INC0010003",
            "INC0010002",
            "INC0010001",
            "INC0010011",
            "RITM0010001",
        ],
        "total": 8,
        "truncated": False,
    }
    only = await mcp.call("tracker_list_candidates", tables=["sc_req_item"])
    assert only["keys"] == ["RITM0010001"]
    assert "not a tracker table" in await mcp.error("tracker_list_candidates", tables=["problem"])


async def test_parse_event_verifies_then_normalises(mcp: Mcp) -> None:
    body = json.dumps(
        {"table": "incident", "sys_id": sys_id_of("INC0010001"), "number": "INC0010001", "sys_mod_count": 3}
    )
    good = await mcp.call("tracker_parse_event", headers={"X-Choregos-Secret": "fake-webhook-secret"}, body=body)
    assert good == {
        "verified": True,
        "events": [
            {"kind": "tracker.item.updated", "item_key": "INC0010001", "delivery_id": f"{sys_id_of('INC0010001')}:3"}
        ],
    }
    bad = await mcp.call("tracker_parse_event", headers={"X-Choregos-Secret": "nope"}, body=body)
    assert bad == {"verified": False, "events": []}


# ───── what a real instance does that the fake did not (an Australia PDI, 2026-11-21) ─────


async def test_a_request_item_takes_the_task_states(mcp: Mcp, fake: FakeServiceNow) -> None:
    """A real instance holds the request item's states on `task`: reading `sc_req_item` alone found none,
    so every state of a request item was refused and it could not be closed."""
    assert not [c for c in fake.rows("sys_choice") if c["name"] == "sc_req_item" and c["element"] == "state"]
    moved = await mcp.call("tracker_set_state", key="RITM0010001", status="Work in Progress")
    assert moved["changed"] is True
    assert fake.tables["sc_req_item"][sys_id_of("RITM0010001")]["state"] == "2"
    closed = await mcp.call("tracker_close", key="RITM0010001", close_notes="Delivered.")
    assert closed == {"key": "RITM0010001", "closed": True, "state": "Closed Complete"}


async def test_states_without_sys_choice_are_the_out_of_box_ones(mcp: Mcp, fake: FakeServiceNow) -> None:
    """`sys_choice` refuses a user with `itil` and not the plugin's role (403): the states are then
    ServiceNow's own, not an error on every transition."""
    fake.choices_readable = False
    result = await mcp.call("tracker_set_state", key="INC0010003", status="On Hold")
    assert result["state"] == "On Hold"
    assert fake.tables["incident"][sys_id_of("INC0010003")]["state"] == "3"
    ritm = await mcp.call("tracker_close", key="RITM0010001")
    assert ritm["state"] == "Closed Complete"


async def test_an_unreadable_journal_does_not_write_twice(mcp: Mcp, fake: FakeServiceNow) -> None:
    """`sys_journal_field` answers no row to a user without its read ACL: the work note's marker was never
    found, and a replayed comment or status wrote a second note. The record's own journal fields are read."""
    fake.journal_readable = False
    fake.tz_offset_hours = -8  # the instance's users read Pacific time by default
    sys_id = sys_id_of("INC0010011")
    first = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c1")
    again = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c1")
    assert (first["written"], again["written"]) == (True, False)
    assert [(await mcp.call("tracker_upsert_status", key="INC0010011", markdown="v1", marker="s"))["written"]
            for _ in range(2)] == [True, False]  # fmt: skip
    said = [(await mcp.call("add_comment", table="incident", sys_id=sys_id, text="We are on it."))["written"]
            for _ in range(2)]  # fmt: skip
    assert said == [True, False]
    notes = [e for e in fake.journal(sys_id) if e["value"].startswith(("Run started.", "v1", "We are on it."))]
    assert len(notes) == 3

    item = await mcp.call("tracker_fetch_item", key="INC0010011")
    ours = [c for c in item["comments"] if c["body"].startswith(("Run started.", "v1", "We are on it."))]
    assert [c["marker"] for c in ours if c["visibility"] == "internal"] == ["c1", "s"]
    assert [c["visibility"] for c in ours].count("customer") == 1
    stored = {e["value"].split("\n")[0]: e["sys_created_on"] for e in notes}
    # The header's local time, back in UTC: the same instant as the stored entry.
    assert {c["body"].split("\n")[0]: c["ts"] for c in ours} == {
        k: v.replace(" ", "T") + "Z" for k, v in stored.items()
    }


async def test_a_creation_whose_key_the_instance_dropped_says_so(mcp: Mcp, fake: FakeServiceNow) -> None:
    """An `itil` user cannot create `correlation_id`: ServiceNow files the incident without it, and a
    replay files a second one. The answer says so (the platform would retry a failure, filing more)."""
    fake.unwritable = {("incident", "correlation_id"), ("change_request", "correlation_id")}
    created = await mcp.call("tracker_create_item", title="Disk full", body="On db-prod-01.", action_key="k1")
    assert created["created"] is True
    assert created["idempotent"] is False
    assert "incident.correlation_id" in created["warning"]
    change = await mcp.call(
        "create_change_request", short_description="Reboot", description="d", implementation_plan="i",
        backout_plan="b", test_plan="t", action_key="c1",
    )  # fmt: skip
    assert change["idempotent"] is False
    fake.unwritable = set()
    kept = await mcp.call("tracker_create_item", title="Disk full", body="On db-prod-02.", action_key="k2")
    assert "idempotent" not in kept


# ───── a change request moves along its change model (graalsystems/tranibor-agents#24) ─────


async def _filed_change(mcp: Mcp) -> tuple[str, str]:
    filed = await mcp.call(
        "create_change_request", short_description="Reboot", description="d", implementation_plan="i",
        backout_plan="b", test_plan="t", action_key="chg-model",
    )  # fmt: skip
    return str(filed["record"]["fields"]["number"]), str(filed["record"]["sys_id"])


async def test_the_table_api_cannot_move_a_change(runtime: Runtime, fake: FakeServiceNow) -> None:
    """What the Australia PDI answered to a Table API PATCH of a change's state (the fake plays it)."""
    sys_id = sys_id_of("CHG0003311")
    fake.tables["change_request"][sys_id]["state"] = "-5"
    with pytest.raises(ServiceNowError, match=r"403.*Change Model: Check State Transition"):
        await runtime.client.update("change_request", sys_id, {"state": "-4"})
    assert fake.tables["change_request"][sys_id]["state"] == "-5"


async def test_a_change_moves_through_the_change_management_api(mcp: Mcp, fake: FakeServiceNow) -> None:
    key, sys_id = await _filed_change(mcp)
    moved = await mcp.call("tracker_set_state", key=key, status="assess")
    assert moved == {"key": key, "changed": True, "state": "Assess", "ignored": []}
    assert fake.tables["change_request"][sys_id]["state"] == "-4"
    writes = [r for r in fake.requests if r.method == "PATCH"]
    assert [r.url.path for r in writes] == [f"/api/sn_chg_rest/change/{sys_id}"]
    # A state the model does not reach from here is refused before any write, naming those it reaches.
    refused = await mcp.error("tracker_set_state", key=key, status="Implement")
    assert "not reachable" in refused and "Authorize" in refused and "Canceled" in refused, refused
    assert len([r for r in fake.requests if r.method == "PATCH"]) == 1
    # Incidents and request items keep the Table API.
    await mcp.call("tracker_set_state", key="INC0010003", status="On Hold")
    assert [r for r in fake.requests if r.method == "PATCH"][-1].url.path.startswith("/api/now/table/incident/")


async def test_a_change_closes_from_review_through_the_change_management_api(mcp: Mcp, fake: FakeServiceNow) -> None:
    key, sys_id = await _filed_change(mcp)
    # From New, the model does not reach Closed: refused, and nothing written (no CAB is skipped).
    refused = await mcp.error("tracker_close", key=key)
    assert "not reachable" in refused and "Assess" in refused, refused
    assert fake.tables["change_request"][sys_id]["state"] == "-5"
    fake.tables["change_request"][sys_id]["state"] = "0"  # Review: its approvals happened on the instance
    closed = await mcp.call("tracker_close", key=key, close_notes="Delivered.")
    assert closed == {"key": key, "closed": True, "state": "Closed"}
    row = fake.tables["change_request"][sys_id]
    assert (row["state"], row["close_code"], row["close_notes"]) == ("3", "successful", "Delivered.")
    assert [r for r in fake.requests if r.method == "PATCH"][-1].url.path == f"/api/sn_chg_rest/change/{sys_id}"
    assert (await mcp.call("tracker_close", key=key))["closed"] is False


# ───── the queue poll pages, and says when it stops (graalsystems/tranibor-agents#25) ─────


def queue(fake: FakeServiceNow, count: int) -> list[str]:
    """`count` open incidents in the Service Desk queue, updated a second apart, the last the newest."""
    now = datetime.now(UTC).replace(microsecond=0)
    numbers = []
    for i in range(count):
        stamp = (now - timedelta(seconds=count - i)).strftime(DATE_FORMAT)
        number = f"INC003{i:04d}"
        fake.insert("incident", {"number": number, "short_description": f"Queued {i}", "state": "1",
                                 "active": "true", "assignment_group": sys_id_of("group:Service Desk"),
                                 "sys_created_on": stamp, "sys_updated_on": stamp})  # fmt: skip
        numbers.append(number)
    return numbers


async def test_the_queue_poll_reads_past_one_page(mcp: Mcp, fake: FakeServiceNow) -> None:
    numbers = queue(fake, 80)
    everything = await mcp.call("tracker_list_candidates", limit=200)
    assert set(numbers) <= set(everything["keys"])
    assert (everything["total"], everything["truncated"]) == (len(everything["keys"]), False)
    offsets = [r.url.params.get("sysparm_offset") for r in fake.requests if r.url.path == "/api/now/table/incident"]
    assert "50" in offsets, offsets


async def test_the_queue_poll_says_when_its_bound_cut_it_and_serves_the_newest_first(
    mcp: Mcp, fake: FakeServiceNow
) -> None:
    numbers = queue(fake, 80)
    found = await mcp.call("tracker_list_candidates")  # the core asks for 50
    assert len(found["keys"]) == 50
    assert found["truncated"] is True and found["total"] >= 80
    assert found["keys"][:3] == numbers[::-1][:3]  # a new incident is polled, whatever waits behind it
    # The contract's bound: never more than 200 keys, whatever is asked.
    assert len((await mcp.call("tracker_list_candidates", limit=500))["keys"]) <= 200
