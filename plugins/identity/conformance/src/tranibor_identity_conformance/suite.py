# SPDX-License-Identifier: Apache-2.0
"""The checks. Subclass `Conformance` in the implementation's tests and provide an `identity_target` fixture.

The suite creates accounts named `conf-<run>` in `target.directory`, puts them in an existing group, disables
them and removes them from their groups afterwards; it never touches another account. It drives the tools
exactly as the platform would: compensation arguments are RENDERED from the descriptor's templates with a
sandboxed Jinja (every argument a string, undefined names refused) and run.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator

from .profile import PROFILE_ID, load_profile, output_schema, tools
from .target import Target, ToolFailed


def _unique() -> str:
    return f"conf-{uuid.uuid4().hex[:8]}"


def _render(template: dict[str, str], result: dict[str, Any]) -> dict[str, str]:
    env = SandboxedEnvironment(undefined=StrictUndefined)
    return {key: env.from_string(value).render(result=result) for key, value in template.items()}


def _operations(target: Target) -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in target.descriptor["operations"]}


class Conformance:
    """Every test takes the `identity_target` fixture (a `Target`). Nothing here is collected until subclassed."""

    # ---- helpers --------------------------------------------------------------------------------

    @staticmethod
    async def call(target: Target, tool: str, **arguments: Any) -> dict[str, Any]:
        """Calls a tool and checks its answer against the profile's output schema."""
        result = await target.call(tool, arguments)
        errors = sorted(Draft202012Validator(output_schema(tool)).iter_errors(result), key=str)
        assert not errors, f"{tool}: the answer breaks the profile's output schema: {errors[0].message}\n{result}"
        return result

    @staticmethod
    async def refused(target: Target, tool: str, **arguments: Any) -> str:
        with pytest.raises(ToolFailed) as raised:
            await target.call(tool, arguments)
        return str(raised.value)

    @staticmethod
    async def a_group(target: Target) -> dict[str, Any]:
        groups = (await Conformance.call(target, "list_groups", directory=target.directory))["groups"]
        assert groups, "the suite needs a directory that holds at least one group"
        return dict(groups[0])

    @staticmethod
    async def a_user(target: Target) -> dict[str, Any]:
        users = (await Conformance.call(target, "find_users", directory=target.directory, limit=5))["users"]
        assert users, "the suite needs a directory that holds at least one user"
        return dict(users[0])

    @staticmethod
    @asynccontextmanager
    async def scratch(target: Target) -> AsyncIterator[dict[str, Any]]:
        """A fresh unique username: yields {username}; whatever was made is disabled and ungrouped afterwards."""
        username = _unique()
        try:
            yield {"username": username}
        finally:
            found = (
                await target.call("find_users", {"directory": target.directory, "username": username, "exact": True})
            )["users"]
            for user in found:
                groups = (
                    await target.call("list_user_groups", {"directory": target.directory, "user_id": user["id"]})
                )["groups"]
                for group in groups:
                    await target.call(
                        "remove_user_from_group",
                        {"directory": target.directory, "user_id": user["id"], "group_id": group["id"]},
                    )
                await target.call("disable_user", {"directory": target.directory, "user_id": user["id"]})

    @staticmethod
    async def make_user(target: Target, username: str, **extra: Any) -> dict[str, Any]:
        arguments = {
            "directory": target.directory,
            "username": username,
            "email": f"{username}@example.com",
            "first_name": "Conf",
            "last_name": "Test",
            **extra,
        }
        return await Conformance.call(target, "create_user", **arguments)

    # ---- the profile is served ------------------------------------------------------------------

    async def test_every_profile_tool_is_served(self, identity_target: Target) -> None:
        served = await identity_target.served()
        missing = sorted(set(tools()) - set(served))
        assert not missing, f"{PROFILE_ID}: tools not served: {missing}"

    async def test_the_descriptor_implements_the_profile_and_declares_every_served_tool(
        self, identity_target: Target
    ) -> None:
        assert PROFILE_ID in identity_target.descriptor.get("implements", [])
        declared = set(_operations(identity_target))
        served = set(await identity_target.served())
        assert served == declared, (
            f"served but not declared: {sorted(served - declared)}; "
            f"declared but not served: {sorted(declared - served)}"
        )

    async def test_governance_is_at_least_the_profile_floor(self, identity_target: Target) -> None:
        operations = _operations(identity_target)
        for name, spec in tools().items():
            declared = operations[name]
            assert declared["access"] == spec["access"], f"{name}: access"
            if spec.get("human_approval"):
                assert declared.get("human_approval") is True, f"{name}: human_approval may be raised, never lowered"
            assert set(spec.get("platform_arguments", [])) <= set(declared.get("platform_arguments", [])), name
        # The writes that have an undo declare it; the others (create, logout, mail) do not pretend to.
        for name in ("add_user_to_group", "remove_user_from_group", "disable_user", "enable_user"):
            assert "compensation" in operations[name], f"{name} must declare its compensation"
        for name in ("create_user", "logout_user", "send_setup_email"):
            assert "compensation" not in operations[name], f"{name} cannot be undone and must not say it can"
        assert not any("compensation" in op for op in operations.values() if op["access"] == "read")

    async def test_served_inputs_accept_what_the_profile_sends(self, identity_target: Target) -> None:
        served = await identity_target.served()
        for name, spec in tools().items():
            schema = served[name]
            properties = schema.get("properties", {})
            wanted = spec["input"].get("properties", {})
            if "$ref" in spec["input"]:
                wanted = load_profile()["$defs"][spec["input"]["$ref"].rsplit("/", 1)[1]].get("properties", {})
            assert set(wanted) <= set(properties), (
                f"{name}: arguments not accepted: {sorted(set(wanted) - set(properties))}"
            )
            for argument in spec.get("platform_arguments", []):
                assert argument in properties and argument not in schema.get("required", []), (name, argument)
            if "directory" in wanted:
                assert "directory" in schema.get("required", []), f"{name}: directory must be required"

    # ---- reads ----------------------------------------------------------------------------------

    async def test_get_provider_says_the_allow_list(self, identity_target: Target) -> None:
        provider = await self.call(identity_target, "get_provider")
        assert provider["instance"] and provider["provider"]
        assert identity_target.directory in provider["directories"]
        assert identity_target.foreign_directory not in provider["directories"]

    async def test_reads_answer_the_profile(self, identity_target: Target) -> None:
        directory = identity_target.directory
        state = await self.call(identity_target, "get_directory", directory=directory)
        assert state["directory"] == directory

        listing = await self.call(identity_target, "find_users", directory=directory, limit=3)
        assert listing["count"] == len(listing["users"]) > 0
        user = listing["users"][0]
        by_id = await self.call(identity_target, "get_user", directory=directory, user_id=user["id"])
        by_name = await self.call(identity_target, "get_user", directory=directory, username=user["username"])
        assert by_id["user"]["id"] == by_name["user"]["id"] == user["id"]
        exact = await self.call(
            identity_target, "find_users", directory=directory, username=user["username"], exact=True
        )
        assert [u["id"] for u in exact["users"]] == [user["id"]]

        groups = await self.call(identity_target, "list_user_groups", directory=directory, user_id=user["id"])
        assert groups["count"] == len(groups["groups"])
        everything = await self.call(identity_target, "list_groups", directory=directory, with_member_counts=True)
        assert everything["count"] == len(everything["groups"]) > 0
        group = everything["groups"][0]
        members = await self.call(identity_target, "get_group_members", directory=directory, group_id=group["id"])
        assert members["group"]["id"] == group["id"] and members["count"] == len(members["members"])

        await self.call(identity_target, "list_user_sessions", directory=directory, user_id=user["id"])
        await self.call(identity_target, "list_privileged_users", directory=directory)
        dormant = await self.call(identity_target, "dormant_users", directory=directory, days=30)
        assert dormant["count"] == len(dormant["dormant"])
        await self.call(identity_target, "users_without_mfa", directory=directory)
        await self.call(identity_target, "users_without_group", directory=directory)
        await self.call(identity_target, "list_sign_in_events", directory=directory, limit=5)
        await self.call(identity_target, "list_admin_events", directory=directory, limit=5)

    async def test_a_user_that_does_not_exist_is_refused_not_invented(self, identity_target: Target) -> None:
        await self.refused(identity_target, "get_user", directory=identity_target.directory, username=_unique())

    async def test_a_user_is_named_by_exactly_one_of_user_id_and_username(self, identity_target: Target) -> None:
        directory = identity_target.directory
        user = await self.a_user(identity_target)
        both = await self.refused(
            identity_target, "get_user", directory=directory, user_id=user["id"], username=user["username"]
        )
        assert "exactly one" in both
        assert "exactly one" in await self.refused(identity_target, "get_user", directory=directory)

    async def test_compare_group_members_reports_the_drift(self, identity_target: Target) -> None:
        directory = identity_target.directory
        everything = (await self.call(identity_target, "list_groups", directory=directory, with_member_counts=True))[
            "groups"
        ]
        group = next((g for g in everything if g.get("member_count")), everything[0])
        members = await self.call(identity_target, "get_group_members", directory=directory, group_id=group["id"])
        names = [u["username"] for u in members["members"]]
        ghost = _unique()
        report = await self.call(
            identity_target,
            "compare_group_members",
            directory=directory,
            group_id=group["id"],
            expected_usernames=[*names, ghost],
        )
        assert report["missing"] == [ghost] and report["unexpected"] == [] and report["matched"] == len(names)
        if names:
            report = await self.call(
                identity_target,
                "compare_group_members",
                directory=directory,
                group_id=group["id"],
                expected_usernames=[ghost],
            )
            assert report["missing"] == [ghost]
            assert {u["username"] for u in report["unexpected"]} == set(names)

    # ---- the allow-list -------------------------------------------------------------------------

    async def test_a_directory_outside_the_allow_list_is_refused_before_anything_is_sent(
        self, identity_target: Target
    ) -> None:
        samples: dict[str, Any] = {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "group_id": "00000000-0000-0000-0000-000000000002",
            "username": "x",
            "email": "x@example.com",
            "first_name": "X",
            "last_name": "X",
            "expected_usernames": ["x"],
            "actions": ["set_password"],
        }
        directory = identity_target.directory
        foreign = [
            identity_target.foreign_directory,
            f"{directory}.evil.test",
            f"x{directory}",
            f"{directory}x",
            "../etc",
        ]
        for tool, spec in tools().items():
            if tool == "get_provider":
                continue
            required = spec["input"].get("required", [])
            if "$ref" in spec["input"]:
                required = load_profile()["$defs"][spec["input"]["$ref"].rsplit("/", 1)[1]].get("required", [])
            for bad in foreign:
                before = identity_target.upstream_calls()
                arguments = {"directory": bad, **{k: samples[k] for k in required if k in samples}}
                if tool in {
                    "get_user",
                    "list_user_groups",
                    "list_user_sessions",
                    "plan_leaver",
                    "disable_user",
                    "enable_user",
                    "logout_user",
                    "add_user_to_group",
                    "remove_user_from_group",
                }:
                    arguments.setdefault("user_id", samples["user_id"])
                message = await self.refused(identity_target, tool, **arguments)
                assert "allow-list" in message, f"{tool} {bad!r}: the refusal must say 'allow-list': {message}"
                assert identity_target.upstream_calls() == before, f"{tool} {bad!r}: the provider was called"

    # ---- plans ----------------------------------------------------------------------------------

    async def test_plan_joiner_writes_nothing_and_its_calls_can_be_run(self, identity_target: Target) -> None:
        directory = identity_target.directory
        group = await self.a_group(identity_target)
        async with self.scratch(identity_target) as s:
            username = s["username"]
            plan = await self.call(
                identity_target,
                "plan_joiner",
                directory=directory,
                username=username,
                email=f"{username}@example.com",
                first_name="Conf",
                last_name="Joiner",
                group_ids=[group["id"]],
                setup_actions=["set_password"],
            )
            assert plan["valid"] is True and plan["errors"] == []
            tools_in_order = [c["tool"] for c in plan["proposed_calls"]]
            assert tools_in_order[0] == "create_user"
            assert tools_in_order.count("add_user_to_group") == 1
            assert all(c["arguments"]["directory"] == directory for c in plan["proposed_calls"])
            found = await self.call(identity_target, "find_users", directory=directory, username=username, exact=True)
            assert found["users"] == [], "a plan must write nothing"
            # What it proposes is what the profile's writes take: run it as the platform would.
            for step in plan["proposed_calls"]:
                if step["tool"] == "send_setup_email" and not identity_target.mail_configured:
                    continue
                await self.call(identity_target, step["tool"], **step["arguments"])
            made = await self.call(identity_target, "get_user", directory=directory, username=username)
            assert made["user"]["enabled"] is True
            member = await self.call(identity_target, "list_user_groups", directory=directory, username=username)
            assert group["id"] in {g["id"] for g in member["groups"]}
            again = await self.call(
                identity_target,
                "plan_joiner",
                directory=directory,
                username=username,
                email=f"{username}@example.com",
                first_name="Conf",
                last_name="Joiner",
            )
            assert again["valid"] is False and again["errors"] and again["proposed_calls"] == []

    async def test_plan_joiner_refuses_an_unknown_group_with_errors_and_no_calls(self, identity_target: Target) -> None:
        plan = await self.call(
            identity_target,
            "plan_joiner",
            directory=identity_target.directory,
            username=_unique(),
            email="nobody@example.com",
            first_name="No",
            last_name="Body",
            group_ids=["00000000-0000-0000-0000-00000000dead"],
        )
        assert plan["valid"] is False and plan["errors"] and plan["proposed_calls"] == []

    async def test_plan_leaver_proposes_disable_logout_and_memberships_and_writes_nothing(
        self, identity_target: Target
    ) -> None:
        directory = identity_target.directory
        group = await self.a_group(identity_target)
        async with self.scratch(identity_target) as s:
            made = await self.make_user(identity_target, s["username"])
            await self.call(
                identity_target,
                "add_user_to_group",
                directory=directory,
                user_id=made["user"]["id"],
                group_id=group["id"],
            )
            plan = await self.call(identity_target, "plan_leaver", directory=directory, user_id=made["user"]["id"])
            assert plan["valid"] is True and plan["errors"] == []
            order = [c["tool"] for c in plan["proposed_calls"]]
            assert order[0] == "disable_user" and "remove_user_from_group" in order
            assert order.index("disable_user") < order.index("remove_user_from_group")
            after = await self.call(identity_target, "get_user", directory=directory, user_id=made["user"]["id"])
            assert after["user"]["enabled"] is True, "a plan must write nothing"
            for step in plan["proposed_calls"]:
                await self.call(identity_target, step["tool"], **step["arguments"])
            done = await self.call(identity_target, "get_user", directory=directory, user_id=made["user"]["id"])
            assert done["user"]["enabled"] is False
            left = await self.call(identity_target, "list_user_groups", directory=directory, user_id=made["user"]["id"])
            assert left["groups"] == []
            replan = await self.call(identity_target, "plan_leaver", directory=directory, user_id=made["user"]["id"])
            assert any("already disabled" in w for w in replan["warnings"])

    async def test_plan_leaver_of_an_unknown_user_is_invalid(self, identity_target: Target) -> None:
        plan = await self.call(identity_target, "plan_leaver", directory=identity_target.directory, username=_unique())
        assert plan["valid"] is False and plan["errors"] and plan["proposed_calls"] == []

    # ---- writes ---------------------------------------------------------------------------------

    async def test_create_user_is_idempotent_for_the_same_action_key_and_the_same_username(
        self, identity_target: Target
    ) -> None:
        async with self.scratch(identity_target) as s:
            first = await self.make_user(identity_target, s["username"], action_key="act-1")
            assert first["created"] is True and first["user"]["username"] == s["username"]
            replay = await self.make_user(identity_target, s["username"], action_key="act-1")
            assert replay["created"] is False and replay["user"]["id"] == first["user"]["id"]
            # Another key, same username: the account exists, nothing is modified.
            other = await self.make_user(identity_target, s["username"], action_key="act-2", last_name="Changed")
            assert other["created"] is False and other["user"]["id"] == first["user"]["id"]
            seen = await self.call(
                identity_target, "get_user", directory=identity_target.directory, username=s["username"]
            )
            assert seen["user"].get("last_name") == "Test"

    async def test_create_user_refuses_what_is_wrong(self, identity_target: Target) -> None:
        for arguments in (
            {"username": ""},
            {"username": "bad\nname"},
            {"username": _unique(), "email": "not-an-address"},
        ):
            await self.refused(identity_target, "create_user", directory=identity_target.directory, **arguments)

    async def test_create_user_sets_no_password_and_returns_no_credential(self, identity_target: Target) -> None:
        async with self.scratch(identity_target) as s:
            made = await self.make_user(identity_target, s["username"], setup_actions=["set_password", "enroll_mfa"])
            text = str(made).lower()
            assert "password" not in {k.lower() for k in made["user"]}
            assert "secret" not in text and "credential" not in text.replace("setup_actions", "")
            assert set(made["user"].get("setup_actions", [])) >= {"set_password", "enroll_mfa"}

    async def test_group_membership_is_idempotent_and_its_compensation_undoes_it(self, identity_target: Target) -> None:
        directory = identity_target.directory
        group = await self.a_group(identity_target)
        async with self.scratch(identity_target) as s:
            made = await self.make_user(identity_target, s["username"])
            uid = made["user"]["id"]
            added = await self.call(
                identity_target, "add_user_to_group", directory=directory, user_id=uid, group_id=group["id"]
            )
            assert (added["was_member"], added["changed"]) == (False, True)
            noop = await self.call(
                identity_target, "add_user_to_group", directory=directory, username=s["username"], group_id=group["id"]
            )
            assert (noop["was_member"], noop["changed"]) == (True, False) and noop["user_id"] == uid

            undo = _operations(identity_target)["add_user_to_group"]["compensation"]
            assert undo["operation"] == "remove_user_from_group"
            removed = await self.call(
                identity_target, undo["operation"], **{"directory": directory, **_render(undo["arguments"], added)}
            )
            assert removed["changed"] is True
            groups = await self.call(identity_target, "list_user_groups", directory=directory, user_id=uid)
            assert group["id"] not in {g["id"] for g in groups["groups"]}

            again = await self.call(
                identity_target, "remove_user_from_group", directory=directory, user_id=uid, group_id=group["id"]
            )
            assert (again["was_member"], again["changed"]) == (False, False)
            redo = _operations(identity_target)["remove_user_from_group"]["compensation"]
            assert redo["operation"] == "add_user_to_group"

    async def test_disable_and_enable_are_idempotent_and_compensate_each_other(self, identity_target: Target) -> None:
        directory = identity_target.directory
        async with self.scratch(identity_target) as s:
            made = await self.make_user(identity_target, s["username"])
            uid = made["user"]["id"]
            off = await self.call(identity_target, "disable_user", directory=directory, user_id=uid)
            assert (off["enabled"], off["previous_enabled"], off["changed"]) == (False, True, True)
            seen = await self.call(identity_target, "get_user", directory=directory, user_id=uid)
            assert seen["user"]["enabled"] is False
            twice = await self.call(identity_target, "disable_user", directory=directory, username=s["username"])
            assert (twice["previous_enabled"], twice["changed"]) == (False, False)

            undo = _operations(identity_target)["disable_user"]["compensation"]
            assert undo["operation"] == "enable_user"
            on = await self.call(
                identity_target, undo["operation"], **{"directory": directory, **_render(undo["arguments"], off)}
            )
            assert (on["enabled"], on["changed"]) == (True, True)
            back = await self.call(identity_target, "get_user", directory=directory, user_id=uid)
            assert back["user"]["enabled"] is True

    async def test_disabling_keeps_the_account_and_its_profile(self, identity_target: Target) -> None:
        directory = identity_target.directory
        async with self.scratch(identity_target) as s:
            made = await self.make_user(identity_target, s["username"])
            await self.call(identity_target, "disable_user", directory=directory, user_id=made["user"]["id"])
            seen = (await self.call(identity_target, "get_user", directory=directory, user_id=made["user"]["id"]))[
                "user"
            ]
            assert (seen["username"], seen.get("email"), seen.get("first_name")) == (
                s["username"],
                f"{s['username']}@example.com",
                "Conf",
            )

    async def test_logout_user_ends_the_sessions_and_says_how_many(self, identity_target: Target) -> None:
        async with self.scratch(identity_target) as s:
            made = await self.make_user(identity_target, s["username"])
            out = await self.call(
                identity_target, "logout_user", directory=identity_target.directory, user_id=made["user"]["id"]
            )
            assert out["sessions_revoked"] >= 0 and out["user_id"] == made["user"]["id"]

    async def test_send_setup_email_is_idempotent_for_the_same_action_key(self, identity_target: Target) -> None:
        directory = identity_target.directory
        async with self.scratch(identity_target) as s:
            await self.make_user(identity_target, s["username"])
            if not identity_target.mail_configured:
                message = await self.refused(
                    identity_target,
                    "send_setup_email",
                    directory=directory,
                    username=s["username"],
                    actions=["set_password"],
                )
                assert "mail" in message.lower()
                return
            first = await self.call(
                identity_target,
                "send_setup_email",
                directory=directory,
                username=s["username"],
                actions=["set_password"],
                action_key="mail-1",
            )
            assert first["sent"] is True and first.get("deduplicated") in (None, False)
            replay = await self.call(
                identity_target,
                "send_setup_email",
                directory=directory,
                username=s["username"],
                actions=["set_password"],
                action_key="mail-1",
            )
            assert replay["deduplicated"] is True
            await self.refused(
                identity_target,
                "send_setup_email",
                directory=directory,
                username=s["username"],
                actions=["not_an_action"],
            )

    # ---- the profile ----------------------------------------------------------------------------

    def test_the_profile_itself_is_well_formed(self) -> None:
        profile = load_profile()
        names = [t["name"] for t in profile["tools"]]
        assert len(names) == len(set(names)) == 24
        assert {t["name"] for t in profile["tools"] if t["access"] == "write"} == {
            "create_user",
            "add_user_to_group",
            "remove_user_from_group",
            "disable_user",
            "enable_user",
            "logout_user",
            "send_setup_email",
        }
        # Nothing in the profile reads a credential, impersonates, or deletes an account.
        assert not [n for n in names if any(w in n for w in ("delete", "impersonate", "password", "secret", "token"))]
