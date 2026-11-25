# SPDX-License-Identifier: Apache-2.0
"""The pure plan and audit, and the record rules."""

from __future__ import annotations

import pytest

from tranibor_ovh_dns.errors import Refused
from tranibor_ovh_dns.plan import Desired, Key, audit_zone, plan
from tranibor_ovh_dns.records import (
    Record,
    cname_problems,
    norm_target,
    normalise_sub_domain,
    validate,
)


def rec(i: int, t: str, sub: str, target: str, ttl: int = 3600) -> Record:
    return {"id": i, "field_type": t, "sub_domain": sub, "target": target, "ttl": ttl}


LIVE = [
    rec(1, "A", "", "192.0.2.10"),
    rec(2, "A", "www", "192.0.2.10", 86400),
    rec(3, "CNAME", "app", "lb.example.com.", 300),
    rec(4, "A", "lb", "192.0.2.20", 300),
]


def test_nothing_to_do_is_an_empty_plan_without_refresh() -> None:
    result = plan(LIVE, [Desired(field_type="A", sub_domain="www", target="192.0.2.10")], [])
    assert result["valid"] and result["summary"] == {"create": 0, "update": 0, "delete": 0, "unchanged": 1}
    assert result["proposed_calls"] == [] and result["needs_refresh"] is False


def test_a_single_record_on_each_side_is_an_update_and_calls_end_with_refresh() -> None:
    result = plan(LIVE, [Desired(field_type="A", sub_domain="www", target="192.0.2.99", ttl=300)], [])
    assert result["summary"]["update"] == 1
    assert result["updates"][0]["before"] == {"target": "192.0.2.10", "ttl": 86400}
    assert result["updates"][0]["after"] == {"target": "192.0.2.99", "ttl": 300}
    assert result["proposed_calls"] == [
        {"tool": "update_record", "arguments": {"record_id": 2, "target": "192.0.2.99", "ttl": 300}},
        {"tool": "refresh_zone", "arguments": {}},
    ]
    assert any("86400" in w for w in result["warnings"])
    assert result["propagation_seconds_after_refresh"] == 86400


def test_a_ttl_only_change_is_an_update_that_keeps_the_target() -> None:
    result = plan(LIVE, [Desired(field_type="A", sub_domain="www", target="192.0.2.10", ttl=300)], [])
    assert result["proposed_calls"][0] == {"tool": "update_record", "arguments": {"record_id": 2, "ttl": 300}}


def test_creates_deletes_and_order() -> None:
    result = plan(
        LIVE,
        [Desired(field_type="A", sub_domain="new", target="192.0.2.50")],
        [Key(field_type="CNAME", sub_domain="app")],
    )
    assert [c["tool"] for c in result["proposed_calls"]] == ["delete_record", "create_record", "refresh_zone"]


def test_cname_swap_deletes_before_it_creates() -> None:
    result = plan(
        LIVE,
        [Desired(field_type="A", sub_domain="app", target="192.0.2.77")],
        [Key(field_type="CNAME", sub_domain="app")],
    )
    # desired A and cleared CNAME are different pairs: the A is created, the CNAME deleted, the result is valid
    assert result["valid"], result["errors"]
    assert [c["tool"] for c in result["proposed_calls"]][:2] == ["delete_record", "create_record"]


def test_a_cname_beside_an_existing_record_is_an_error_with_no_calls() -> None:
    result = plan(LIVE, [Desired(field_type="CNAME", sub_domain="www", target="example.com.")], [])
    assert not result["valid"] and result["proposed_calls"] == []
    assert any("beside" in e for e in result["errors"])


def test_a_cname_at_the_apex_is_an_error() -> None:
    result = plan(LIVE, [Desired(field_type="CNAME", sub_domain="", target="x.example.net.")], [])
    assert not result["valid"] and "apex" in result["errors"][0]


def test_bad_input_is_reported_not_raised() -> None:
    result = plan(
        LIVE, [Desired(field_type="A", sub_domain="x", target="not-an-ip")], [Key(field_type="NS", sub_domain="")]
    )
    assert len(result["errors"]) == 2 and result["proposed_calls"] == []


def test_a_relative_host_warns() -> None:
    result = plan(LIVE, [Desired(field_type="CNAME", sub_domain="blog", target="host.example.net")], [])
    assert any("trailing dot" in w for w in result["warnings"])


def test_a_pre_existing_violation_is_a_warning_not_an_error() -> None:
    live = [*LIVE, rec(9, "CNAME", "www", "x.example.net.")]
    result = plan(live, [Desired(field_type="A", sub_domain="www", target="192.0.2.10")], [])
    assert result["valid"]
    assert any("already so" in w for w in result["warnings"])


def test_trailing_dot_case_and_quotes_do_not_make_a_difference() -> None:
    assert norm_target("CNAME", "LB.Example.com.") == norm_target("CNAME", "lb.example.com")
    assert norm_target("TXT", '"v=spf1 -all"') == "v=spf1 -all"
    assert norm_target("AAAA", "2001:0db8:0:0:0:0:0:1") == "2001:db8::1"
    assert norm_target("MX", "10 Mail.Example.com.") == "10 mail.example.com"


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("A", "", "300.1.1.1", 0), "IPv4"),
        (("AAAA", "", "192.0.2.1", 0), "IPv6"),
        (("CNAME", "", "a.example.net.", 0), "apex"),
        (("NS", "", "ns.example.net.", 0), "delegation"),
        (("MX", "", "mail.example.net.", 0), "priority"),
        (("A", "x", "192.0.2.1", -1), "ttl"),
        (("A", "x", "192.0.2.1", 10**9), "ttl"),
        (("TXT", "x", "a\x01b", 0), "control"),
        (("BOGUS", "x", "1", 0), "field_type"),
        (("CNAME", "x", "a b", 0), "one host"),
    ],
)
def test_validate_refuses(args: tuple[str, str, str, int], why: str) -> None:
    with pytest.raises(Refused, match=why):
        validate(*args)


def test_sub_domain_normalisation() -> None:
    assert (
        normalise_sub_domain("@") == ""
        and normalise_sub_domain("WWW.") == "www"
        and normalise_sub_domain("_dmarc") == "_dmarc"
    )
    with pytest.raises(Refused):
        normalise_sub_domain("bad name")


def test_cname_problems() -> None:
    assert cname_problems([rec(1, "CNAME", "a", "x."), rec(2, "CNAME", "a", "y.")])
    assert not cname_problems([rec(1, "CNAME", "a", "x."), rec(2, "A", "b", "192.0.2.1")])


def test_the_audit_finds_the_usual_suspects() -> None:
    records = [
        rec(1, "A", "", "192.0.2.10", 86400),
        rec(2, "MX", "", "10 mail.example.com."),
        rec(3, "CNAME", "old", "gone.example.com."),
        rec(4, "CNAME", "ok", "mail.example.com."),
        rec(5, "A", "mail", "192.0.2.10"),
        rec(6, "TXT", "", '"v=spf1 -all"'),
        rec(7, "TXT", "", "v=spf1 include:x.example.net -all"),
        rec(8, "A", "dup", "192.0.2.1"),
        rec(9, "A", "dup", "192.0.2.1"),
    ]
    report = audit_zone("example.com", records)
    codes = {(f["code"], f["name"]) for f in report["findings"]}
    assert ("dangling_candidate", "old") in codes
    assert ("dangling_candidate", "ok") not in codes
    assert ("multiple_spf", "@") in codes and ("no_dmarc", "_dmarc") in codes and ("no_caa", "@") in codes
    assert ("duplicate", "dup") in codes and ("long_ttl", "@") in codes
    assert any(f["code"] == "address_inventory" and "192.0.2.10" in f["detail"] for f in report["findings"])
    assert report["findings"][0]["severity"] == "error"
