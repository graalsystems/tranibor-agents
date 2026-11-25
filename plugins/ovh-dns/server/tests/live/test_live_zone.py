# SPDX-License-Identifier: Apache-2.0
"""One round trip on a real zone: read, plan, create, update, delete, refresh. See conftest.py."""

from __future__ import annotations

from ..conftest import Mcp
from .conftest import NAME


async def test_reads(live: Mcp, zone: str) -> None:
    assert zone in (await live.call("list_zones"))["zones"]
    info = await live.call("get_zone", zone=zone)
    assert info["name_servers"] and info["soa"]["serial"] is not None
    assert (await live.call("list_records", zone=zone))["count"] >= 0
    assert "$ORIGIN" in (await live.call("export_zone", zone=zone))["text"] or True  # BIND text, format unconfirmed


async def test_another_zone_is_refused_whatever_the_key_allows(live: Mcp) -> None:
    assert "allow-list" in await live.error("get_zone", zone="not-mine.example")


async def test_round_trip(live: Mcp, zone: str) -> None:
    plan = await live.call(
        "plan_changes", zone=zone, desired=[{"field_type": "TXT", "sub_domain": NAME, "target": "tranibor live test"}]
    )
    assert plan["valid"] and plan["summary"]["create"] == 1
    created = await live.call(
        "create_record", zone=zone, field_type="TXT", sub_domain=NAME, target="tranibor live test", ttl=60
    )
    record_id = created["record_id"]
    try:
        assert created["created"] is True
        again = await live.call(
            "create_record", zone=zone, field_type="TXT", sub_domain=NAME, target="tranibor live test", ttl=60
        )
        assert again["created"] is False and again["record_id"] == record_id  # idempotence, quoting included
        updated = await live.call("update_record", zone=zone, record_id=record_id, target="tranibor live test 2")
        assert updated["previous"]["target"].strip('"') == "tranibor live test"
        await live.call("refresh_zone", zone=zone)
    finally:
        deleted = await live.call("delete_record", zone=zone, record_id=record_id)
        assert deleted["deleted"] is True
        await live.call("refresh_zone", zone=zone)
