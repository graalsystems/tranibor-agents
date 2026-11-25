# SPDX-License-Identifier: Apache-2.0
"""The tools, over MCP, on the fake site: what each reads, what each writes, and what each refuses."""

from __future__ import annotations

import pytest

from tranibor_lan_unifi.fake import (
    AP_LOBBY,
    AP_ROOM_12,
    AP_ROOM_14,
    GUEST_PHONE,
    MISSING,
    NVR,
    SITE,
    STAFF_LAPTOP,
    SWITCH,
)

from .conftest import Mcp


def test_info_and_sites(mcp: Mcp) -> None:
    assert mcp.call("get_info")["application_version"] == "10.1.68"
    sites = mcp.call("list_sites")
    assert sites["sites"] == [{"id": SITE, "name": "Example Office", "internal_reference": "default"}]
    assert sites["source"] == "unifi-network-integration-api"
    assert sites["as_of"] == "2026-11-25T09:17:00Z"


def test_devices_with_their_state_and_firmware(mcp: Mcp) -> None:
    devices = mcp.call("list_devices", site_id=SITE)
    assert devices["total"] == 5
    assert not devices["truncated"]
    by_name = {d["name"]: d for d in devices["devices"]}
    assert by_name["uap-room-14"]["state"] == "OFFLINE"
    assert by_name["uap-room-12"]["firmware_updatable"] is True
    assert by_name["usw-core"]["firmware_version"] == "7.0.0"
    offline = mcp.call("list_devices", site_id=SITE, state="OFFLINE")
    assert [d["id"] for d in offline["devices"]] == [AP_ROOM_14]
    assert mcp.call("list_devices", site_id=SITE, limit=2)["truncated"] is True


def test_a_device_in_detail_has_its_uplink_ports_and_radios(mcp: Mcp) -> None:
    switch = mcp.call("get_device", site_id=SITE, device_id=SWITCH)
    port5 = next(p for p in switch["ports"] if p["idx"] == 5)
    assert port5["poe"] == {"standard": "802.3at", "type": 2, "enabled": True, "state": "UP"}
    ap = mcp.call("get_device", site_id=SITE, device_id=AP_ROOM_12)
    assert ap["uplink_device_id"] == SWITCH
    assert {(r["frequency_ghz"], r["channel"], r["channel_width_mhz"]) for r in ap["radios"]} == {
        (2.4, 6, 40),
        (5.0, 36, 80),
    }


def test_statistics_show_the_retry_rate_of_each_radio(mcp: Mcp) -> None:
    stats = mcp.call("get_device_statistics", site_id=SITE, device_id=AP_ROOM_12)
    assert {r["frequency_ghz"]: r["tx_retries_pct"] for r in stats["radios"]} == {2.4: 41.0, 5.0: 8.0}
    assert stats["uptime_sec"] == 864_000
    assert mcp.call("get_device_statistics", site_id=SITE, device_id=AP_ROOM_14)["last_heartbeat_at"]


def test_clients_and_a_guest(mcp: Mcp) -> None:
    clients = mcp.call("list_clients", site_id=SITE)
    assert {c["name"] for c in clients["clients"]} == {"guest-phone", "staff-laptop", "nvr-1"}
    wired = mcp.call("list_clients", site_id=SITE, type="WIRED")["clients"]
    assert [c["id"] for c in wired] == [NVR]
    phone = mcp.call("get_client", site_id=SITE, client_id=GUEST_PHONE)
    assert phone["client"]["uplink_device_id"] == AP_ROOM_12
    assert phone["client"]["guest_authorized"] is False
    assert phone["guest_authorization"] is None


def test_networks_and_wifi_never_carry_a_passphrase(mcp: Mcp) -> None:
    nets = mcp.call("list_networks", site_id=SITE)
    wlans = mcp.call("list_wlans", site_id=SITE)
    assert {(n["name"], n["vlan_id"]) for n in nets["networks"]} == {("Default", 1), ("Guests", 20)}
    assert {(w["name"], w["security_type"]) for w in wlans["wlans"]} == {
        ("example-staff", "WPA2_ENTERPRISE"),
        ("example-guest", "OPEN"),
    }
    keys = {k for w in wlans["wlans"] for k in w} | {k for n in nets["networks"] for k in n}
    assert not {k for k in keys if "pass" in k or "secret" in k or "psk" in k}


def test_firmware_status(mcp: Mcp) -> None:
    drift = mcp.call("firmware_status", site_id=SITE)
    assert {d["name"] for d in drift["updatable"]} == {"uap-room-12", "uap-lobby"}
    assert [d["name"] for d in drift["offline"]] == ["uap-room-14"]
    u6 = next(m for m in drift["by_model"] if m["model"] == "U6-Pro")
    assert (u6["devices"], u6["updatable"], u6["versions"]) == (2, 1, {"6.6.0": 1, "6.7.0": 1})


def test_the_cloud_tool_reads_the_site_manager(mcp: Mcp) -> None:
    cloud = mcp.call("cloud_firmware_status")
    assert cloud["source"] == "unifi-site-manager-api"
    assert [(d["name"], d["update_available"]) for d in cloud["needing_update"]] == [("uap-room-12", "6.7.0")]
    assert [d["name"] for d in cloud["offline"]] == ["uap-room-14"]
    assert mcp.call("cloud_firmware_status", host_id="no-such-host")["devices_examined"] == 0


def test_vouchers_are_listed_without_their_codes(mcp: Mcp) -> None:
    listed = mcp.call("list_vouchers", site_id=SITE)
    assert listed["total"] == 1
    assert listed["vouchers"][0]["code"] is None


# -- writes -------------------------------------------------------------------------------------------------


def test_restart_a_device(mcp: Mcp) -> None:
    done = mcp.call("restart_device", site_id=SITE, device_id=AP_LOBBY)
    assert (done["action"], done["target_name"], done["previous_state"]) == ("RESTART", "uap-lobby", "ONLINE")
    assert mcp.fake.restarts == [AP_LOBBY]
    assert (
        "POST",
        f"/proxy/network/integration/v1/sites/{SITE}/devices/{AP_LOBBY}/actions",
        {"action": "RESTART"},
    ) in mcp.fake.calls


def test_power_cycle_a_poe_port(mcp: Mcp) -> None:
    done = mcp.call("power_cycle_port", site_id=SITE, device_id=SWITCH, port_idx=5)
    assert (done["action"], done["target"], done["target_name"]) == ("POWER_CYCLE", f"{SWITCH}/ports/5", "usw-core")
    assert mcp.fake.power_cycles == [(SWITCH, 5)]


@pytest.mark.parametrize(
    ("device", "port", "why"),
    [(SWITCH, 99, "no port 99"), (AP_ROOM_12, 1, "no port 1"), (SWITCH, -1, "port_idx")],
)
def test_power_cycle_is_refused_without_a_poe_port_before_anything_is_sent(
    mcp: Mcp, device: str, port: int, why: str
) -> None:
    assert mcp.error("power_cycle_port", site_id=SITE, device_id=device, port_idx=port).startswith(
        "invalid_arguments: "
    )
    assert mcp.fake.power_cycles == []
    assert not [c for c in mcp.fake.calls if c[0] == "POST"]


def test_authorize_then_unauthorize_a_guest(mcp: Mcp) -> None:
    granted = mcp.call(
        "authorize_guest", site_id=SITE, client_id=GUEST_PHONE, time_limit_minutes=60, data_usage_limit_mbytes=500
    )
    assert granted["granted"]["expires_at"] == "2026-11-25T10:17:00Z"
    assert granted["granted"]["data_usage_limit_mbytes"] == 500
    assert mcp.call("get_client", site_id=SITE, client_id=GUEST_PHONE)["client"]["guest_authorized"] is True
    again = mcp.call("authorize_guest", site_id=SITE, client_id=GUEST_PHONE)
    assert again["revoked"] is not None  # a new authorisation replaces the active one
    gone = mcp.call("unauthorize_guest", site_id=SITE, client_id=GUEST_PHONE)
    assert gone["revoked"] is not None
    assert mcp.call("get_client", site_id=SITE, client_id=GUEST_PHONE)["client"]["guest_authorized"] is False


def test_the_compensation_of_a_guest_authorisation_revokes_it(mcp: Mcp) -> None:
    granted = mcp.call("authorize_guest", site_id=SITE, client_id=GUEST_PHONE)
    undone = mcp.call("revoke_guest_access", site_id=granted["site_id"], client_id=granted["client_id"])
    assert undone["action"] == "UNAUTHORIZE_GUEST_ACCESS"


def test_a_client_that_is_not_a_guest_is_refused_by_the_console(mcp: Mcp) -> None:
    assert mcp.error("authorize_guest", site_id=SITE, client_id=STAFF_LAPTOP).startswith("invalid_arguments: ")


@pytest.mark.parametrize(
    "field", ["time_limit_minutes", "data_usage_limit_mbytes", "rx_rate_limit_kbps", "tx_rate_limit_kbps"]
)
def test_guest_limits_out_of_range_are_refused_before_a_call(mcp: Mcp, field: str) -> None:
    assert mcp.error("authorize_guest", site_id=SITE, client_id=GUEST_PHONE, **{field: 0}).startswith(
        "invalid_arguments: "
    )
    assert not [c for c in mcp.fake.calls if c[0] == "POST"]


def test_create_vouchers_is_idempotent_through_the_action_key_and_undone_by_name(mcp: Mcp) -> None:
    first = mcp.call(
        "create_vouchers", site_id=SITE, name="Workshop", time_limit_minutes=240, count=3, action_key="act-0001"
    )
    assert (first["reused"], first["count"], first["voucher_name"]) == (False, 3, "Workshop [act-0001]")
    assert all(v["code"] for v in first["vouchers"])
    retry = mcp.call(
        "create_vouchers", site_id=SITE, name="Workshop", time_limit_minutes=240, count=3, action_key="act-0001"
    )
    assert (retry["reused"], retry["count"]) == (True, 3)
    assert mcp.call("list_vouchers", site_id=SITE)["total"] == 4  # the seed's, and three: not six
    undone = mcp.call("revoke_vouchers", site_id=first["site_id"], voucher_name=first["voucher_name"])
    assert undone["vouchers_deleted"] == 3
    assert mcp.call("list_vouchers", site_id=SITE)["total"] == 1


def test_a_voucher_name_with_a_quote_is_escaped_in_the_filter(mcp: Mcp) -> None:
    made = mcp.call("create_vouchers", site_id=SITE, name="Bob's day", time_limit_minutes=60, action_key="k1")
    assert mcp.call("create_vouchers", site_id=SITE, name="Bob's day", time_limit_minutes=60, action_key="k1")["reused"]
    assert mcp.call("revoke_vouchers", site_id=SITE, voucher_name=made["voucher_name"])["vouchers_deleted"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": " ", "time_limit_minutes": 5},
        {"name": "x", "time_limit_minutes": 0},
        {"name": "x", "time_limit_minutes": 5, "count": 101},
    ],
)
def test_voucher_requests_out_of_range_are_refused(mcp: Mcp, arguments: dict[str, object]) -> None:
    assert mcp.error("create_vouchers", site_id=SITE, **arguments).startswith("invalid_arguments: ")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_devices", {"site_id": "../../x"}),
        ("get_device", {"site_id": SITE, "device_id": "1/../2"}),
        ("get_client", {"site_id": SITE, "client_id": "not-a-uuid"}),
        ("restart_device", {"site_id": SITE, "device_id": "x"}),
        ("list_devices", {"site_id": SITE, "limit": 0}),
        ("list_devices", {"site_id": SITE, "limit": 501}),
    ],
)
def test_an_id_that_is_not_a_uuid_never_reaches_the_console(mcp: Mcp, tool: str, arguments: dict[str, object]) -> None:
    assert mcp.error(tool, **arguments).startswith("invalid_arguments: ")
    assert mcp.fake.calls == []


def test_unknown_objects_are_not_found(mcp: Mcp) -> None:
    assert mcp.error("get_device", site_id=SITE, device_id=MISSING).startswith("not_found: ")
    assert mcp.error("restart_device", site_id=SITE, device_id=MISSING).startswith("not_found: ")
    assert mcp.error("get_client", site_id=MISSING, client_id=NVR).startswith("not_found: ")


def test_a_port_index_the_workflow_renders_as_a_string_is_accepted(mcp: Mcp) -> None:
    """A template renders `{{ fields.port_idx }}` as text; the server's own arguments take it."""
    done = mcp.call("power_cycle_port", site_id=SITE, device_id=SWITCH, port_idx="5")
    assert done["target"] == f"{SWITCH}/ports/5"


def test_get_provider_names_the_instance_and_what_it_cannot_serve(mcp: Mcp) -> None:
    provider = mcp.call("get_provider")
    assert provider["provider"] == "unifi"
    assert provider["instance"] == "lan-unifi"
    assert [s["name"] for s in provider["sites"]] == ["Example Office"]
    assert provider["unsupported_tools"] == ["block_client", "unblock_client", "upgrade_firmware"]


def test_the_tools_the_official_api_lacks_answer_not_supported_and_send_nothing(mcp: Mcp) -> None:
    before = len(mcp.fake.calls)
    for tool, args in (
        ("block_client", {"site_id": SITE, "client_id": STAFF_LAPTOP}),
        ("unblock_client", {"site_id": SITE, "client_id": STAFF_LAPTOP}),
        ("upgrade_firmware", {"site_id": SITE, "device_id": AP_ROOM_12}),
    ):
        assert mcp.error(tool, **args).startswith("not_supported: "), tool
    assert len(mcp.fake.calls) == before
