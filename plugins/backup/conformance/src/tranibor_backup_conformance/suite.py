# SPDX-License-Identifier: Apache-2.0
"""The checks. Subclass `Conformance` in the implementation's tests and provide a `backup_target` fixture.

The suite triggers one backup of `target.schedule`, and restores `target.source` of `target.backup` into the
isolated target the server derives, then cleans that target up; it writes nowhere else. It drives the tools
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

STATUSES = {"succeeded", "partial", "failed", "running", "pending"}


def _render(template: dict[str, str], result: dict[str, Any]) -> dict[str, str]:
    env = SandboxedEnvironment(undefined=StrictUndefined)
    return {key: env.from_string(value).render(result=result) for key, value in template.items()}


def _operations(target: Target) -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in target.descriptor["operations"]}


class Conformance:
    """Every test takes the `backup_target` fixture (a `Target`). Nothing here is collected until subclassed."""

    # ---- helpers --------------------------------------------------------------------------------

    @staticmethod
    async def call(subject: Target, tool: str, **arguments: Any) -> dict[str, Any]:
        """Calls a tool and checks its answer against the profile's output schema."""
        result = await subject.call(tool, arguments)
        errors = sorted(Draft202012Validator(output_schema(tool)).iter_errors(result), key=str)
        assert not errors, f"{tool}: the answer breaks the profile's output schema: {errors[0].message}\n{result}"
        return result

    @staticmethod
    async def refused(subject: Target, tool: str, **arguments: Any) -> str:
        with pytest.raises(ToolFailed) as raised:
            await subject.call(tool, arguments)
        return str(raised.value)

    @staticmethod
    @asynccontextmanager
    async def restore_test(subject: Target, **extra: Any) -> AsyncIterator[dict[str, Any]]:
        """A restore test into the isolated target; whatever it created is cleaned up afterwards."""
        name = extra.pop("name", None) or f"conf-{uuid.uuid4().hex[:6]}"
        key = f"conf-{uuid.uuid4().hex[:8]}"
        done = await Conformance.call(
            subject,
            "run_restore_test",
            backup_id=subject.backup,
            source=subject.source,
            name=name,
            action_key=key,
            **extra,
        )
        try:
            yield {**done, "action_key": key, "name": name}
        finally:
            await subject.call("cleanup_restore_test", {"target": done["target"]})

    # ---- the profile is served ------------------------------------------------------------------

    async def test_every_profile_tool_is_served(self, backup_target: Target) -> None:
        served = await backup_target.served()
        missing = sorted(set(tools()) - set(served))
        assert not missing, f"{PROFILE_ID}: tools not served: {missing}"

    async def test_the_descriptor_implements_the_profile_and_declares_every_served_tool(
        self, backup_target: Target
    ) -> None:
        assert PROFILE_ID in backup_target.descriptor.get("implements", [])
        declared = set(_operations(backup_target))
        served = set(await backup_target.served())
        assert served == declared, (
            f"served but not declared: {sorted(served - declared)}; "
            f"declared but not served: {sorted(declared - served)}"
        )

    async def test_governance_is_at_least_the_profile_floor(self, backup_target: Target) -> None:
        operations = _operations(backup_target)
        for name, spec in tools().items():
            declared = operations[name]
            assert declared["access"] == spec["access"], f"{name}: access"
            if spec.get("human_approval"):
                assert declared.get("human_approval") is True, f"{name}: human_approval may be raised, never lowered"
            assert set(spec.get("platform_arguments", [])) <= set(declared.get("platform_arguments", [])), name
        # A restore writes data: it is approved by a person, and it declares how it is undone.
        assert operations["run_restore_test"]["human_approval"] is True
        undo = operations["run_restore_test"]["compensation"]
        assert undo["operation"] == "cleanup_restore_test"
        assert not any("compensation" in op for op in operations.values() if op["access"] == "read")

    async def test_served_inputs_accept_what_the_profile_sends(self, backup_target: Target) -> None:
        served = await backup_target.served()
        for name, spec in tools().items():
            schema = served[name]
            properties = schema.get("properties", {})
            wanted = spec["input"].get("properties", {})
            assert set(wanted) <= set(properties), (
                f"{name}: arguments not accepted: {sorted(set(wanted) - set(properties))}"
            )
            extra_required = set(schema.get("required", [])) - set(spec["input"].get("required", []))
            assert not extra_required, f"{name}: requires more than the profile: {sorted(extra_required)}"
            for argument in spec.get("platform_arguments", []):
                assert argument in properties and argument not in schema.get("required", []), (name, argument)

    # ---- reads ----------------------------------------------------------------------------------

    async def test_get_provider_says_where_a_restore_may_go(self, backup_target: Target) -> None:
        provider = await self.call(backup_target, "get_provider")
        assert provider["instance"] and provider["provider"]
        assert backup_target.target_prefix in provider["restore_target_rule"]

    async def test_reads_answer_the_profile(self, backup_target: Target) -> None:
        target = backup_target
        schedules = (await self.call(target, "list_schedules"))["schedules"]
        assert target.schedule in {s["id"] for s in schedules}

        listing = await self.call(target, "list_backups", limit=100)
        assert listing["total"] >= len(listing["backups"]) > 0
        assert all(b["status"] in STATUSES for b in listing["backups"])
        mine = await self.call(target, "list_backups", schedule=target.schedule)
        assert mine["backups"] and all(b["schedule"] == target.schedule for b in mine["backups"])
        starts = [b["start"] or "" for b in mine["backups"]]
        assert starts == sorted(starts, reverse=True), "list_backups: newest first"
        one = await self.call(target, "list_backups", limit=1)
        assert len(one["backups"]) == 1 and one["truncated"] is (one["total"] > 1)
        done = await self.call(target, "list_backups", status="succeeded")
        assert all(b["status"] == "succeeded" for b in done["backups"])

        backup = (await self.call(target, "get_backup", id=target.backup))["backup"]
        assert backup["id"] == target.backup and backup["status"] in {"succeeded", "partial"}
        if target.unrestorable_backup:
            bad = (await self.call(target, "get_backup", id=target.unrestorable_backup))["backup"]
            assert bad["status"] in {"failed", "pending"}
        await self.refused(target, "get_backup", id="conf-does-not-exist")

        locations = (await self.call(target, "list_locations"))["locations"]
        assert locations and all(isinstance(loc["available"], bool) for loc in locations)

        restores = await self.call(target, "list_restores", limit=5)
        assert restores["total"] >= len(restores["restores"])
        assert all(r["status"] in STATUSES for r in restores["restores"])

        fresh = (await self.call(target, "backup_freshness"))["schedules"]
        mine_fresh = next(f for f in fresh if f["schedule"] == target.schedule)
        assert mine_fresh["freshness"] in {"fresh", "stale", "never", "paused"}
        wide = (await self.call(target, "backup_freshness", max_age_hours=24 * 365))["schedules"]
        assert all(f["allowed_age_hours"] == 24 * 365 for f in wide)

        await self.call(target, "storage_inventory")

    async def test_a_partial_backup_is_never_counted_as_fresh(self, backup_target: Target) -> None:
        # The newest backup of a schedule may be failed or partial; the verdict rests on the newest SUCCEEDED one.
        for row in (await self.call(backup_target, "backup_freshness"))["schedules"]:
            if row["last_good_backup"] is not None:
                good = (await self.call(backup_target, "get_backup", id=row["last_good_backup"]))["backup"]
                assert good["status"] == "succeeded", row

    # ---- trigger_backup ------------------------------------------------------------------------

    async def test_trigger_backup_is_idempotent_for_the_same_action_key(self, backup_target: Target) -> None:
        key = f"conf-{uuid.uuid4().hex[:8]}"
        first = await self.call(backup_target, "trigger_backup", schedule=backup_target.schedule, action_key=key)
        assert first["already_existed"] is False and first["schedule"] == backup_target.schedule
        replay = await self.call(backup_target, "trigger_backup", schedule=backup_target.schedule, action_key=key)
        assert replay["already_existed"] is True and replay["backup_id"] == first["backup_id"]
        seen = (await self.call(backup_target, "get_backup", id=first["backup_id"]))["backup"]
        assert seen["id"] == first["backup_id"]
        other = await self.call(backup_target, "trigger_backup", schedule=backup_target.schedule)
        assert other["backup_id"] != first["backup_id"]

    async def test_trigger_backup_refuses_a_schedule_that_does_not_exist(self, backup_target: Target) -> None:
        await self.refused(backup_target, "trigger_backup", schedule="conf-does-not-exist")

    # ---- run_restore_test, cleanup_restore_test ------------------------------------------------

    async def test_a_restore_test_goes_into_the_allowed_target_and_compensation_undoes_it(
        self, backup_target: Target
    ) -> None:
        target = backup_target
        async with self.restore_test(target) as done:
            assert done["target"].startswith(target.target_prefix), "the target must be inside the allowed rule"
            assert done["already_existed"] is False and done["backup_id"] == target.backup
            assert done["source"] == target.source
            # The restore is visible, as a restore of that backup, into that target, made by this server.
            seen = (await self.call(target, "get_restore", id=done["restore_id"]))["restore"]
            assert (seen["backup_id"], seen["target"], seen["made_by_this_server"]) == (
                target.backup,
                done["target"],
                True,
            )
            assert done["restore_id"] in {
                r["id"] for r in (await self.call(target, "list_restores", backup_id=target.backup))["restores"]
            }
            # Idempotent through the action_key: a replay is the same restore.
            replay = await self.call(
                target,
                "run_restore_test",
                backup_id=target.backup,
                source=target.source,
                name=done["name"],
                action_key=done["action_key"],
            )
            assert replay["already_existed"] is True and replay["restore_id"] == done["restore_id"]
            # Compensation, rendered the way the platform does it.
            undo = _operations(target)["run_restore_test"]["compensation"]
            gone = await self.call(target, undo["operation"], **_render(undo["arguments"], done))
            assert gone["deleted"] is True
            again = await self.call(target, undo["operation"], **_render(undo["arguments"], done))
            assert again["deleted"] is False and again.get("already_gone") is True

    async def test_a_backup_that_is_not_restorable_is_not_restored(self, backup_target: Target) -> None:
        if not backup_target.unrestorable_backup:
            pytest.skip("the implementation has no backup that cannot be restored")
        before = len((await self.call(backup_target, "list_restores", limit=100))["restores"])
        await self.refused(
            backup_target, "run_restore_test", backup_id=backup_target.unrestorable_backup, source=backup_target.source
        )
        assert len((await self.call(backup_target, "list_restores", limit=100))["restores"]) == before

    async def test_run_restore_test_refuses_an_unknown_backup(self, backup_target: Target) -> None:
        await self.refused(
            backup_target, "run_restore_test", backup_id="conf-does-not-exist", source=backup_target.source
        )

    async def test_a_target_outside_the_allow_list_is_refused_before_anything_is_sent(
        self, backup_target: Target
    ) -> None:
        for outside in backup_target.outside_targets:
            before = backup_target.upstream_calls()
            message = await self.refused(backup_target, "cleanup_restore_test", target=outside)
            assert "allow-list" in message, f"{outside!r}: the refusal must say 'allow-list': {message}"
            assert backup_target.upstream_calls() == before, f"{outside!r}: the backup system was called"

    async def test_the_label_of_the_target_cannot_escape_the_rule(self, backup_target: Target) -> None:
        for label in ("../x", "A B", "kube-system/../x", "x" * 80):
            before = backup_target.upstream_calls()
            await self.refused(
                backup_target,
                "run_restore_test",
                backup_id=backup_target.backup,
                source=backup_target.source,
                name=label,
            )
            assert backup_target.upstream_calls() == before, f"{label!r}: the backup system was called"

    def test_the_profile_itself_is_well_formed(self) -> None:
        profile = load_profile()
        names = [t["name"] for t in profile["tools"]]
        assert len(names) == len(set(names)) == 12
        assert {t["name"] for t in profile["tools"] if t["access"] == "write"} == {
            "trigger_backup",
            "run_restore_test",
            "cleanup_restore_test",
        }
        assert next(t for t in profile["tools"] if t["name"] == "run_restore_test")["human_approval"] is True
