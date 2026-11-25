# SPDX-License-Identifier: Apache-2.0
"""Read tools against a real realm; writes only on a user this suite creates (and leaves disabled)."""

from __future__ import annotations

from tranibor_keycloak_admin.tools import Tools


async def test_realm_summary_has_no_secret(live: Tools) -> None:
    realm = (await live.get_realm_summary())["realm"]
    assert realm["realm"]
    assert "smtpServer" not in realm


async def test_users_groups_clients(live: Tools) -> None:
    users = await live.find_users(max=5)
    assert "users" in users
    assert "groups" in await live.list_groups(limit=10)
    clients = await live.list_clients()
    assert all("secret" not in c for c in clients["clients"])


async def test_events_and_dormant_report_their_own_reliability(live: Tools) -> None:
    assert "events" in await live.list_login_events(max=5)
    assert "events" in await live.list_admin_events(max=5)
    dormant = await live.dormant_users(days=365)
    assert isinstance(dormant["reliable"], bool)
    assert "evidence" in dormant


async def test_write_round_trip_on_a_user_created_here(live: Tools, write_enabled: None, run_tag: str) -> None:
    created = await live.create_user(run_tag, required_actions=["UPDATE_PASSWORD"])
    assert created["created"] is True
    user_id = created["user"]["id"]
    again = await live.create_user(run_tag)
    assert again["created"] is False
    assert (await live.disable_user(user_id))["enabled"] is False
    assert (await live.enable_user(user_id))["enabled"] is True
    assert (await live.disable_user(user_id))["enabled"] is False  # left disabled; no tool deletes it
