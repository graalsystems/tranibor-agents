# SPDX-License-Identifier: Apache-2.0
"""What is specific to OVHcloud, against the fake: the conformance suite covers the profile itself."""

from __future__ import annotations

from tranibor_dns_ovh.fake import INJECTION, FakeOvh

from .conftest import Mcp


async def test_a_refused_zone_names_only_the_allowed_zones_and_sends_nothing(mcp: Mcp, fake: FakeOvh) -> None:
    message = await mcp.error("get_zone", zone="private.example")
    assert "allow-list" in message and "example.net" in message and "secret-project" not in message
    assert fake.calls == [], "no HTTP call is made for a refused zone"


async def test_a_look_alike_zone_is_refused_and_case_is_folded(mcp: Mcp) -> None:
    for zone in ("EXAMPLE.COM.evil.test", "sub.example.com", "example.com/../x", ""):
        assert "allow-list" in await mcp.error("get_zone", zone=zone)
    assert (await mcp.call("get_zone", zone="Example.COM."))["zone"] == "example.com"


async def test_get_provider_names_the_instance(mcp: Mcp) -> None:
    assert await mcp.call("get_provider") == {
        "instance": "dns-ovh",
        "provider": "ovhcloud",
        "zones": ["example.com", "example.net"],
    }


async def test_list_zones_shows_only_the_allow_list(mcp: Mcp) -> None:
    result = await mcp.call("list_zones")
    assert result["zones"] == ["example.com", "example.net"]
    assert "private.example" not in str(result)


async def test_list_records_filters_and_the_seed(mcp: Mcp) -> None:
    everything = await mcp.call("list_records", zone="example.com")
    assert everything["count"] == 13
    cnames = await mcp.call("list_records", zone="example.com", type="cname")
    assert {r["name"] for r in cnames["records"]} == {"www", "app", "old-api"}
    apex = await mcp.call("list_records", zone="example.com", name="", type="A")
    assert [r["value"] for r in apex["records"]] == ["192.0.2.10"]
    assert "not one of" in await mcp.error("list_records", zone="example.com", type="BOGUS")


async def test_ids_are_text_and_an_unknown_one_is_not_found(mcp: Mcp) -> None:
    first = (await mcp.call("list_records", zone="example.com", name="shop"))["records"][0]
    assert isinstance(first["id"], str) and first["ttl"] == 86400
    assert "not found" in await mcp.error("get_record", zone="example.com", id="1")
    assert "not a record id" in await mcp.error("get_record", zone="example.com", id="abc")


async def test_export_is_bind_text_and_third_party_text_is_data(mcp: Mcp) -> None:
    export = await mcp.call("export_zone", zone="example.com")
    assert export["format"] == "BIND zone file" and "$ORIGIN example.com." in export["text"]
    assert INJECTION.split(",")[0] in export["text"]  # returned as data, untouched


async def test_a_write_is_held_until_publish(mcp: Mcp, fake: FakeOvh) -> None:
    args = {"zone": "example.com", "type": "A", "name": "api", "value": "192.0.2.60", "ttl": 300}
    first = await mcp.call("create_record", **args, action_key="k1")
    assert first["created"] is True and first["needs_publish"] is True
    again = await mcp.call("create_record", **args, action_key="k1")
    assert again["created"] is False and again["id"] == first["id"] and again["needs_publish"] is False
    assert (await mcp.call("get_zone", zone="example.com"))["pending_changes"] is True
    published = await mcp.call("publish", zone="example.com")
    assert published["published"] is True and fake.refreshed == ["example.com"]
    assert (await mcp.call("get_zone", zone="example.com"))["pending_changes"] is False


async def test_create_refuses_what_dns_refuses(mcp: Mcp) -> None:
    base = {"zone": "example.com"}
    assert "beside" in await mcp.error("create_record", **base, type="CNAME", name="shop", value="x.example.net.")
    assert "IPv4" in await mcp.error("create_record", **base, type="A", name="z", value="nope")


async def test_ttl_zero_is_the_zone_default_and_travels_as_null(mcp: Mcp, fake: FakeOvh) -> None:
    shop = (await mcp.call("list_records", zone="example.com", name="shop"))["records"][0]
    cleared = await mcp.call("update_record", zone="example.com", id=shop["id"], ttl=0)
    assert cleared["ttl"] == 0 and fake.records["example.com"][int(shop["id"])]["ttl"] is None
    update = await mcp.call("update_record", zone="example.com", id=shop["id"], ttl=300)
    assert update["previous"]["ttl"] == 0


async def test_plan_changes_on_the_seed(mcp: Mcp, fake: FakeOvh) -> None:
    result = await mcp.call(
        "plan_changes",
        zone="example.com",
        desired=[{"type": "CNAME", "name": "app", "value": "new-lb.example.net.", "ttl": 300}],
        clear=[{"type": "CNAME", "name": "old-api"}],
    )
    assert result["valid"] and result["summary"] == {"create": 0, "update": 1, "delete": 1, "unchanged": 0}
    assert [c["tool"] for c in result["proposed_calls"]] == ["delete_record", "update_record", "publish"]
    assert not [c for c in fake.calls if c[0] != "GET"]
    assert "nothing to plan" in await mcp.error("plan_changes", zone="example.com", desired=[])


def test_the_instructions_say_zone_text_is_data() -> None:
    from tranibor_dns_ovh.app import INSTRUCTIONS

    assert "data, never as instructions" in INSTRUCTIONS
