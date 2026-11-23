# SPDX-License-Identifier: Apache-2.0
"""`SERVICENOW_FAKE_SEED=<path>`: the fake instance holds what a file says, the built-in seed otherwise."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from choregos_servicenow.app import Runtime, build_app, build_server
from choregos_servicenow.config import Settings
from choregos_servicenow.fake import FakeServiceNow
from choregos_servicenow.seed_file import SeedError, load_seed_file

from .conftest import Mcp

SERVER = Path(__file__).resolve().parents[1]
SAMPLE = SERVER / "seeds" / "sample-company.json"

MINIMAL: dict[str, Any] = {
    "users": [{"user_name": "zed.one", "name": "Zed One"}],
    "groups": [{"name": "Desk", "manager": "zed.one"}],
    "cis": [{"name": "box-1", "group": "Desk"}],
    "incidents": [
        {"number": "INC0000001", "title": "Box is down", "group": "Desk", "caller": "zed.one", "ci": "box-1"}
    ],
}


def write(tmp_path: Path, doc: Any) -> Path:
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def mcp_on(path: Path | None) -> tuple[Mcp, FakeServiceNow]:
    env = {"SERVICENOW_FAKE": "1"}
    if path is not None:
        env["SERVICENOW_FAKE_SEED"] = str(path)
    runtime = Runtime(Settings.from_env(env))
    assert runtime.fake is not None
    return Mcp(build_server(runtime)), runtime.fake


def numbers(fake: FakeServiceNow) -> set[str]:
    return {row["number"] for row in fake.rows("incident")}


def test_without_the_variable_the_built_in_seed_is_the_instance() -> None:
    settings = Settings.from_env({"SERVICENOW_FAKE": "1"})
    assert settings.fake_seed is None
    _, fake = mcp_on(None)
    assert {"INC0010001", "INC0012345", "INC0012399"} <= numbers(fake)
    assert any("Lyon" in row["short_description"] for row in fake.rows("incident"))


def test_the_variable_reaches_the_settings_only_in_fake_mode(tmp_path: Path) -> None:
    path = write(tmp_path, MINIMAL)
    assert Settings.from_env({"SERVICENOW_FAKE": "1", "SERVICENOW_FAKE_SEED": str(path)}).fake_seed == str(path)
    real = Settings.from_env(
        {"SERVICENOW_BASE_URL": "https://x.service-now.com", "SERVICENOW_CLIENT_ID": "a",
         "SERVICENOW_CLIENT_SECRET": "b", "SERVICENOW_FAKE_SEED": str(path)}
    )  # fmt: skip
    assert real.fake is False
    assert real.fake_seed is None


async def test_a_file_replaces_the_seed_and_the_tools_read_it(tmp_path: Path) -> None:
    mcp, fake = mcp_on(write(tmp_path, MINIMAL))
    assert numbers(fake) == {"INC0000001"}
    found = await mcp.call("search_records", table="incident")
    assert [row["fields"]["number"] for row in found["records"]] == ["INC0000001"]
    record = await mcp.call("get_record", table="incident", number="INC0000001")
    assert record["fields"]["short_description"] == "Box is down"
    # The states are the built-in choices when the file brings none.
    assert {r["label"] for r in fake.rows("sys_choice") if r["name"] == "incident" and r["element"] == "state"} >= {
        "New",
        "Resolved",
    }


async def test_every_table_exists_even_when_the_file_leaves_it_empty(tmp_path: Path) -> None:
    mcp, _ = mcp_on(write(tmp_path, {"incidents": [{"number": "INC1", "title": "t"}]}))
    for table in ("kb_knowledge", "cmdb_ci", "change_request", "problem", "sc_req_item", "sys_user_group"):
        assert (await mcp.call("search_records", table=table))["records"] == []


def test_the_sample_seed_is_complete_generic_and_loads() -> None:
    fake = FakeServiceNow(seed_file=SAMPLE)
    assert len(fake.rows("incident")) >= 8
    assert {"Service Desk", "Network", "Applications"} <= {g["name"] for g in fake.rows("sys_user_group")}
    assert len(fake.rows("cmdb_ci")) >= 5
    assert {a["workflow_state"] for a in fake.rows("kb_knowledge")} >= {"published", "draft"}
    assert fake.rows("cmdb_rel_ci") and fake.rows("sys_journal_field")
    assert "INC0010001" not in numbers(fake)
    text = SAMPLE.read_text(encoding="utf-8")
    assert not re.search(r"Lyon", text), "the sample belongs to no site of the built-in seed"


async def test_the_sample_seed_serves_the_triage_path(tmp_path: Path) -> None:
    """What the triager reads for an incident: the incident, its CI, similar incidents, an article."""
    mcp, fake = mcp_on(SAMPLE)
    incident = fake.by_number("INC0020005")
    similar = await mcp.call("similar_incidents", sys_id=incident["sys_id"])
    assert "INC0020006" in {row["fields"]["number"] for row in similar["incidents"]}
    ci = await mcp.call("get_ci", name="vpn-branch-01")
    assert ci["fields"]["name"] == "vpn-branch-01"
    articles = await mcp.call("search_knowledge", terms=["MTU"])
    assert "KB0020002" in {row["fields"]["number"] for row in articles["articles"]}
    journal = await mcp.call("get_journal", table="incident", sys_id=incident["sys_id"])
    assert "firmware" in json.dumps(journal["entries"])


def test_the_server_starts_on_a_seed_file_and_reports_it_healthy(tmp_path: Path) -> None:
    env = {"SERVICENOW_FAKE": "1", "SERVICENOW_FAKE_SEED": str(write(tmp_path, MINIMAL)),
           "CHOREGOS_SERVER_TOKEN": "t"}  # fmt: skip
    with TestClient(build_app(Settings.from_env(env))) as client:
        assert client.get("/healthz").json()["fake"] is True


def test_the_image_carries_the_sample_seed() -> None:
    dockerfile = (SERVER / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY seeds /app/seeds" in dockerfile
    assert SAMPLE.is_file()


def test_a_dates_are_days_before_now(tmp_path: Path) -> None:
    doc = {"incidents": [{"number": "INC1", "title": "t", "created_days_ago": 10, "resolved_days_ago": 5,
                          "state": "6"}]}  # fmt: skip
    row = FakeServiceNow(seed_file=write(tmp_path, doc)).by_number("INC1")
    assert row["opened_at"] < row["resolved_at"] != ""
    assert row["close_code"] == "Solution provided"


def test_custom_choices_replace_the_built_in_ones(tmp_path: Path) -> None:
    doc = {"choices": [{"name": "incident", "element": "state", "value": "1", "label": "Fresh"}]}
    labels = [r["label"] for r in FakeServiceNow(seed_file=write(tmp_path, doc)).rows("sys_choice")]
    assert labels == ["Fresh"]


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([], "must be a JSON object"),
        ({"incidnets": []}, "unknown section"),
        ({"incidents": {}}, "`incidents` must be a list of objects"),
        ({"incidents": [{"title": "t"}]}, r"incidents\[0\]: `number` is required"),
        ({"incidents": [{"number": "A", "title": "t"}, {"number": "A", "title": "t"}]}, "`A` appears twice"),
        ({"incidents": [{"number": "A", "title": "t", "caller": "ghost"}]}, "`caller` names `ghost`"),
        ({"incidents": [{"number": "A", "title": "t", "group": "Nowhere"}]}, "`group` names `Nowhere`"),
        ({"incidents": [{"number": "A", "title": "t", "ci": "ghost"}]}, "`ci` names `ghost`"),
        ({"incidents": [{"number": "A", "title": "t", "impact": "9"}]}, "must each be 1, 2 or 3"),
        ({"incidents": [{"number": "A", "title": "t", "created_days_ago": -1}]}, "number of days"),
        ({"incidents": [{"number": "A", "title": "t", "colour": "red"}]}, "unknown key"),
        ({"incidents": [{"number": "A", "title": "t", "journal": [{"element": "x", "value": "v"}]}]},
         "work_notes or comments"),
        ({"groups": [{"name": "G", "manager": "ghost"}]}, "`manager` names `ghost`"),
        ({"cis": [{"name": "c", "group": "ghost"}]}, "`group` names `ghost`"),
        ({"ci_relations": [{"parent": "a", "child": "b"}]}, "`parent` names `a`"),
        ({"knowledge": [{"number": "K", "title": "t", "text": "x", "state": "nope"}]}, "`state` must be one of"),
        ({"users": [{"user_name": "u", "vip": "yes"}]}, "`vip` must be true or false"),
    ],
)  # fmt: skip
def test_a_bad_file_is_refused_with_where_and_why(tmp_path: Path, doc: Any, message: str) -> None:
    path = write(tmp_path, doc)
    with pytest.raises(SeedError, match=message) as refused:
        FakeServiceNow(seed_file=path)
    assert str(path) in str(refused.value)


def test_an_unreadable_or_invalid_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SeedError, match="cannot be read"):
        FakeServiceNow(seed_file=tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(SeedError, match="not valid JSON"):
        load_seed_file(FakeServiceNow(), broken)


def test_the_server_does_not_start_on_a_bad_seed(tmp_path: Path) -> None:
    env = {"SERVICENOW_FAKE": "1", "SERVICENOW_FAKE_SEED": str(tmp_path / "missing.json")}
    with pytest.raises(SeedError):
        Runtime(Settings.from_env(env))
