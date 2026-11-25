# SPDX-License-Identifier: Apache-2.0
"""The checks. Subclass `Conformance` in the implementation's tests and provide a `lan_target` fixture.

The suite reads the site it is given and, for the writes, restarts and power-cycles the devices it is given and
(when the provider supports it) blocks and unblocks the client it is given. It drives the tools exactly as the
platform would: compensation arguments are RENDERED from the descriptor's templates with a sandboxed Jinja
(every argument a string, undefined names refused) and run.

A tool the provider cannot serve answers `not_supported` and is listed in `get_provider.unsupported_tools`:
the suite then requires exactly that (an error, nothing sent upstream) instead of the write's effect.
"""

from __future__ import annotations

from typing import Any

import pytest
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator

from .profile import PROFILE_ID, load_profile, output_schema, tools
from .target import Target, ToolFailed

SECRETISH = ("pass", "psk", "secret", "token", "api_key")


def _render(template: dict[str, str], result: dict[str, Any]) -> dict[str, str]:
    env = SandboxedEnvironment(undefined=StrictUndefined)
    return {key: env.from_string(value).render(result=result) for key, value in template.items()}


def _operations(target: Target) -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in target.descriptor["operations"]}


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return dict(load_profile()["$defs"][ref.rsplit("/", 1)[-1]])
    return schema


def _sample(schema: dict[str, Any]) -> Any:
    """A value of the schema's shape: enough to render a compensation's templates."""
    schema = _resolve(schema)
    kind = schema.get("type")
    kind = next((k for k in kind if k != "null"), "string") if isinstance(kind, list) else kind
    if kind == "object":
        return {k: _sample(v) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return [_sample(schema.get("items", {}))]
    if kind == "integer":
        return 1
    if kind == "boolean":
        return True
    return "x"


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(k) for k in value} | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


class Conformance:
    """Every test takes the `lan_target` fixture (a `Target`). Nothing here is collected until subclassed."""

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
    async def unsupported(target: Target) -> set[str]:
        provider = await Conformance.call(target, "get_provider")
        return set(provider["unsupported_tools"])

    # ---- the profile is served ------------------------------------------------------------------

    async def test_every_profile_tool_is_served(self, lan_target: Target) -> None:
        served = await lan_target.served()
        missing = sorted(set(tools()) - set(served))
        assert not missing, f"{PROFILE_ID}: tools not served: {missing}"

    async def test_the_descriptor_implements_the_profile_and_declares_every_served_tool(
        self, lan_target: Target
    ) -> None:
        assert PROFILE_ID in lan_target.descriptor.get("implements", [])
        declared = set(_operations(lan_target))
        served = set(await lan_target.served())
        assert served == declared, (
            f"served but not declared: {sorted(served - declared)}; "
            f"declared but not served: {sorted(declared - served)}"
        )

    async def test_governance_is_at_least_the_profile_floor(self, lan_target: Target) -> None:
        operations = _operations(lan_target)
        for name, spec in tools().items():
            declared = operations[name]
            assert declared["access"] == spec["access"], f"{name}: access"
            if spec.get("human_approval"):
                assert declared.get("human_approval") is True, f"{name}: human_approval may be raised, never lowered"
            assert set(spec.get("platform_arguments", [])) <= set(declared.get("platform_arguments", [])), name
        # Block and unblock undo each other, whatever the provider can do today.
        for name in ("block_client", "unblock_client"):
            assert "compensation" in operations[name], f"{name} must declare its compensation"
        assert not any("compensation" in op for op in operations.values() if op["access"] == "read")

    async def test_every_compensation_renders_and_calls_a_tool_with_arguments_it_accepts(
        self, lan_target: Target
    ) -> None:
        served = await lan_target.served()
        for name, op in _operations(lan_target).items():
            undo = op.get("compensation")
            if undo is None:
                continue
            assert undo["operation"] in served, f"{name}: compensation names a tool that is not served"
            assert _operations(lan_target)[undo["operation"]]["access"] == "write", name
            # A result of the shape of the tool's own output (profile tools), or a plausible one for an extra.
            if name in tools():
                result = _sample(output_schema(name))
            else:
                result = {"site_id": "x", "client_id": "x", "voucher_name": "x"}
            rendered = _render(undo["arguments"], result)
            properties = served[undo["operation"]].get("properties", {})
            assert set(rendered) <= set(properties), f"{name}: compensation arguments not accepted: {sorted(rendered)}"
            required = set(served[undo["operation"]].get("required", []))
            assert required <= set(rendered) | set(op.get("platform_arguments", [])), (
                f"{name}: compensation lacks {required - set(rendered)}"
            )

    async def test_served_inputs_accept_what_the_profile_sends(self, lan_target: Target) -> None:
        served = await lan_target.served()
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

    async def test_get_provider_names_the_instance_the_sites_and_what_it_cannot_serve(self, lan_target: Target) -> None:
        provider = await self.call(lan_target, "get_provider")
        assert provider["instance"] and provider["provider"]
        assert lan_target.site_id in {s["id"] for s in provider["sites"]}
        write_tools = {n for n, t in tools().items() if t["access"] == "write"}
        assert set(provider["unsupported_tools"]) <= write_tools, "only writes may be unsupported"
        sites = (await self.call(lan_target, "list_sites"))["sites"]
        assert {s["id"] for s in sites} == {s["id"] for s in provider["sites"]}

    async def test_device_reads_answer_the_profile(self, lan_target: Target) -> None:
        site = lan_target.site_id
        listing = await self.call(lan_target, "list_devices", site_id=site)
        assert listing["site_id"] == site and listing["devices"]
        ids = {d["id"] for d in listing["devices"]}
        assert lan_target.device_id in ids
        online = next(d for d in listing["devices"] if d["id"] == lan_target.device_id)
        assert online["state"] == "ONLINE"
        only = await self.call(lan_target, "list_devices", site_id=site, state="ONLINE")
        assert only["devices"] and all(d["state"] == "ONLINE" for d in only["devices"])
        detail = await self.call(lan_target, "get_device", site_id=site, device_id=lan_target.device_id)
        assert detail["device"]["id"] == lan_target.device_id
        stats = await self.call(lan_target, "get_device_statistics", site_id=site, device_id=lan_target.device_id)
        assert stats["device_id"] == lan_target.device_id
        poe = await self.call(lan_target, "get_device", site_id=site, device_id=lan_target.poe_device_id)
        assert lan_target.poe_port in {p["idx"] for p in poe["ports"]}
        if lan_target.offline_device_id:
            offline = await self.call(lan_target, "list_devices", site_id=site, state="OFFLINE")
            assert lan_target.offline_device_id in {d["id"] for d in offline["devices"]}

    async def test_client_network_and_firmware_reads_answer_the_profile(self, lan_target: Target) -> None:
        site = lan_target.site_id
        clients = await self.call(lan_target, "list_clients", site_id=site)
        assert lan_target.client_id in {c["id"] for c in clients["clients"]}
        one = await self.call(lan_target, "get_client", site_id=site, client_id=lan_target.client_id)
        assert one["client"]["id"] == lan_target.client_id
        wired = await self.call(lan_target, "list_clients", site_id=site, type="WIRED")
        assert all(c["type"] == "WIRED" for c in wired["clients"])
        networks = await self.call(lan_target, "list_networks", site_id=site)
        assert networks["networks"]
        wlans = await self.call(lan_target, "list_wlans", site_id=site)
        assert wlans["wlans"]
        assert not {k for k in _keys(wlans) if any(s in k.lower() for s in SECRETISH)}, "a WLAN never carries a secret"
        firmware = await self.call(lan_target, "firmware_status", site_id=site)
        assert firmware["devices_examined"] >= len(firmware["updatable"])

    async def test_an_unknown_site_device_or_client_is_refused(self, lan_target: Target) -> None:
        missing, site = lan_target.missing_id, lan_target.site_id
        await self.refused(lan_target, "list_devices", site_id=missing)
        await self.refused(lan_target, "get_device", site_id=site, device_id=missing)
        await self.refused(lan_target, "get_device_statistics", site_id=site, device_id=missing)
        await self.refused(lan_target, "get_client", site_id=site, client_id=missing)

    # ---- writes the provider cannot serve ----------------------------------------------------------

    async def test_an_unsupported_tool_says_not_supported_and_sends_nothing_upstream(self, lan_target: Target) -> None:
        arguments = {
            "block_client": {"client_id": lan_target.client_id},
            "unblock_client": {"client_id": lan_target.client_id},
            "upgrade_firmware": {"device_id": lan_target.device_id},
        }
        for tool in sorted(await self.unsupported(lan_target)):
            before = lan_target.upstream_calls()
            message = await self.refused(lan_target, tool, site_id=lan_target.site_id, **arguments[tool])
            assert "not_supported" in message, f"{tool}: the refusal must say 'not_supported': {message}"
            assert lan_target.upstream_calls() == before, f"{tool}: the provider was called"

    # ---- writes ----------------------------------------------------------------------------------

    async def test_restart_device_answers_the_profile(self, lan_target: Target) -> None:
        done = await self.call(
            lan_target, "restart_device", site_id=lan_target.site_id, device_id=lan_target.restart_device_id
        )
        assert done["target"] == lan_target.restart_device_id and done["site_id"] == lan_target.site_id

    async def test_power_cycle_port_answers_the_profile_and_refuses_a_port_that_does_not_exist(
        self, lan_target: Target
    ) -> None:
        site, switch = lan_target.site_id, lan_target.poe_device_id
        done = await self.call(
            lan_target, "power_cycle_port", site_id=site, device_id=switch, port_idx=lan_target.poe_port
        )
        assert done["target"].startswith(switch)
        await self.refused(lan_target, "power_cycle_port", site_id=site, device_id=switch, port_idx=999)

    async def test_block_and_unblock_undo_each_other_when_supported(self, lan_target: Target) -> None:
        if {"block_client", "unblock_client"} & await self.unsupported(lan_target):
            pytest.skip("the provider cannot block a client: covered by the not_supported test")
        site, client = lan_target.site_id, lan_target.client_id
        blocked = await self.call(lan_target, "block_client", site_id=site, client_id=client)
        assert blocked["blocked"] is True and blocked["client_id"] == client
        undo = _operations(lan_target)["block_client"]["compensation"]
        assert undo["operation"] == "unblock_client"
        back = await self.call(lan_target, undo["operation"], **_render(undo["arguments"], blocked))
        assert back["blocked"] is False
        redo = _operations(lan_target)["unblock_client"]["compensation"]
        assert redo["operation"] == "block_client"
        again = await self.call(lan_target, redo["operation"], **_render(redo["arguments"], back))
        assert again["blocked"] is True
        await self.call(lan_target, "unblock_client", site_id=site, client_id=client)

    async def test_upgrade_firmware_answers_the_profile_when_supported(self, lan_target: Target) -> None:
        if "upgrade_firmware" in await self.unsupported(lan_target):
            pytest.skip("the provider cannot upgrade a firmware: covered by the not_supported test")
        done = await self.call(
            lan_target, "upgrade_firmware", site_id=lan_target.site_id, device_id=lan_target.restart_device_id
        )
        assert done["accepted"] is True

    def test_the_profile_itself_is_well_formed(self) -> None:
        profile = load_profile()
        names = [t["name"] for t in profile["tools"]]
        assert len(names) == len(set(names)) == 15
        assert {t["name"] for t in profile["tools"] if t["access"] == "write"} == {
            "restart_device",
            "power_cycle_port",
            "block_client",
            "unblock_client",
            "upgrade_firmware",
        }
        for tool in profile["tools"]:
            if tool["name"] in ("restart_device", "power_cycle_port", "block_client", "upgrade_firmware"):
                assert tool["human_approval"] is True, tool["name"]
