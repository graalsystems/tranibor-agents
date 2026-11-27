# SPDX-License-Identifier: Apache-2.0
"""The checks. Subclass `Conformance` in the implementation's tests and provide a `certificates_target` fixture.

The profile is read-only, so the suite writes nothing: it calls every tool, validates every answer against the
profile's output schemas, and checks the rules the schemas cannot say (order, filters, references between
answers, the allow-list refusal, the descriptor's governance).
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator

from .profile import PROFILE_ID, load_profile, output_schema, tools
from .target import Target, ToolFailed

SEVERITIES = {"critical", "warning", "info"}


def _operations(target: Target) -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in target.descriptor["operations"]}


class Conformance:
    """Every test takes the `certificates_target` fixture (a `Target`). Nothing here is collected until subclassed."""

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
    async def everything(target: Target) -> list[dict[str, Any]]:
        listing = await Conformance.call(target, "list_certificates", limit=200)
        assert not listing["truncated"], "the fake holds more than 200 certificates: the suite cannot read them all"
        return list(listing["certificates"])

    # ---- the profile is served, read-only -------------------------------------------------------

    async def test_every_profile_tool_is_served(self, certificates_target: Target) -> None:
        served = await certificates_target.served()
        missing = sorted(set(tools()) - set(served))
        assert not missing, f"{PROFILE_ID}: tools not served: {missing}"

    async def test_the_descriptor_implements_the_profile_and_declares_every_served_tool(
        self, certificates_target: Target
    ) -> None:
        assert PROFILE_ID in certificates_target.descriptor.get("implements", [])
        declared = set(_operations(certificates_target))
        served = set(await certificates_target.served())
        assert served == declared, (
            f"served but not declared: {sorted(served - declared)}; "
            f"declared but not served: {sorted(declared - served)}"
        )

    async def test_the_profile_is_read_only_and_so_is_the_descriptor(self, certificates_target: Target) -> None:
        operations = _operations(certificates_target)
        for name, spec in tools().items():
            assert spec["access"] == "read", f"the profile is read-only in v1: {name}"
            declared = operations[name]
            assert declared["access"] == "read", f"{name}: access"
            assert "compensation" not in declared, f"{name}: a read has nothing to undo"
        assert all(op["access"] == "read" for op in operations.values()), "a server of this profile writes nothing"

    async def test_served_inputs_accept_what_the_profile_sends(self, certificates_target: Target) -> None:
        served = await certificates_target.served()
        for name, spec in tools().items():
            schema = served[name]
            properties = schema.get("properties", {})
            wanted = spec["input"].get("properties", {})
            assert set(wanted) <= set(properties), (
                f"{name}: arguments not accepted: {sorted(set(wanted) - set(properties))}"
            )
            extra_required = set(schema.get("required", [])) - set(spec["input"].get("required", []))
            assert not extra_required, f"{name}: requires more than the profile: {sorted(extra_required)}"

    # ---- reads ----------------------------------------------------------------------------------

    async def test_get_provider_says_what_the_instance_looks_at(self, certificates_target: Target) -> None:
        provider = await self.call(certificates_target, "get_provider")
        assert provider["instance"] and provider["provider"]
        assert provider["scope"], "an instance names what it may look at"

    async def test_list_certificates_is_sorted_by_soonest_expiry(self, certificates_target: Target) -> None:
        certificates = await self.everything(certificates_target)
        assert certificates, "the fake holds no certificate: the suite has nothing to check"
        assert len({c["id"] for c in certificates}) == len(certificates), "ids are unique within an instance"
        issued = [c for c in certificates if "not_after" in c]
        assert all("days_remaining" in c for c in issued)
        # Not issued yet first, then by soonest not_after.
        flags = [("not_after" in c, c.get("not_after", "")) for c in certificates]
        assert flags == sorted(flags), "soonest expiry first, certificates with no issued certificate before all"

    async def test_within_days_and_name_filter(self, certificates_target: Target) -> None:
        everything = await self.everything(certificates_target)
        window = certificates_target.window_days
        within = await self.call(certificates_target, "list_certificates", within_days=window, limit=200)
        ids = {c["id"] for c in within["certificates"]}
        expected = {c["id"] for c in everything if "days_remaining" not in c or c["days_remaining"] <= window}
        assert ids == expected
        assert within["total"] == len(expected)
        assert 0 < len(expected) < len(everything), "the fake must hold certificates both in and out of the window"
        # A name filter keeps the certificates that cover it, and only those.
        covered = next(c for c in everything if c["names"])
        name = covered["names"][0].removeprefix("*.")
        named = await self.call(certificates_target, "list_certificates", name=name, limit=200)
        assert covered["id"] in {c["id"] for c in named["certificates"]}
        capped = await self.call(certificates_target, "list_certificates", limit=1)
        assert len(capped["certificates"]) == 1 and capped["truncated"] is (capped["total"] > 1)

    async def test_get_certificate_matches_its_list_entry(self, certificates_target: Target) -> None:
        for listed in await self.everything(certificates_target):
            one = await self.call(certificates_target, "get_certificate", id=listed["id"])
            assert one["certificate"]["id"] == listed["id"]
            assert one["certificate"]["names"] == listed["names"]
            assert one["certificate"].get("not_after") == listed.get("not_after")

    async def test_a_certificate_outside_the_scope_is_refused(self, certificates_target: Target) -> None:
        before = certificates_target.upstream_calls()
        message = await self.refused(certificates_target, "get_certificate", id=certificates_target.foreign_id)
        if certificates_target.refusal_is_local:
            assert "allow-list" in message or "configured" in message, f"the refusal must say why: {message}"
            assert certificates_target.upstream_calls() == before, "the source was called for an id nobody configured"

    async def test_an_unknown_id_is_refused(self, certificates_target: Target) -> None:
        await self.refused(certificates_target, "get_certificate", id=certificates_target.unknown_id)

    async def test_findings_are_well_formed_and_refer_to_known_certificates(self, certificates_target: Target) -> None:
        window = certificates_target.window_days
        known = {c["id"] for c in await self.everything(certificates_target)}
        found = await self.call(certificates_target, "list_findings", within_days=window)
        assert found["within_days"] == window and found["total"] == len(found["findings"])
        assert found["findings"], "the fake must hold something wrong or about to expire"
        ids = [f["id"] for f in found["findings"]]
        assert len(ids) == len(set(ids)), "a finding's id is unique"
        order = {"critical": 0, "warning": 1, "info": 2}
        assert [order[f["severity"]] for f in found["findings"]] == sorted(
            order[f["severity"]] for f in found["findings"]
        )
        for finding in found["findings"]:
            assert finding["severity"] in SEVERITIES, finding
            assert finding["evidence"], f"a finding without evidence: {finding['id']}"
            if finding["kind"] != "unreachable":
                assert finding["certificate"] in known, f"{finding['id']}: refers to no listed certificate"

    async def test_findings_follow_the_window(self, certificates_target: Target) -> None:
        window = certificates_target.window_days
        everything = {c["id"]: c for c in await self.everything(certificates_target)}
        found = await self.call(certificates_target, "list_findings", within_days=window)
        for finding in found["findings"]:
            if finding["kind"] == "expires_soon":
                assert everything[finding["certificate"]]["days_remaining"] <= window
        wide = await self.call(certificates_target, "list_findings", within_days=3650)
        assert wide["total"] >= found["total"]
        one = next(f for f in found["findings"] if f["kind"] != "unreachable")["certificate"]
        only = await self.call(certificates_target, "list_findings", within_days=window, certificate=one)
        assert only["findings"] and {f["certificate"] for f in only["findings"]} == {one}

    async def test_an_expired_certificate_is_a_critical_finding_and_a_healthy_one_is_none(
        self, certificates_target: Target
    ) -> None:
        window = certificates_target.window_days
        everything = await self.everything(certificates_target)
        flagged = {
            f["certificate"]
            for f in (await self.call(certificates_target, "list_findings", within_days=window))["findings"]
        }
        expired = [c for c in everything if c.get("expired") or c.get("days_remaining", 0) < 0]
        assert not [c["id"] for c in expired if c["id"] not in flagged], "an expired certificate raises a finding"
        healthy = [
            c
            for c in everything
            if c.get("days_remaining", -1) > window
            and c["renewal"]["state"] in ("ok", "unknown")
            and c.get("trusted", True)
            and c.get("chain_complete", True)
            and c.get("hostname_matches", True)
        ]
        assert healthy, "the fake must hold a healthy certificate"
        assert not [c["id"] for c in healthy if c["id"] in flagged], "a healthy certificate must raise no finding"

    def test_the_profile_itself_is_well_formed(self) -> None:
        profile = load_profile()
        names = [t["name"] for t in profile["tools"]]
        assert len(names) == len(set(names)) == 4
        assert {t["access"] for t in profile["tools"]} == {"read"}
