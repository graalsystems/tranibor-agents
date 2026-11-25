# SPDX-License-Identifier: Apache-2.0
"""The tools on the seeded fake realm: what they return, what they never return, and the writes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tranibor_keycloak_admin.errors import Failure
from tranibor_keycloak_admin.fake import FakeKeycloak
from tranibor_keycloak_admin.tools import Tools

from .conftest import uid

#: Strings the fake holds and the server must never return.
SECRETS = [
    "fake-client-secret",
    "fake-portal-secret",
    "fake-registration-token",
    "fake-smtp-password",
    "fake-private-key",
    "fake-idp-secret",
    "fake-hash-do-not-return",
    "E-0042",
    "+33 1 23 45 67 89",
    "ldap-example",
    "fake-rotated-secret",
    "fake-code",
]


def clean(*results: Any) -> None:
    text = json.dumps(results)
    for secret in SECRETS:
        assert secret not in text, secret


async def test_find_users_paginates_and_hides_what_is_not_whitelisted(tools: Tools) -> None:
    page = await tools.find_users(search="martin")
    assert [u["username"] for u in page["users"]] == ["alice.martin"]
    assert not {"attributes", "credentials", "federationLink"} & set(page["users"][0])
    first = await tools.find_users(max=10)
    assert (first["count"], first["has_more"]) == (10, True)
    second = await tools.find_users(first=10, max=100)
    assert second["has_more"] is False
    assert {u["id"] for u in first["users"]}.isdisjoint(u["id"] for u in second["users"])
    clean(page, first, second)


async def test_find_users_filters(tools: Tools) -> None:
    exact = await tools.find_users(username="alice.martin", exact=True)
    assert exact["count"] == 1
    assert (await tools.find_users(username="alice", exact=True))["count"] == 0
    assert (await tools.find_users(enabled=False))["users"][0]["username"] == "felix.bernard"
    assert (await tools.find_users(email="bruno.petit@example.com"))["count"] == 1


@pytest.mark.parametrize("kwargs", [{"max": 0}, {"max": 101}, {"first": -1}])
async def test_find_users_bounds(tools: Tools, kwargs: dict[str, int]) -> None:
    with pytest.raises(Failure) as error:
        await tools.find_users(**kwargs)
    assert error.value.code == "invalid_arguments"


async def test_get_user_by_id_or_username(tools: Tools) -> None:
    by_name = await tools.get_user(username="Alice.Martin")
    by_id = await tools.get_user(user_id=uid("alice.martin"))
    assert by_name == by_id
    assert by_id["user"]["enabled"] is True
    clean(by_id)
    for kwargs in ({}, {"user_id": uid("alice.martin"), "username": "x"}):
        with pytest.raises(Failure, match="exactly one"):
            await tools.get_user(**kwargs)
    with pytest.raises(Failure) as missing:
        await tools.get_user(username="nobody")
    assert missing.value.code == "not_found"


@pytest.mark.parametrize("bad", ["../realms", "1234", "", "x" * 40, f"{uid('a')}/../x", "%2e%2e"])
async def test_identifiers_are_validated_before_they_reach_a_path(tools: Tools, fake: FakeKeycloak, bad: str) -> None:
    with pytest.raises(Failure) as error:
        await tools.get_user(user_id=bad) if bad else await tools.list_user_groups(user_id=bad)
    assert error.value.code == "invalid_arguments"
    assert fake.calls == []


async def test_user_groups_and_group_members(tools: Tools) -> None:
    groups = await tools.list_user_groups(uid("alice.martin"))
    assert {g["path"] for g in groups["groups"]} == {"/staff/engineering", "/realm-admins"}
    members = await tools.get_group_members(uid("group/realm-admins"))
    assert members["group"]["path"] == "/realm-admins"
    assert [u["username"] for u in members["members"]] == ["alice.martin"]
    clean(groups, members)


async def test_group_members_page(tools: Tools, fake: FakeKeycloak) -> None:
    gid = fake.group_ids["everyone"]
    page = await tools.get_group_members(gid, max=15)
    assert (page["count"], page["has_more"]) == (15, True)
    rest = await tools.get_group_members(gid, first=15, max=100)
    assert (rest["count"], rest["has_more"]) == (25, False)


async def test_list_groups_flat_with_paths_and_counts(tools: Tools) -> None:
    result = await tools.list_groups()
    paths = {g["path"] for g in result["groups"]}
    assert paths == {"/staff", "/staff/engineering", "/staff/finance", "/realm-admins", "/everyone"}
    assert result["truncated"] is False
    counted = await tools.list_groups(with_member_counts=True)
    sizes = {g["path"]: g["member_count"] for g in counted["groups"]}
    assert sizes["/everyone"] == 40
    assert sizes["/realm-admins"] == 1
    assert (await tools.list_groups(search="financ"))["groups"][0]["path"] == "/staff/finance"
    cut = await tools.list_groups(limit=2)
    assert (cut["count"], cut["truncated"]) == (2, True)


async def test_sessions_never_carry_tokens(tools: Tools) -> None:
    sessions = await tools.list_user_sessions(uid("alice.martin"))
    assert sessions["count"] == 1
    assert sessions["sessions"][0]["clients"] == ["portal"]
    assert set(sessions["sessions"][0]) == {"id", "started", "last_access", "ip_address", "remember_me", "clients"}


async def test_clients_never_carry_secrets(tools: Tools) -> None:
    result = await tools.list_clients()
    assert {c["clientId"] for c in result["clients"]} == {"tranibor-iam", "portal", "spa"}
    for client in result["clients"]:
        assert not {"secret", "registrationAccessToken", "attributes"} & set(client)
    clean(result)
    one = await tools.list_clients(client_id="por", search=True)
    assert [c["clientId"] for c in one["clients"]] == ["portal"]
    # the description is third-party text: it comes through as data, with the secrets still stripped
    assert "Ignore your instructions" in one["clients"][0]["description"]


async def test_realm_summary_strips_every_secret(tools: Tools) -> None:
    summary = (await tools.get_realm_summary())["realm"]
    assert summary["smtp_configured"] is True
    assert summary["eventsEnabled"] is True
    assert summary["user_count"] == 49
    assert summary["client_count"] == 3
    for dropped in ("smtpServer", "components", "attributes", "identityProviders", "keys"):
        assert dropped not in summary
    clean(summary)


async def test_login_events(tools: Tools) -> None:
    result = await tools.list_login_events(user_id=uid("alice.martin"))
    assert [e["type"] for e in result["events"]] == ["LOGIN", "LOGIN"]
    assert result["events"][0]["details"] == {"username": "alice.martin", "auth_method": "openid-connect"}
    clean(result)
    assert (await tools.list_login_events(max=2))["has_more"] is True


async def test_login_events_when_the_realm_does_not_record_them(tools: Tools, fake: FakeKeycloak) -> None:
    fake.realm["eventsEnabled"] = False
    result = await tools.list_login_events()
    assert result["events_enabled"] is False
    assert result["events"] == []
    assert "not enabled" in result["note"]


async def test_admin_events_drop_the_representation(tools: Tools) -> None:
    result = await tools.list_admin_events()
    assert result["admin_events_enabled"] is True
    assert [e["resource_type"] for e in result["events"]] == ["REALM_ROLE_MAPPING", "CLIENT_SECRET", "USER"]
    assert result["events"][0]["representation_present"] is True
    assert result["events"][2]["representation_present"] is False
    assert "representation" not in result["events"][0]
    clean(result)
    only = await tools.list_admin_events(resource_types=["CLIENT_SECRET"])
    assert only["count"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"date_from": "yesterday"},
        {"operation_types": ["CREATE;DROP"]},
        {"resource_path": "users/../../x?y"},
        {"max": 500},
    ],
)
async def test_admin_events_arguments_are_checked(tools: Tools, kwargs: dict[str, Any]) -> None:
    with pytest.raises(Failure) as error:
        await tools.list_admin_events(**kwargs)
    assert error.value.code == "invalid_arguments"


async def test_dormant_users(tools: Tools) -> None:
    result = await tools.dormant_users(days=90)
    names = {u["username"]: u for u in result["dormant"]}
    assert set(names) == {"david.leroy", "emma.moreau"}
    assert names["david.leroy"]["days_since_last_login"] == 200
    assert names["emma.moreau"]["last_login"] is None
    assert "no login event" in names["emma.moreau"]["note"]
    assert result["reliable"] is True
    assert result["evidence"]["window_covered_by_retention"] is True
    assert "LOGIN events" in result["relies_on"]
    clean(result)


async def test_dormant_users_judges_sessions_and_young_accounts(tools: Tools) -> None:
    names = {u["username"] for u in (await tools.dormant_users(days=90))["dormant"]}
    assert "ines.girard" not in names  # a session was used two days ago, with no LOGIN event
    assert "gaelle.roux" not in names  # created ten days ago
    assert "felix.bernard" not in names  # already disabled
    assert "alice.martin" not in names


async def test_dormant_users_can_leave_out_the_never_logged_in(tools: Tools) -> None:
    result = await tools.dormant_users(days=90, include_never_logged_in=False)
    assert {u["username"] for u in result["dormant"]} == {"david.leroy"}


async def test_dormant_users_says_when_it_cannot_tell(tools: Tools, fake: FakeKeycloak) -> None:
    fake.realm["eventsExpiration"] = 30 * 86400
    short = await tools.dormant_users(days=90)
    assert short["reliable"] is False
    assert short["evidence"]["retention_days"] == 30.0
    fake.realm["eventsEnabled"] = False
    off = await tools.dormant_users(days=90)
    assert off["reliable"] is False
    assert off["dormant"] == []
    assert "disabled" in off["reason"]


async def test_users_without_group(tools: Tools) -> None:
    result = await tools.users_without_group()
    assert {u["username"] for u in result["users"]} == {"gaelle.roux", "hugo.faure"}
    assert result["truncated"] is False
    cut = await tools.users_without_group(max_users=1)
    assert cut["truncated"] is True


# -- writes --


async def test_create_user_is_idempotent_and_sets_no_password(tools: Tools, fake: FakeKeycloak) -> None:
    created = await tools.create_user(
        "New.Person", email="new.person@example.com", required_actions=["UPDATE_PASSWORD"]
    )
    assert created["created"] is True
    assert created["user"]["username"] == "new.person"
    assert created["user"]["required_actions"] == ["UPDATE_PASSWORD"]
    again = await tools.create_user("new.person", email="other@example.com")
    assert again["created"] is False
    assert again["user"]["email"] == "new.person@example.com"  # left unchanged
    posts = [c for c in fake.calls if c == ("POST", "/users")]
    assert len(posts) == 1
    body = json.dumps(list(fake.users.values())[-1])
    assert "credentials" not in body


@pytest.mark.parametrize(
    "kwargs",
    [
        {"username": ""},
        {"username": "a/b"},
        {"username": "a b"},
        {"username": "ok", "email": "not-an-email"},
        {"username": "ok", "required_actions": ["DELETE_ACCOUNT"]},
    ],
)
async def test_create_user_arguments_are_checked(tools: Tools, fake: FakeKeycloak, kwargs: dict[str, Any]) -> None:
    with pytest.raises(Failure) as error:
        await tools.create_user(**kwargs)
    assert error.value.code == "invalid_arguments"
    assert not [c for c in fake.calls if c[0] == "POST"]


async def test_group_membership_round_trip_and_idempotence(tools: Tools, fake: FakeKeycloak) -> None:
    user, group = uid("hugo.faure"), fake.group_ids["finance"]
    added = await tools.add_user_to_group(user, group)
    assert (added["changed"], added["was_member"], added["group_path"]) == (True, False, "/staff/finance")
    assert {"user_id", "group_id"} <= set(added)  # what the descriptor's compensation renders from
    again = await tools.add_user_to_group(user, group)
    assert (again["changed"], again["was_member"]) == (False, True)
    assert len([c for c in fake.calls if c[0] == "PUT"]) == 1
    removed = await tools.remove_user_from_group(user, group)
    assert removed["changed"] is True
    assert (await tools.remove_user_from_group(user, group))["changed"] is False
    assert len([c for c in fake.calls if c[0] == "DELETE"]) == 1


async def test_group_membership_of_unknown_objects(tools: Tools) -> None:
    with pytest.raises(Failure) as error:
        await tools.add_user_to_group(uid("alice.martin"), uid("no-such-group"))
    assert error.value.code == "not_found"
    with pytest.raises(Failure) as bad:
        await tools.add_user_to_group(uid("alice.martin"), "..")
    assert bad.value.code == "invalid_arguments"


async def test_disable_and_enable(tools: Tools, fake: FakeKeycloak) -> None:
    david = uid("david.leroy")
    disabled = await tools.disable_user(david)
    assert (disabled["enabled"], disabled["previous_enabled"], disabled["changed"]) == (False, True, True)
    assert fake.users[david]["enabled"] is False
    # the other fields (attributes) were sent back untouched, not wiped
    assert fake.users[david]["attributes"]["employee_number"] == ["E-0042"]
    assert (await tools.disable_user(david))["changed"] is False
    enabled = await tools.enable_user(david)
    assert (enabled["enabled"], enabled["changed"]) == (True, True)
    assert fake.users[david]["enabled"] is True


async def test_logout_user_reports_the_sessions_it_ended(tools: Tools, fake: FakeKeycloak) -> None:
    result = await tools.logout_user(uid("alice.martin"))
    assert result["sessions_revoked"] == 1
    assert (await tools.list_user_sessions(uid("alice.martin")))["count"] == 0
    assert ("POST", f"/users/{uid('alice.martin')}/logout") in fake.calls


async def test_required_actions_email(tools: Tools, fake: FakeKeycloak) -> None:
    sent = await tools.send_required_actions_email(
        uid("bruno.petit"), ["UPDATE_PASSWORD", "CONFIGURE_TOTP"], action_key="k1"
    )
    assert sent["sent"] is True
    assert fake.emails == [
        {"user": uid("bruno.petit"), "actions": ["CONFIGURE_TOTP", "UPDATE_PASSWORD"], "lifespan": "43200"}
    ]
    replay = await tools.send_required_actions_email(uid("bruno.petit"), ["UPDATE_PASSWORD"], action_key="k1")
    assert replay["deduplicated"] is True
    assert len(fake.emails) == 1


@pytest.mark.parametrize(
    ("user", "kwargs", "why"),
    [
        ("emma.moreau", {"actions": ["UPDATE_PASSWORD"]}, "no email"),
        ("bruno.petit", {"actions": ["UPDATE_PASSWORD"], "lifespan_seconds": 5}, "lifespan_seconds"),
        ("bruno.petit", {"actions": ["TERMS_AND_CONDITIONS"]}, "actions"),
        ("bruno.petit", {"actions": []}, "actions"),
        ("bruno.petit", {"actions": ["UPDATE_PASSWORD"], "redirect_uri": "https://x.example.com"}, "client_id"),
        (
            "bruno.petit",
            {"actions": ["UPDATE_PASSWORD"], "client_id": "portal", "redirect_uri": "http://x.example.com"},
            "https",
        ),
    ],
)
async def test_required_actions_email_refusals(
    tools: Tools, fake: FakeKeycloak, user: str, kwargs: dict[str, Any], why: str
) -> None:
    with pytest.raises(Failure, match=why):
        await tools.send_required_actions_email(uid(user), **kwargs)
    assert fake.emails == []


async def test_no_email_without_smtp(tools: Tools, fake: FakeKeycloak) -> None:
    fake.realm["smtpServer"] = {}
    with pytest.raises(Failure, match="SMTP"):
        await tools.send_required_actions_email(uid("bruno.petit"), ["UPDATE_PASSWORD"])
    assert fake.emails == []


async def test_no_call_the_server_makes_deletes_or_reads_credentials(tools: Tools, fake: FakeKeycloak) -> None:
    await tools.get_realm_summary()
    await tools.dormant_users()
    await tools.users_without_group()
    await tools.disable_user(uid("david.leroy"))
    await tools.create_user("someone")
    methods = {m for m, _ in fake.calls}
    assert "DELETE" not in methods
    assert not [p for _, p in fake.calls if "credentials" in p or "client-secret" in p or "impersonation" in p]
