# SPDX-License-Identifier: Apache-2.0
"""The former name, `choregos`, during the window of the core's ADR 0226.

The server writes `Tranibor: <state>` categories and `tranibor-*` extended properties, and still reads
what a server before 0.1.3 left in the mailbox under the former name: a message categorised
`Choregos: …` stays taken (and its thread's follow-ups with it), its state is read, and a state change
replaces the former category rather than adding a second state; a status kept under `choregos-status-…`
is not written again; a reply marked `choregos-action` is the reply already made. The core mirrors a state
by its label, `tranibor:<state>` from 0.30.0 and `choregos:<state>` before: both name the state. A file or
a command that still names the former package or command keeps working.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

import pytest

from tranibor_m365_desk.app import build_app
from tranibor_m365_desk.config import Settings
from tranibor_m365_desk.fake import MAIL_HTML, MAIL_PLAIN, MAIL_TAKEN, FakeMicrosoft, message_id
from tranibor_m365_desk.tracker import PROPERTY_SET, status_property

from .conftest import Mcp, Tracker

PLAIN = f"mail:{MAIL_PLAIN}"
HTML = f"mail:{MAIL_HTML}"
TAKEN = f"mail:{MAIL_TAKEN}"
SERVER = Path(__file__).resolve().parents[1]


def taken_before(fake: FakeMicrosoft, identifier: str, state: str = "In progress") -> None:
    """The message as a server before 0.1.3 left it: categorised under the former prefix."""
    fake.messages[identifier].categories = [f"Choregos: {state}", "Blue category"]


# ─────────────────────────── categories ───────────────────────────


@pytest.mark.parametrize("label", ["tranibor:triage", "choregos:triage", "Tranibor:triage"])
async def test_a_state_mirrored_by_its_label_under_either_prefix(
    tracker: Tracker, fake: FakeMicrosoft, label: str
) -> None:
    """The core 0.30.0 sends `tranibor:<state>`, a core before it `choregos:<state>`: the same state."""
    moved = await tracker("tracker_set_state", key=PLAIN, status=None, label=label, remove_labels=[])
    assert (moved["changed"], moved["state"]) == (True, "triage")
    assert fake.messages[MAIL_PLAIN].categories == ["Tranibor: triage"]


async def test_a_message_categorised_under_the_former_prefix_is_still_taken(
    tracker: Tracker, fake: FakeMicrosoft
) -> None:
    taken_before(fake, MAIL_TAKEN)
    item = await tracker("tracker_fetch_item", key=TAKEN)
    assert (item["state"], item["labels"]) == ("In progress", [])
    thread = fake.messages[MAIL_TAKEN].render(None)["conversationId"]
    fake.add_message("AAkAfollow", "RE: New laptop for Kim", "kim.lee@example.test", "Thanks!", conversation=thread)
    assert (await tracker("tracker_list_candidates", limit=50))["keys"] == [HTML, PLAIN]


async def test_a_state_change_replaces_the_former_category(tracker: Tracker, fake: FakeMicrosoft) -> None:
    taken_before(fake, MAIL_TAKEN)
    moved = await tracker("tracker_set_state", key=TAKEN, status="Waiting for review", label=None, remove_labels=[])
    assert moved["changed"] is True
    assert fake.messages[MAIL_TAKEN].categories == ["Blue category", "Tranibor: Waiting for review"]


async def test_the_same_state_under_the_former_prefix_takes_the_new_one(tracker: Tracker, fake: FakeMicrosoft) -> None:
    taken_before(fake, MAIL_TAKEN)
    moved = await tracker("tracker_set_state", key=TAKEN, status="In progress", label=None, remove_labels=[])
    assert (moved["changed"], moved["state"]) == (True, "In progress")
    assert fake.messages[MAIL_TAKEN].categories == ["Blue category", "Tranibor: In progress"]
    again = await tracker("tracker_set_state", key=TAKEN, status="In progress", label=None, remove_labels=[])
    assert again["changed"] is False


async def test_a_message_closed_under_the_former_prefix_stays_as_it_is(tracker: Tracker, fake: FakeMicrosoft) -> None:
    taken_before(fake, MAIL_TAKEN, state="Done")
    closed = await tracker("tracker_close", key=TAKEN, close_notes="Delivered.", close_code=None)
    assert closed == {"key": TAKEN, "closed": False, "state": "Done"}
    assert fake.messages[MAIL_TAKEN].categories == ["Choregos: Done", "Blue category"]


# ─────────────────────────── extended properties ───────────────────────────


@pytest.mark.parametrize("handed", ["choregos:status", "tranibor:status"])
async def test_a_status_kept_under_the_former_property_is_not_written_again(
    tracker: Tracker, fake: FakeMicrosoft, handed: str
) -> None:
    """The core hands `choregos:status` until its window closes, `tranibor:status` after; a server before
    0.1.3 kept the status under `choregos-status-choregos:status`."""
    former = f"String {PROPERTY_SET} Name choregos-status-choregos:status"
    fake.messages[MAIL_PLAIN].properties[former] = "**Triage**"
    same = await tracker("tracker_upsert_status", key=PLAIN, markdown="**Triage**", marker=handed)
    assert same == {"key": PLAIN, "written": False}
    moved = await tracker("tracker_upsert_status", key=PLAIN, markdown="Review", marker=handed)
    assert moved["written"] is True
    assert fake.messages[MAIL_PLAIN].properties[status_property(handed)] == "Review"
    assert status_property(handed) == f"String {PROPERTY_SET} Name tranibor-status-{handed}"
    again = await tracker("tracker_upsert_status", key=PLAIN, markdown="Review", marker=handed)
    assert again["written"] is False


async def test_a_reply_marked_under_the_former_property_is_the_reply_made(mcp: Mcp, fake: FakeMicrosoft) -> None:
    fake.add_message(message_id(901), "RE: VPN token expired", "it-desk@example.test", "Done.", folder="sentitems")
    fake.messages[message_id(901)].properties[f"String {PROPERTY_SET} Name choregos-action"] = "a9"
    answer = await mcp.call("reply", key=PLAIN, work_item_key=PLAIN, text="Done.", action_key="a9")
    assert (answer["sent"], answer["message_id"]) == (False, message_id(901))
    assert fake.sent_mail == []


async def test_a_draft_marked_under_the_former_property_is_sent_not_duplicated(mcp: Mcp, fake: FakeMicrosoft) -> None:
    fake.add_message(message_id(902), "RE: VPN token expired", "it-desk@example.test", "Draft.", folder="drafts")
    fake.messages[message_id(902)].properties[f"String {PROPERTY_SET} Name choregos-action"] = "a8"
    fake.messages[message_id(902)].to = ["alex.durand@example.test"]
    assert (await mcp.call("reply", key=PLAIN, work_item_key=PLAIN, text="Draft.", action_key="a8"))["sent"] is True
    assert [m["id"] for m in fake.sent_mail] == [message_id(902)]


async def test_a_new_reply_is_marked_under_the_new_property(mcp: Mcp, fake: FakeMicrosoft) -> None:
    sent = await mcp.call("reply", key=PLAIN, work_item_key=PLAIN, text="Reset.", action_key="a7")
    assert fake.messages[sent["message_id"]].properties == {f"String {PROPERTY_SET} Name tranibor-action": "a7"}


# ─────────────────────────── the former package and command ───────────────────────────


def test_the_former_import_name_is_the_package() -> None:
    for name in [m for m in sys.modules if m == "choregos_m365_desk" or m.startswith("choregos_m365_desk.")]:
        del sys.modules[name]
    with pytest.warns(DeprecationWarning, match="tranibor_m365_desk"):
        former = importlib.import_module("choregos_m365_desk")
    assert former is importlib.import_module("tranibor_m365_desk")
    assert importlib.import_module("choregos_m365_desk.app").build_app is build_app
    assert importlib.import_module("choregos_m365_desk.config").Settings is Settings


def test_both_commands_start_the_server() -> None:
    project = tomllib.loads((SERVER / "pyproject.toml").read_text())
    scripts = project["project"]["scripts"]
    assert scripts["tranibor-m365-desk"] == scripts["choregos-m365-desk"] == "tranibor_m365_desk.app:main"
    assert {"src/tranibor_m365_desk", "src/choregos_m365_desk"} <= set(
        project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    )
    assert '"/app/.venv/bin/tranibor-m365-desk"' in (SERVER / "Dockerfile").read_text()
