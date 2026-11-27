# SPDX-License-Identifier: Apache-2.0
"""The pure plan, and the record rules."""

from __future__ import annotations

from typing import Any

import pytest

from tranibor_dns_godaddy.errors import Refused
from tranibor_dns_godaddy.plan import Desired, Key
from tranibor_dns_godaddy.plan import plan as _plan
from tranibor_dns_godaddy.records import (
    Record,
    cname_problems,
    norm_value,
    normalise_name,
    validate,
)


def rec(i: int, t: str, sub: str, value: str, ttl: int = 3600) -> Record:
    return {"id": str(i), "zone": "example.com", "type": t, "name": sub, "value": value, "ttl": ttl}


def plan(zone: str, live: list[Record], desired: list[Desired], clear: list[Key]) -> dict[str, Any]:
    return _plan(zone, live, desired, clear, stages_changes=False)


LIVE = [
    rec(1, "A", "", "192.0.2.10"),
    rec(2, "A", "www", "192.0.2.10", 86400),
    rec(3, "CNAME", "app", "lb.example.com.", 600),
    rec(4, "A", "lb", "192.0.2.20", 600),
]


def test_nothing_to_do_is_an_empty_plan_without_publish() -> None:
    result = plan("example.com", LIVE, [Desired(type="A", name="www", value="192.0.2.10")], [])
    assert result["valid"] and result["summary"] == {"create": 0, "update": 0, "delete": 0, "unchanged": 1}
    assert result["proposed_calls"] == [] and result["needs_publish"] is False


def test_a_single_record_on_each_side_is_an_update_and_there_is_no_publish_call() -> None:
    result = plan("example.com", LIVE, [Desired(type="A", name="www", value="192.0.2.99", ttl=600)], [])
    assert result["summary"]["update"] == 1
    assert result["updates"][0]["before"] == {"value": "192.0.2.10", "ttl": 86400}
    assert result["updates"][0]["after"] == {"value": "192.0.2.99", "ttl": 600}
    assert result["proposed_calls"] == [
        {"tool": "update_record", "arguments": {"zone": "example.com", "id": "2", "value": "192.0.2.99", "ttl": 600}},
    ]
    assert any("86400" in w for w in result["warnings"])
    assert result["propagation_seconds_after_change"] == 86400


def test_a_ttl_only_change_is_an_update_that_keeps_the_target() -> None:
    result = plan("example.com", LIVE, [Desired(type="A", name="www", value="192.0.2.10", ttl=600)], [])
    assert result["proposed_calls"][0] == {
        "tool": "update_record",
        "arguments": {"zone": "example.com", "id": "2", "ttl": 600},
    }


def test_creates_deletes_and_order() -> None:
    result = plan(
        "example.com",
        LIVE,
        [Desired(type="A", name="new", value="192.0.2.50")],
        [Key(type="CNAME", name="app")],
    )
    assert [c["tool"] for c in result["proposed_calls"]] == ["delete_record", "create_record"]


def test_cname_swap_deletes_before_it_creates() -> None:
    result = plan(
        "example.com",
        LIVE,
        [Desired(type="A", name="app", value="192.0.2.77")],
        [Key(type="CNAME", name="app")],
    )
    # desired A and cleared CNAME are different pairs: the A is created, the CNAME deleted, the result is valid
    assert result["valid"], result["errors"]
    assert [c["tool"] for c in result["proposed_calls"]][:2] == ["delete_record", "create_record"]


def test_a_cname_beside_an_existing_record_is_an_error_with_no_calls() -> None:
    result = plan("example.com", LIVE, [Desired(type="CNAME", name="www", value="example.com.")], [])
    assert not result["valid"] and result["proposed_calls"] == []
    assert any("beside" in e for e in result["errors"])


def test_a_cname_at_the_apex_is_an_error() -> None:
    result = plan("example.com", LIVE, [Desired(type="CNAME", name="", value="x.example.net.")], [])
    assert not result["valid"] and "apex" in result["errors"][0]


def test_bad_input_is_reported_not_raised() -> None:
    result = plan("example.com", LIVE, [Desired(type="A", name="x", value="not-an-ip")], [Key(type="NS", name="")])
    assert len(result["errors"]) == 2 and result["proposed_calls"] == []


def test_a_pre_existing_violation_is_a_warning_not_an_error() -> None:
    live = [*LIVE, rec(9, "CNAME", "www", "x.example.net.")]
    result = plan("example.com", live, [Desired(type="A", name="www", value="192.0.2.10")], [])
    assert result["valid"]
    assert any("already so" in w for w in result["warnings"])


def test_trailing_dot_case_and_quotes_do_not_make_a_difference() -> None:
    assert norm_value("CNAME", "LB.Example.com.") == norm_value("CNAME", "lb.example.com")
    assert norm_value("TXT", '"v=spf1 -all"') == "v=spf1 -all"
    assert norm_value("AAAA", "2001:0db8:0:0:0:0:0:1") == "2001:db8::1"
    assert norm_value("MX", "10 Mail.Example.com.") == "10 mail.example.com"


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("A", "", "300.1.1.1", 0), "IPv4"),
        (("AAAA", "", "192.0.2.1", 0), "IPv6"),
        (("CNAME", "", "a.example.net.", 0), "apex"),
        (("NS", "", "ns.example.net.", 0), "not one of"),
        (("MX", "", "mail.example.net.", 0), "priority"),
        (("A", "x", "192.0.2.1", -1), "ttl"),
        (("A", "x", "192.0.2.1", 10**9), "ttl"),
        (("TXT", "x", "a\x01b", 0), "control"),
        (("BOGUS", "x", "1", 0), "not one of"),
        (("CNAME", "x", "a b", 0), "one host"),
    ],
)
def test_validate_refuses(args: tuple[str, str, str, int], why: str) -> None:
    with pytest.raises(Refused, match=why):
        validate(*args)


def test_name_normalisation() -> None:
    assert normalise_name("@") == "" and normalise_name("WWW.") == "www" and normalise_name("_dmarc") == "_dmarc"
    with pytest.raises(Refused):
        normalise_name("bad name")


def test_cname_problems() -> None:
    assert cname_problems([rec(1, "CNAME", "a", "x."), rec(2, "CNAME", "a", "y.")])
    assert not cname_problems([rec(1, "CNAME", "a", "x."), rec(2, "A", "b", "192.0.2.1")])
