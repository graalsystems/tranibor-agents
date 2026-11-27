# SPDX-License-Identifier: Apache-2.0
"""What is specific to Keycloak, on the seeded fake: what is read, what is never returned, the allow-list at
the tool level, and the plans. The profile's behaviour (shapes, idempotence, compensation) is checked by the
conformance suite."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tranibor_identity_keycloak.errors import Failure
from tranibor_identity_keycloak.fake import PRIVATE_MARKER, FakeKeycloak
from tranibor_identity_keycloak.tools import Tools

from .conftest import uid

D = "demo"

#: Strings the fake holds and the server must never return.
SECRETS = [
    "fake-client-secret",
    "fake-portal-secret",
    "fake-management-secret",
    "fake-registration-token",
    "fake-smtp-password",
    "fake-private-key",
    "fake-idp-secret",
    "fake-hash",
    "fake-otp-seed",
    "credentialData",
    "secretData",
    "E-0042",
    "+33 1 23 45 67 89",
    "ldap-example",
    "fake-rotated-secret",
    "fake-code",
    PRIVATE_MARKER,
]


def clean(*results: Any) -> None:
    text = json.dumps(results)
    for secret in SECRETS:
        assert secret not in text, secret


async def test_the_provider_lists_the_allow_list_and_nothing_else(tools: Tools) -> None:
    assert await tools.get_provider() == {"instance": "identity-keycloak", "provider": "keycloak", "directories": [D]}


@pytest.mark.parametrize("directory", ["private", "Demo", "demo ", "demo/../private", "", "*", "master"])
async def test_a_directory_outside_the_allow_list_is_refused_before_any_call(
    tools: Tools, fake: FakeKeycloak, directory: str
) -> None:
    with pytest.raises(Failure, match="allow-list") as error:
        await tools.find_users(directory)
    assert error.value.code == "invalid_arguments"
    assert fake.upstream_calls == 0
    assert "demo" in str(error.value) and PRIVATE_MARKER not in str(error.value)


async def test_the_private_realm_is_never_asked_for(tools: Tools, fake: FakeKeycloak) -> None:
    await tools.dormant_users(D)
    await tools.users_without_mfa(D)
    await tools.list_privileged_users(D)
    await tools.list_groups(D)
    assert {realm for realm, _, _ in fake.calls} == {D}


async def test_find_users_hides_what_is_not_whitelisted_and_paginates(tools: Tools) -> None:
    page = await tools.find_users(D, search="martin")
    assert [u["username"] for u in page["users"]] == ["alice.martin"]
    assert not {"attributes", "credentials", "federationLink"} & set(page["users"][0])
    first = await tools.find_users(D, limit=10)
    assert (first["count"], first["has_more"]) == (10, True)
    second = await tools.find_users(D, limit=100, offset=10)
    assert second["has_more"] is False
    assert {u["id"] for u in first["users"]}.isdisjoint(u["id"] for u in second["users"])
    assert (await tools.find_users(D, enabled=False))["users"][0]["username"] == "felix.bernard"
    assert (await tools.find_users(D, username="alice", exact=True))["count"] == 0
    clean(page, first, second)


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_find_users_bounds(tools: Tools, kwargs: dict[str, int]) -> None:
    with pytest.raises(Failure) as error:
        await tools.find_users(D, **kwargs)
    assert error.value.code == "invalid_arguments"


@pytest.mark.parametrize("bad", ["../realms", "1234", "x" * 40, f"{uid('a')}/../x", "%2e%2e"])
async def test_identifiers_are_validated_before_they_reach_a_path(tools: Tools, fake: FakeKeycloak, bad: str) -> None:
    with pytest.raises(Failure) as error:
        await tools.get_user(D, user_id=bad)
    assert error.value.code == "invalid_arguments"
    assert fake.upstream_calls == 0


async def test_exactly_one_of_user_id_and_username(tools: Tools) -> None:
    for kwargs in ({}, {"user_id": uid("alice.martin"), "username": "alice.martin"}):
        with pytest.raises(Failure, match="exactly one"):
            await tools.get_user(D, **kwargs)
    assert (await tools.get_user(D, username="Alice.Martin")) == (await tools.get_user(D, user_id=uid("alice.martin")))


async def test_mfa_methods_are_types_only(tools: Tools) -> None:
    assert (await tools.get_user(D, username="alice.martin"))["mfa_methods"] == ["otp"]
    assert (await tools.get_user(D, username="bruno.petit"))["mfa_methods"] == ["webauthn"]
    assert (await tools.get_user(D, username="chloe.durand"))["mfa_methods"] == []
    clean(await tools.get_user(D, username="alice.martin"), await tools.users_without_mfa(D))


async def test_users_without_mfa(tools: Tools) -> None:
    result = await tools.users_without_mfa(D)
    names = {u["username"] for u in result["users"]}
    assert {"chloe.durand", "david.leroy", "hugo.faure"} <= names
    assert not {"alice.martin", "bruno.petit", "felix.bernard"} & names  # second factor, and a disabled account
    assert result["truncated"] is False
    assert (await tools.users_without_mfa(D, max_users=1))["truncated"] is True


async def test_privileged_users_directly_and_through_a_group_and_what_it_does_not_cover(tools: Tools) -> None:
    result = await tools.list_privileged_users(D)
    grants = {u["username"]: {(g["privilege"], g["via"]) for g in u["grants"]} for u in result["users"]}
    assert grants == {
        "alice.martin": {("manage-users", "direct"), ("realm-admin", "group:/realm-admins")},
        "hugo.faure": {("realm-admin", "direct")},
    }
    # `view-users`, which the group also carries, is not an administrative power.
    assert "view-users" not in json.dumps(result["users"])
    assert "Not read" in result["scope"] and "composite" in result["scope"]
    clean(result)


async def test_privileged_users_follows_subgroups_by_path(tools: Tools, fake: FakeKeycloak) -> None:
    realm = fake.demo
    parent = realm.group_ids["staff"]
    realm.group_roles[parent] = ["manage-clients"]
    result = await tools.list_privileged_users(D)
    grants = {u["username"]: {g["privilege"] for g in u["grants"]} for u in result["users"]}
    assert "manage-clients" in grants["bruno.petit"]  # a member of /staff/engineering
    assert grants["chloe.durand"] == {"manage-clients"}  # a member of /staff/finance


async def test_the_directory_says_what_the_dormancy_review_relies_on(tools: Tools, fake: FakeKeycloak) -> None:
    state = await tools.get_directory(D)
    assert (state["sign_in_events_enabled"], state["events_retention_days"], state["mail_configured"]) == (
        True,
        180.0,
        True,
    )
    clean(state)
    fake.demo.realm["smtpServer"] = {}
    assert (await tools.get_directory(D))["mail_configured"] is False


async def test_dormant_users(tools: Tools) -> None:
    result = await tools.dormant_users(D, days=90)
    names = {u["username"]: u for u in result["dormant"]}
    assert set(names) == {"david.leroy", "emma.moreau"}
    assert names["david.leroy"]["days_since_last_sign_in"] == 200
    assert "last_sign_in_at" not in names["emma.moreau"] and "no sign-in event" in names["emma.moreau"]["note"]
    assert result["reliable"] is True and result["evidence"]["window_covered_by_retention"] is True
    clean(result)


async def test_dormant_users_judges_sessions_and_young_accounts(tools: Tools) -> None:
    names = {u["username"] for u in (await tools.dormant_users(D, days=90))["dormant"]}
    assert not {"ines.girard", "gaelle.roux", "felix.bernard", "alice.martin"} & names
    assert {u["username"] for u in (await tools.dormant_users(D, include_never_signed_in=False))["dormant"]} == {
        "david.leroy"
    }


async def test_dormant_users_says_when_it_cannot_tell(tools: Tools, fake: FakeKeycloak) -> None:
    fake.demo.realm["eventsExpiration"] = 30 * 86400
    short = await tools.dormant_users(D, days=90)
    assert short["reliable"] is False and short["evidence"]["retention_days"] == 30.0
    fake.demo.realm["eventsEnabled"] = False
    off = await tools.dormant_users(D, days=90)
    assert off["reliable"] is False and off["dormant"] == [] and "disabled" in off["reason"]


async def test_users_without_group(tools: Tools) -> None:
    result = await tools.users_without_group(D)
    assert {u["username"] for u in result["users"]} == {"gaelle.roux", "hugo.faure"}


async def test_compare_group_members_reports_drift_both_ways(tools: Tools, fake: FakeKeycloak) -> None:
    gid = fake.demo.group_ids["finance"]
    result = await tools.compare_group_members(D, gid, ["chloe.durand", "ghost.user"])
    assert result["missing"] == ["ghost.user"]
    assert {u["username"] for u in result["unexpected"]} == {"david.leroy", "felix.bernard"}
    assert result["complete"] is True


async def test_events_never_carry_secrets_and_say_when_the_realm_does_not_keep_them(
    tools: Tools, fake: FakeKeycloak
) -> None:
    admin = await tools.list_admin_events(D)
    assert [e["resource_type"] for e in admin["events"]] == ["REALM_ROLE_MAPPING", "CLIENT_SECRET", "USER"]
    assert admin["events"][0]["representation_present"] is True and "representation" not in admin["events"][0]
    sign_in = await tools.list_sign_in_events(D, user_id=uid("alice.martin"))
    assert [e["type"] for e in sign_in["events"]] == ["LOGIN", "LOGIN"]
    clean(admin, sign_in)
    fake.demo.realm["eventsEnabled"] = False
    off = await tools.list_sign_in_events(D)
    assert off["events_enabled"] is False and off["events"] == [] and "not enabled" in off["note"]


@pytest.mark.parametrize(
    "kwargs",
    [{"date_from": "yesterday"}, {"operations": ["CREATE;DROP"]}, {"resource_path": "users/../../x?y"}, {"limit": 500}],
)
async def test_admin_events_arguments_are_checked(tools: Tools, kwargs: dict[str, Any]) -> None:
    with pytest.raises(Failure) as error:
        await tools.list_admin_events(D, **kwargs)
    assert error.value.code == "invalid_arguments"


async def test_the_keycloak_only_reads_strip_every_secret(tools: Tools) -> None:
    clients = await tools.list_clients(D)
    assert {c["clientId"] for c in clients["clients"]} == {"tranibor-iam", "portal", "spa", "realm-management"}
    for client in clients["clients"]:
        assert not {"secret", "registrationAccessToken", "attributes"} & set(client)
    summary = (await tools.get_realm_summary(D))["realm"]
    assert summary["smtp_configured"] is True and summary["client_count"] == 4
    for dropped in ("smtpServer", "components", "attributes", "identityProviders", "keys"):
        assert dropped not in summary
    clean(clients, summary)


async def test_a_plan_for_an_arrival_orders_its_calls_and_writes_nothing(tools: Tools, fake: FakeKeycloak) -> None:
    finance = fake.demo.group_ids["finance"]
    plan = await tools.plan_joiner(D, "new.person", "new.person@example.com", "New", "Person", group_ids=[finance])
    assert plan["valid"] is True
    assert [c["tool"] for c in plan["proposed_calls"]] == ["create_user", "add_user_to_group", "send_setup_email"]
    assert all(c["arguments"]["directory"] == D for c in plan["proposed_calls"])
    assert plan["proposed_calls"][2]["arguments"]["actions"] == ["set_password"]
    assert not [call for call in fake.calls if call[1] != "GET"]


async def test_a_plan_for_an_arrival_without_mail_says_so(tools: Tools, fake: FakeKeycloak) -> None:
    fake.demo.realm["smtpServer"] = {}
    plan = await tools.plan_joiner(D, "new.person", "new.person@example.com", "New", "Person")
    assert [c["tool"] for c in plan["proposed_calls"]] == ["create_user"]
    assert any("mail" in w.lower() for w in plan["warnings"])


async def test_an_invalid_plan_has_errors_and_no_calls(tools: Tools) -> None:
    taken = await tools.plan_joiner(D, "alice.martin", "a@example.com", "A", "M")
    assert (taken["valid"], taken["proposed_calls"]) == (False, []) and "already exists" in taken["errors"][0]
    no_group = await tools.plan_joiner(D, "new.person", "n@example.com", "N", "P", group_ids=[uid("no-such-group")])
    assert (no_group["valid"], no_group["proposed_calls"]) == (False, [])


async def test_a_plan_for_a_departure_disables_first_then_ends_sessions_then_removes_groups(tools: Tools) -> None:
    plan = await tools.plan_leaver(D, username="alice.martin")
    assert [c["tool"] for c in plan["proposed_calls"]] == [
        "disable_user",
        "logout_user",
        "remove_user_from_group",
        "remove_user_from_group",
    ]
    assert plan["state"]["open_sessions"] == 1
    gone = await tools.plan_leaver(D, username="felix.bernard")
    assert [c["tool"] for c in gone["proposed_calls"]] == ["remove_user_from_group"]
    assert any("already disabled" in w for w in gone["warnings"])
    assert (await tools.plan_leaver(D, username="nobody"))["valid"] is False


async def test_create_user_sets_no_password_and_a_setup_email_needs_smtp(tools: Tools, fake: FakeKeycloak) -> None:
    made = await tools.create_user(D, "new.person", email="new.person@example.com", setup_actions=["set_password"])
    assert made["created"] is True and made["user"]["setup_actions"] == ["set_password"]
    assert "credentials" not in json.dumps(list(fake.demo.users.values())[-1])
    fake.demo.realm["smtpServer"] = {}
    with pytest.raises(Failure, match="mail"):
        await tools.send_setup_email(D, ["set_password"], username="bruno.petit")
    assert fake.demo.emails == []


async def test_no_call_the_server_makes_deletes_or_reads_a_secret(tools: Tools, fake: FakeKeycloak) -> None:
    await tools.get_realm_summary(D)
    await tools.list_privileged_users(D)
    await tools.users_without_mfa(D)
    await tools.plan_leaver(D, username="david.leroy")
    await tools.disable_user(D, user_id=uid("david.leroy"))
    await tools.create_user(D, "someone")
    assert "DELETE" not in {method for _, method, _ in fake.calls}
    assert not [
        p for _, _, p in fake.calls if "client-secret" in p or p.endswith("/impersonation") or p.endswith("/keys")
    ]
