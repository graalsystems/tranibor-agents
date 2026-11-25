# SPDX-License-Identifier: Apache-2.0
"""The checks. Subclass `Conformance` in the implementation's tests and provide a `gitops_target` fixture.

The suite syncs `drifted_app`, rolls `history_app` back and undoes it, and pauses and resumes `auto_sync_app`:
point it at the implementation's fake, or at throwaway applications. It drives the tools exactly as the platform
would: compensation arguments are RENDERED from the descriptor's templates with a sandboxed Jinja (every
argument a string, undefined names refused) and run.
"""

from __future__ import annotations

from typing import Any

import pytest
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator

from .profile import PROFILE_ID, input_schema, load_profile, output_schema, tools
from .target import Target, ToolFailed

WRITES = {"refresh_application", "sync_application", "rollback_application", "set_auto_sync"}
#: Which operation undoes each write that has an undo, and what the descriptor must say.
COMPENSATED = {"rollback_application": "sync_application", "set_auto_sync": "set_auto_sync"}


def _render(template: dict[str, str], result: dict[str, Any]) -> dict[str, str]:
    env = SandboxedEnvironment(undefined=StrictUndefined)
    return {key: env.from_string(value).render(result=result) for key, value in template.items()}


def _operations(target: Target) -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in target.descriptor["operations"]}


class Conformance:
    """Every test takes the `gitops_target` fixture (a `Target`). Nothing here is collected until subclassed."""

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
    async def undo(target: Target, write: str, result: dict[str, Any]) -> dict[str, Any]:
        """Runs the descriptor's compensation of `write`, its arguments rendered from `result` as the platform does."""
        compensation = _operations(target)[write]["compensation"]
        assert compensation["operation"] == COMPENSATED[write], f"{write}: compensated by {compensation['operation']}"
        arguments = _render(compensation["arguments"], result)
        assert all(isinstance(value, str) for value in arguments.values())
        return await Conformance.call(target, compensation["operation"], **arguments)

    # ---- the profile is served ------------------------------------------------------------------

    async def test_every_profile_tool_is_served(self, gitops_target: Target) -> None:
        served = await gitops_target.served()
        missing = sorted(set(tools()) - set(served))
        assert not missing, f"{PROFILE_ID}: tools not served: {missing}"

    async def test_the_descriptor_implements_the_profile_and_declares_every_served_tool(
        self, gitops_target: Target
    ) -> None:
        assert PROFILE_ID in gitops_target.descriptor.get("implements", [])
        declared = set(_operations(gitops_target))
        served = set(await gitops_target.served())
        assert served == declared, (
            f"served but not declared: {sorted(served - declared)}; "
            f"declared but not served: {sorted(declared - served)}"
        )

    async def test_governance_is_at_least_the_profile_floor(self, gitops_target: Target) -> None:
        operations = _operations(gitops_target)
        for name, spec in tools().items():
            declared = operations[name]
            assert declared["access"] == spec["access"], f"{name}: access"
            if spec.get("human_approval"):
                assert declared.get("human_approval") is True, f"{name}: human_approval may be raised, never lowered"
            assert set(spec.get("platform_arguments", [])) <= set(declared.get("platform_arguments", [])), name
        # What changes what runs needs a person, whatever the provider.
        for name in ("sync_application", "rollback_application", "set_auto_sync"):
            assert operations[name].get("human_approval") is True, name
        # The writes that have an undo declare it, and nothing else does.
        for name in COMPENSATED:
            assert "compensation" in operations[name], f"{name} must declare its compensation"
        assert {n for n, op in operations.items() if "compensation" in op} == set(COMPENSATED)

    async def test_served_inputs_accept_what_the_profile_sends(self, gitops_target: Target) -> None:
        served = await gitops_target.served()
        for name, spec in tools().items():
            schema = served[name]
            properties = schema.get("properties", {})
            wanted = input_schema(name).get("properties", {})
            assert set(wanted) <= set(properties), (
                f"{name}: arguments not accepted: {sorted(set(wanted) - set(properties))}"
            )
            extra_required = set(schema.get("required", [])) - set(input_schema(name).get("required", []))
            assert not extra_required, f"{name}: requires more than the profile: {sorted(extra_required)}"
            for argument in spec.get("platform_arguments", []):
                assert argument in properties and argument not in schema.get("required", []), (name, argument)

    # ---- reads ----------------------------------------------------------------------------------

    async def test_get_provider_says_the_scopes(self, gitops_target: Target) -> None:
        provider = await self.call(gitops_target, "get_provider")
        assert provider["instance"] and provider["provider"]
        assert gitops_target.scope in provider["scopes"]
        assert gitops_target.foreign_scope not in provider["scopes"]

    async def test_list_applications_stays_in_the_scopes_and_filters(self, gitops_target: Target) -> None:
        allowed = set((await self.call(gitops_target, "get_provider"))["scopes"])
        listing = await self.call(gitops_target, "list_applications")
        assert listing["total"] == len(listing["applications"]) > 0 and listing["truncated"] is False
        assert {a["scope"] for a in listing["applications"]} <= allowed
        assert gitops_target.foreign_app not in {a["name"] for a in listing["applications"]}
        out = await self.call(gitops_target, "list_applications", sync_status="OutOfSync")
        assert out["applications"] and all(a["sync_status"] == "OutOfSync" for a in out["applications"])
        assert gitops_target.drifted_app in {a["name"] for a in out["applications"]}
        degraded = await self.call(gitops_target, "list_applications", health="Degraded")
        assert all(a["health"] == "Degraded" for a in degraded["applications"])
        only = await self.call(gitops_target, "list_applications", scope=gitops_target.scope)
        assert {a["scope"] for a in only["applications"]} == {gitops_target.scope}
        capped = await self.call(gitops_target, "list_applications", limit=1)
        assert len(capped["applications"]) == 1 and capped["truncated"] is (capped["total"] > 1)

    async def test_reads_answer_the_profile(self, gitops_target: Target) -> None:
        name = gitops_target.history_app
        app = await self.call(gitops_target, "get_application", name=name)
        assert app["name"] == name and app["scope"] == gitops_target.scope
        assert app["auto_sync"] is False and len(app["history"]) >= 2
        resources = await self.call(gitops_target, "list_resources", name=name)
        assert resources["application"] == name and resources["total"] == len(resources["nodes"]) > 0
        sick = await self.call(gitops_target, "list_resources", name=name, unhealthy_only=True)
        assert all(n["health"] not in (None, "Healthy") for n in sick["nodes"])
        diff = await self.call(gitops_target, "get_diff", name=gitops_target.drifted_app)
        assert diff["out_of_sync_total"] == len(diff["resources"]) > 0
        assert await self.call(gitops_target, "list_events", name=name)
        windows = await self.call(gitops_target, "get_sync_windows", name=name)
        assert isinstance(windows["can_sync"], bool)
        history = await self.call(gitops_target, "get_history", name=name)
        assert history["history"] and history["rollback_possible"] is True
        ids = [entry["id"] for entry in history["history"]]
        assert ids == sorted(ids, reverse=True), "the history is newest first"
        meta = await self.call(
            gitops_target, "get_revision_metadata", name=name, revision=history["history"][0]["revision"]
        )
        assert meta["revision"] == history["history"][0]["revision"] and meta["message"]

    # ---- the allow-list -------------------------------------------------------------------------

    async def test_a_scope_outside_the_allow_list_is_refused_before_anything_is_written(
        self, gitops_target: Target
    ) -> None:
        samples: dict[str, Any] = {"history_id": 1, "revision": "abc123", "enabled": False}
        for tool in tools():
            properties = input_schema(tool).get("properties", {})
            if "name" not in properties:
                continue
            before = gitops_target.upstream_writes()
            arguments = {
                "name": gitops_target.foreign_app,
                **{k: samples[k] for k in input_schema(tool).get("required", []) if k in samples},
            }
            message = await self.refused(gitops_target, tool, **arguments)
            assert "allow-list" in message, f"{tool}: the refusal must say 'allow-list': {message}"
            assert gitops_target.upstream_writes() == before, f"{tool}: the provider was written to"
        message = await self.refused(gitops_target, "list_applications", scope=gitops_target.foreign_scope)
        assert "allow-list" in message

    # ---- plan ----------------------------------------------------------------------------------

    async def test_plan_sync_writes_nothing(self, gitops_target: Target) -> None:
        name = gitops_target.drifted_app
        before = await self.call(gitops_target, "get_application", name=name)
        writes = gitops_target.upstream_writes()
        plan = await self.call(gitops_target, "plan_sync", name=name)
        assert plan["mode"] == "full" and plan["blocked"] == []
        assert plan["would_create"] or plan["would_update"]
        assert plan["not_simulated"], "a plan says what it does not simulate"
        assert gitops_target.upstream_writes() == writes
        assert await self.call(gitops_target, "get_application", name=name) == before

    async def test_plan_sync_says_what_blocks_a_sync(self, gitops_target: Target) -> None:
        if gitops_target.running_app is None and gitops_target.denied_app is None:
            pytest.skip("the target has no application with a running operation or a denying window")
        for name in (gitops_target.running_app, gitops_target.denied_app):
            if name is not None:
                assert (await self.call(gitops_target, "plan_sync", name=name))["blocked"]

    # ---- writes --------------------------------------------------------------------------------

    async def test_refresh_changes_nothing_that_runs(self, gitops_target: Target) -> None:
        name = gitops_target.drifted_app
        before = await self.call(gitops_target, "get_application", name=name)
        done = await self.call(gitops_target, "refresh_application", name=name, mode="hard")
        assert done["refresh"] == "hard" and done["application"] == name
        after = await self.call(gitops_target, "get_application", name=name)
        assert (after["sync_status"], after["health"], after["history"]) == (
            before["sync_status"],
            before["health"],
            before["history"],
        )

    async def test_sync_is_full_by_default_and_idempotent_for_the_same_action_key(self, gitops_target: Target) -> None:
        name = gitops_target.drifted_app
        first = await self.call(gitops_target, "sync_application", name=name, action_key="act-1")
        assert first["sync_requested"] is True and first["already_applied"] is False and first["mode"] == "full"
        assert first["prune"] is False, "prune is off unless asked"
        replay = await self.call(gitops_target, "sync_application", name=name, action_key="act-1")
        assert replay["sync_requested"] is False and replay["already_applied"] is True
        assert (await self.call(gitops_target, "get_application", name=name))["sync_status"] == "Synced"

    async def test_a_partial_sync_needs_a_justification(self, gitops_target: Target) -> None:
        name = gitops_target.drifted_app
        diff = (await self.call(gitops_target, "get_diff", name=name))["resources"][0]
        resource = [{"kind": diff["kind"], "name": diff["name"], "namespace": diff.get("namespace") or ""}]
        writes = gitops_target.upstream_writes()
        message = await self.refused(gitops_target, "sync_application", name=name, resources=resource)
        assert "justification" in message and gitops_target.upstream_writes() == writes
        done = await self.call(
            gitops_target,
            "sync_application",
            name=name,
            resources=resource,
            justification="the other resources are held back on purpose by the release owner",
        )
        assert done["mode"] == "selective"

    async def test_sync_is_refused_when_something_blocks_it(self, gitops_target: Target) -> None:
        if gitops_target.running_app is None and gitops_target.denied_app is None:
            pytest.skip("the target has no application with a running operation or a denying window")
        for name in (gitops_target.running_app, gitops_target.denied_app):
            if name is not None:
                writes = gitops_target.upstream_writes()
                await self.refused(gitops_target, "sync_application", name=name)
                assert gitops_target.upstream_writes() == writes

    async def test_rollback_and_its_compensation(self, gitops_target: Target) -> None:
        name = gitops_target.history_app
        before = await self.call(gitops_target, "get_history", name=name)
        oldest = before["history"][-1]
        latest = before["history"][0]
        assert oldest["id"] != latest["id"]
        done = await self.call(gitops_target, "rollback_application", name=name, history_id=oldest["id"])
        assert done["rollback_requested"] is True and done["id"] == oldest["id"]
        assert done["revision"] == oldest["revision"] and done["previous_revision"] == latest["revision"]
        now = await self.call(gitops_target, "get_history", name=name)
        assert now["history"][0]["revision"] == oldest["revision"], "the rollback is now the latest deployment"
        # The undo is a sync back to the revision that was in force, its arguments rendered as the platform does.
        back = await self.undo(gitops_target, "rollback_application", done)
        assert back["sync_requested"] is True and back["revision"] == latest["revision"]
        after = await self.call(gitops_target, "get_history", name=name)
        assert after["history"][0]["revision"] == latest["revision"]

    async def test_rollback_is_refused_with_auto_sync_and_for_a_bad_target(self, gitops_target: Target) -> None:
        writes = gitops_target.upstream_writes()
        await self.refused(gitops_target, "rollback_application", name=gitops_target.auto_sync_app, history_id=1)
        history = await self.call(gitops_target, "get_history", name=gitops_target.history_app)
        await self.refused(
            gitops_target,
            "rollback_application",
            name=gitops_target.history_app,
            history_id=history["history"][0]["id"],
        )
        await self.refused(gitops_target, "rollback_application", name=gitops_target.history_app, history_id=99999)
        assert gitops_target.upstream_writes() == writes

    async def test_set_auto_sync_pauses_resumes_and_is_compensable(self, gitops_target: Target) -> None:
        name = gitops_target.auto_sync_app
        assert (await self.call(gitops_target, "get_application", name=name))["auto_sync"] is True
        paused = await self.call(gitops_target, "set_auto_sync", name=name, enabled=False)
        assert paused["changed"] is True and paused["enabled"] is False and paused["previous_enabled"] is True
        assert (await self.call(gitops_target, "get_application", name=name))["auto_sync"] is False
        assert (await self.call(gitops_target, "get_history", name=name))["rollback_possible"] is True
        # A no-op says so and writes nothing.
        writes = gitops_target.upstream_writes()
        again = await self.call(gitops_target, "set_auto_sync", name=name, enabled=False)
        assert again["changed"] is False and gitops_target.upstream_writes() == writes
        # Undoing it resumes auto-sync.
        resumed = await self.undo(gitops_target, "set_auto_sync", paused)
        assert resumed["changed"] is True and resumed["enabled"] is True
        assert (await self.call(gitops_target, "get_application", name=name))["auto_sync"] is True
        # And undoing the resume pauses again.
        assert (await self.undo(gitops_target, "set_auto_sync", resumed))["enabled"] is False

    def test_the_profile_itself_is_well_formed(self) -> None:
        profile = load_profile()
        names = [t["name"] for t in profile["tools"]]
        assert len(names) == len(set(names)) == 14
        assert {t["name"] for t in profile["tools"] if t["access"] == "write"} == WRITES
