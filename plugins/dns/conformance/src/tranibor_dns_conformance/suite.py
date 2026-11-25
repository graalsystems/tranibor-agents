# SPDX-License-Identifier: Apache-2.0
"""The checks. Subclass `Conformance` in the implementation's tests and provide a `dns_target` fixture.

The suite writes records named `conf-<run>` in `target.zone` and removes them; it never touches another
record. It drives the tools exactly as the platform would: compensation arguments are RENDERED from the
descriptor's templates with a sandboxed Jinja (every argument a string, undefined names refused) and run.
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
    """Every test takes the `dns_target` fixture (a `Target`). Nothing here is collected until subclassed."""

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
    async def records_at(target: Target, name: str, type: str | None = None) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {"zone": target.zone, "name": name}
        if type:
            arguments["type"] = type
        return list((await Conformance.call(target, "list_records", **arguments))["records"])

    @staticmethod
    @asynccontextmanager
    async def scratch(target: Target, **extra: Any) -> AsyncIterator[dict[str, Any]]:
        """A fresh unique name: yields {name}; whatever is left at it is deleted afterwards."""
        name = extra.pop("name", None) or _unique()
        try:
            yield {"name": name}
        finally:
            for record in await Conformance.records_at(target, name):
                await target.call("delete_record", {"zone": target.zone, "id": record["id"]})
            if target.stages_changes:
                await target.call("publish", {"zone": target.zone})

    # ---- the profile is served ------------------------------------------------------------------

    async def test_every_profile_tool_is_served(self, dns_target: Target) -> None:
        served = await dns_target.served()
        missing = sorted(set(tools()) - set(served))
        assert not missing, f"{PROFILE_ID}: tools not served: {missing}"

    async def test_the_descriptor_implements_the_profile_and_declares_every_served_tool(
        self, dns_target: Target
    ) -> None:
        assert PROFILE_ID in dns_target.descriptor.get("implements", [])
        declared = set(_operations(dns_target))
        served = set(await dns_target.served())
        assert served == declared, (
            f"served but not declared: {sorted(served - declared)}; "
            f"declared but not served: {sorted(declared - served)}"
        )

    async def test_governance_is_at_least_the_profile_floor(self, dns_target: Target) -> None:
        operations = _operations(dns_target)
        for name, spec in tools().items():
            declared = operations[name]
            assert declared["access"] == spec["access"], f"{name}: access"
            if spec.get("human_approval"):
                assert declared.get("human_approval") is True, f"{name}: human_approval may be raised, never lowered"
            assert set(spec.get("platform_arguments", [])) <= set(declared.get("platform_arguments", [])), name
        # The writes that have an undo declare it.
        for name in ("update_record", "delete_record"):
            assert "compensation" in operations[name], f"{name} must declare its compensation"
        assert not any("compensation" in op for op in operations.values() if op["access"] == "read")

    async def test_served_inputs_accept_what_the_profile_sends(self, dns_target: Target) -> None:
        served = await dns_target.served()
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

    async def test_get_provider_and_list_zones_say_the_allow_list(self, dns_target: Target) -> None:
        provider = await self.call(dns_target, "get_provider")
        assert provider["instance"] and provider["provider"]
        assert dns_target.zone in provider["zones"] and dns_target.foreign_zone not in provider["zones"]
        zones = (await self.call(dns_target, "list_zones"))["zones"]
        assert dns_target.zone in zones and dns_target.foreign_zone not in zones

    async def test_reads_answer_the_profile(self, dns_target: Target) -> None:
        zone = dns_target.zone
        state = await self.call(dns_target, "get_zone", zone=zone)
        assert state["zone"] == zone
        listing = await self.call(dns_target, "list_records", zone=zone)
        assert listing["count"] == len(listing["records"]) > 0
        assert all(r["zone"] == zone for r in listing["records"])
        first = listing["records"][0]
        again = (await self.call(dns_target, "get_record", zone=zone, id=first["id"]))["record"]
        assert again == first
        exported = await self.call(dns_target, "export_zone", zone=zone)
        assert exported["text"].strip() and exported["truncated"] is False
        typed = await self.call(dns_target, "list_records", zone=zone, type=first["type"])
        assert first["id"] in {r["id"] for r in typed["records"]}
        assert all(r["type"] == first["type"] for r in typed["records"])
        named = await self.records_at(dns_target, first["name"])
        assert first["id"] in {r["id"] for r in named} and all(r["name"] == first["name"] for r in named)

    # ---- the allow-list -------------------------------------------------------------------------

    async def test_a_zone_outside_the_allow_list_is_refused_before_anything_is_sent(self, dns_target: Target) -> None:
        samples: dict[str, Any] = {
            "id": "1",
            "name": "x",
            "type": "A",
            "value": "192.0.2.1",
            "desired": [{"type": "A", "name": "x", "value": "192.0.2.1"}],
        }
        zones = [dns_target.foreign_zone, f"{dns_target.zone}.evil.test", f"x{dns_target.zone}", "../etc"]
        for tool, spec in tools().items():
            if "zone" not in spec["input"].get("properties", {}):
                continue
            for zone in zones:
                before = dns_target.upstream_calls()
                arguments = {"zone": zone, **{k: samples[k] for k in spec["input"].get("required", []) if k in samples}}
                if tool == "plan_changes":
                    arguments["desired"] = samples["desired"]
                message = await self.refused(dns_target, tool, **arguments)
                assert "allow-list" in message, f"{tool} {zone!r}: the refusal must say 'allow-list': {message}"
                assert dns_target.upstream_calls() == before, f"{tool} {zone!r}: the provider was called"

    # ---- plan ----------------------------------------------------------------------------------

    async def test_plan_changes_writes_nothing_and_proposes_the_calls(self, dns_target: Target) -> None:
        zone = dns_target.zone
        async with self.scratch(dns_target) as s:
            before = await self.call(dns_target, "list_records", zone=zone)
            plan = await self.call(
                dns_target,
                "plan_changes",
                zone=zone,
                desired=[{"type": "A", "name": s["name"], "value": "192.0.2.77", "ttl": dns_target.ttl}],
            )
            assert plan["valid"] is True and plan["errors"] == []
            assert plan["summary"]["create"] == 1 and plan["summary"]["delete"] == 0
            calls = plan["proposed_calls"]
            assert calls[0]["tool"] == "create_record" and calls[0]["arguments"]["zone"] == zone
            assert plan["needs_publish"] is dns_target.stages_changes
            assert (calls[-1]["tool"] == "publish") is dns_target.stages_changes
            assert await self.call(dns_target, "list_records", zone=zone) == before
            # What it proposes is what the profile's create_record takes.
            created = await self.call(dns_target, calls[0]["tool"], **calls[0]["arguments"])
            assert created["created"] is True
            again = await self.call(
                dns_target, "plan_changes", zone=zone, desired=[{"type": "A", "name": s["name"], "value": "192.0.2.77"}]
            )
            assert again["summary"] == {"create": 0, "update": 0, "delete": 0, "unchanged": 1}
            assert again["proposed_calls"] == []

    async def test_plan_changes_reports_an_invalid_plan_with_errors_and_no_calls(self, dns_target: Target) -> None:
        plan = await self.call(
            dns_target,
            "plan_changes",
            zone=dns_target.zone,
            desired=[{"type": "CNAME", "name": "", "value": "target.example.com."}],
        )
        assert plan["valid"] is False and plan["errors"] and plan["proposed_calls"] == []

    # ---- writes --------------------------------------------------------------------------------

    async def test_create_record_is_idempotent_for_the_same_action_key(self, dns_target: Target) -> None:
        zone = dns_target.zone
        async with self.scratch(dns_target) as s:
            args = {"zone": zone, "name": s["name"], "type": "A", "value": "192.0.2.31", "ttl": dns_target.ttl}
            first = await self.call(dns_target, "create_record", action_key="act-1", **args)
            assert first["created"] is True and first["needs_publish"] is dns_target.stages_changes
            assert (first["name"], first["type"], first["value"]) == (s["name"], "A", "192.0.2.31")
            replay = await self.call(dns_target, "create_record", action_key="act-1", **args)
            assert replay["created"] is False and replay["id"] == first["id"]
            assert len(await self.records_at(dns_target, s["name"], "A")) == 1
            # A different value at the same name is a different record, not a replay.
            other = await self.call(dns_target, "create_record", **{**args, "value": "192.0.2.32"})
            assert other["created"] is True and other["id"] != first["id"]

    async def test_create_record_refuses_what_is_wrong_in_dns(self, dns_target: Target) -> None:
        zone = dns_target.zone
        for arguments in (
            {"name": "", "type": "CNAME", "value": "target.example.com."},
            {"name": "conf-bad", "type": "A", "value": "not-an-address"},
            {"name": "conf-bad", "type": "NOPE", "value": "x"},
        ):
            await self.refused(dns_target, "create_record", zone=zone, **arguments)
        assert await self.records_at(dns_target, "conf-bad") == []

    async def test_update_record_changes_what_is_given_and_compensation_undoes_it(self, dns_target: Target) -> None:
        zone = dns_target.zone
        async with self.scratch(dns_target) as s:
            made = await self.call(
                dns_target, "create_record", zone=zone, name=s["name"], type="A", value="192.0.2.41", ttl=dns_target.ttl
            )
            updated = await self.call(
                dns_target, "update_record", zone=zone, id=made["id"], value="192.0.2.42", ttl=dns_target.other_ttl
            )
            assert updated["changed"] is True
            assert (updated["value"], updated["ttl"]) == ("192.0.2.42", dns_target.other_ttl)
            assert updated["previous"] == {"value": "192.0.2.41", "ttl": dns_target.ttl}
            now = await self.records_at(dns_target, s["name"], "A")
            assert [(r["value"], r["ttl"]) for r in now] == [("192.0.2.42", dns_target.other_ttl)]
            # A no-op update says so and writes nothing.
            same = await self.call(dns_target, "update_record", zone=zone, id=updated["id"], value="192.0.2.42")
            assert same["changed"] is False

            undo = _operations(dns_target)["update_record"]["compensation"]
            assert undo["operation"] == "update_record"
            await self.call(dns_target, undo["operation"], **_render(undo["arguments"], updated))
            back = await self.records_at(dns_target, s["name"], "A")
            assert [(r["value"], r["ttl"]) for r in back] == [("192.0.2.41", dns_target.ttl)]

    async def test_delete_record_and_its_compensation(self, dns_target: Target) -> None:
        zone = dns_target.zone
        async with self.scratch(dns_target) as s:
            made = await self.call(
                dns_target,
                "create_record",
                zone=zone,
                name=s["name"],
                type="TXT",
                value="v=conf one",
                ttl=dns_target.ttl,
            )
            gone = await self.call(dns_target, "delete_record", zone=zone, id=made["id"])
            assert gone["deleted"] is True and gone["needs_publish"] is dns_target.stages_changes
            assert (gone["name"], gone["type"], gone["value"], gone["ttl"]) == (
                s["name"],
                "TXT",
                made["value"],
                dns_target.ttl,
            )
            assert await self.records_at(dns_target, s["name"]) == []
            await self.refused(dns_target, "get_record", zone=zone, id=made["id"])

            recreate = _operations(dns_target)["delete_record"]["compensation"]
            assert recreate["operation"] == "create_record"
            restored = await self.call(dns_target, recreate["operation"], **_render(recreate["arguments"], gone))
            assert restored["created"] is True
            back = await self.records_at(dns_target, s["name"])
            assert [(r["type"], r["value"], r["ttl"]) for r in back] == [("TXT", made["value"], dns_target.ttl)]
            # Undoing twice is harmless: the record exists, nothing is written.
            twice = await self.call(dns_target, recreate["operation"], **_render(recreate["arguments"], gone))
            assert twice["created"] is False
            assert len(await self.records_at(dns_target, s["name"])) == 1

    # ---- publish -------------------------------------------------------------------------------

    async def test_publish_semantics(self, dns_target: Target) -> None:
        zone = dns_target.zone
        async with self.scratch(dns_target) as s:
            await self.call(dns_target, "publish", zone=zone)  # start from a clean state
            made = await self.call(
                dns_target, "create_record", zone=zone, name=s["name"], type="A", value="192.0.2.51", ttl=dns_target.ttl
            )
            assert made["needs_publish"] is dns_target.stages_changes
            assert (await self.call(dns_target, "get_zone", zone=zone))["pending_changes"] is dns_target.stages_changes
            done = await self.call(dns_target, "publish", zone=zone)
            assert done["pending"] is False
            assert done["published"] is dns_target.stages_changes
            if not dns_target.stages_changes:
                assert done.get("note"), "a provider that applies at once answers a no-op WITH a note"
            assert (await self.call(dns_target, "get_zone", zone=zone))["pending_changes"] is False
            nothing = await self.call(dns_target, "publish", zone=zone)
            assert (nothing["published"], nothing["pending"]) == (False, False)

    def test_the_profile_itself_is_well_formed(self) -> None:
        profile = load_profile()
        names = [t["name"] for t in profile["tools"]]
        assert len(names) == len(set(names)) == 11
        assert {t["name"] for t in profile["tools"] if t["access"] == "write"} == {
            "create_record",
            "update_record",
            "delete_record",
            "publish",
        }
