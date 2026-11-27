# SPDX-License-Identifier: Apache-2.0
"""The checks. Subclass `Conformance` in the implementation's tests and provide an `observability_target` fixture.

The suite silences ONE alert (`target.alert_labels`) for a few minutes and expires the silence, and adds
annotations tagged `conf-<run>` (an annotation has no undo: a live stack keeps them, a fake forgets them). It
drives the tools exactly as the platform would: compensation arguments are RENDERED from the descriptor's
templates with a sandboxed Jinja (every argument a string, undefined names refused) and run.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Conformance:
    """Every test takes the `observability_target` fixture (a `Target`). Nothing here is collected until subclassed."""

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
    @asynccontextmanager
    async def silenced(target: Target, key: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Silences the target's alert for a few minutes; the silence is expired afterwards."""
        made = await Conformance.call(
            target,
            "create_silence",
            matchers=target.alert_labels,
            duration_minutes=5,
            comment=f"conformance {_unique()}",
            **({"action_key": key} if key else {}),
        )
        try:
            yield made
        finally:
            await target.call("expire_silence", {"silence_id": made["silence_id"]})

    # ---- the profile is served ------------------------------------------------------------------

    async def test_every_profile_tool_is_served(self, observability_target: Target) -> None:
        served = await observability_target.served()
        missing = sorted(set(tools()) - set(served))
        assert not missing, f"{PROFILE_ID}: tools not served: {missing}"

    async def test_the_descriptor_implements_the_profile_and_declares_every_served_tool(
        self, observability_target: Target
    ) -> None:
        assert PROFILE_ID in observability_target.descriptor.get("implements", [])
        declared = set(_operations(observability_target))
        served = set(await observability_target.served())
        assert served == declared, (
            f"served but not declared: {sorted(served - declared)}; "
            f"declared but not served: {sorted(declared - served)}"
        )

    async def test_governance_is_at_least_the_profile_floor(self, observability_target: Target) -> None:
        operations = _operations(observability_target)
        for name, spec in tools().items():
            declared = operations[name]
            assert declared["access"] == spec["access"], f"{name}: access"
            if spec.get("human_approval"):
                assert declared.get("human_approval") is True, f"{name}: human_approval may be raised, never lowered"
            assert set(spec.get("platform_arguments", [])) <= set(declared.get("platform_arguments", [])), name
        # A silence hides alerts from people: a person approves it, and it can be undone.
        assert operations["create_silence"]["human_approval"] is True
        assert operations["create_silence"]["compensation"]["operation"] == "expire_silence"
        assert not any("compensation" in op for op in operations.values() if op["access"] == "read")

    async def test_served_inputs_accept_what_the_profile_sends(self, observability_target: Target) -> None:
        served = await observability_target.served()
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

    async def test_get_provider_and_list_sources_say_the_allow_list(self, observability_target: Target) -> None:
        t = observability_target
        provider = await self.call(t, "get_provider")
        assert provider["instance"] and provider["provider"]
        assert {t.metrics_source, t.logs_source} <= set(provider["sources"])
        assert t.foreign_source not in provider["sources"]
        sources = {s["id"]: s for s in (await self.call(t, "list_sources"))["sources"]}
        assert sources[t.metrics_source]["kind"] == "metrics" and sources[t.logs_source]["kind"] == "logs"
        assert t.foreign_source not in sources

    async def test_alerts_come_with_their_rule(self, observability_target: Target) -> None:
        t = observability_target
        listing = await self.call(t, "list_firing_alerts")
        assert listing["total"] >= 1 and listing["untrusted"] is True
        mine = [a for a in listing["alerts"] if all(a["labels"].get(k) == v for k, v in t.alert_labels.items())]
        assert len(mine) == 1 and mine[0]["rule_id"] == t.rule_id
        assert all(a["state"] for a in listing["alerts"])
        starts = [a["starts_at"] or "" for a in listing["alerts"]]
        assert starts == sorted(starts, reverse=True), "newest first"
        by_labels = await self.call(t, "list_firing_alerts", labels={"alertname": t.alert_labels["alertname"]})
        assert mine[0]["id"] in {a["id"] for a in by_labels["alerts"]}
        assert all(a["labels"]["alertname"] == t.alert_labels["alertname"] for a in by_labels["alerts"])
        nothing = await self.call(t, "list_firing_alerts", labels={"alertname": _unique()})
        assert nothing["alerts"] == [] and nothing["total"] == 0
        rule = await self.call(t, "get_alert_rule", rule_id=t.rule_id)
        assert rule["id"] == t.rule_id and rule["title"] and rule["queries"]
        await self.refused(t, "get_alert_rule", rule_id=_unique())
        assert (await self.call(t, "list_silences", state="expired"))["silences"] is not None

    async def test_metrics_and_logs_answer_the_profile(self, observability_target: Target) -> None:
        t = observability_target
        metrics = await self.call(
            t, "query_metrics", source=t.metrics_source, expr=t.metrics_expr, start=t.start, end=t.end
        )
        assert metrics["source"] == t.metrics_source and metrics["series"], "the metric expression answers nothing"
        series = metrics["series"][0]
        assert series["count"] >= 1 and series["points"] and series["last"] is not None
        assert series["min"] <= series["last"] <= series["max"]
        instant = await self.call(
            t, "query_metrics", source=t.metrics_source, expr=t.metrics_expr, start=t.start, end=t.end, instant=True
        )
        assert instant["instant"] is True
        logs = await self.call(t, "query_logs", source=t.logs_source, expr=t.logs_expr, start=t.start, end=t.end)
        assert logs["lines"] and all(line["line"] for line in logs["lines"])
        times = [line["time"] or "" for line in logs["lines"]]
        assert times == sorted(times, reverse=True), "newest first"
        window = {"source": t.logs_source, "expr": t.logs_expr, "start": t.start, "end": t.end}
        one = await self.call(t, "query_logs", **window, limit=1)
        assert len(one["lines"]) == 1

    async def test_dashboards_and_annotations_answer_the_profile(self, observability_target: Target) -> None:
        t = observability_target
        dashboards = (await self.call(t, "list_dashboards"))["dashboards"]
        assert dashboards
        opened = await self.call(t, "get_dashboard", id=dashboards[0]["id"])
        assert opened["id"] == dashboards[0]["id"] and opened["title"]
        await self.refused(t, "get_dashboard", id=_unique())
        marks = await self.call(t, "list_annotations", start=t.start, end=t.end)
        assert marks["total"] == len(marks["annotations"])

    # ---- bounds and the allow-list --------------------------------------------------------------

    async def test_a_source_outside_the_allow_list_is_refused_before_anything_is_sent(
        self, observability_target: Target
    ) -> None:
        t = observability_target
        for tool, expr in (("query_metrics", t.metrics_expr), ("query_logs", t.logs_expr)):
            before = t.upstream_calls()
            message = await self.refused(t, tool, source=t.foreign_source, expr=expr, start=t.start, end=t.end)
            assert "allow-list" in message, f"{tool}: the refusal must say 'allow-list': {message}"
            assert t.upstream_calls() == before, f"{tool}: the provider was called"

    async def test_a_source_of_the_wrong_kind_is_refused(self, observability_target: Target) -> None:
        t = observability_target
        await self.refused(t, "query_metrics", source=t.logs_source, expr=t.metrics_expr, start=t.start, end=t.end)
        await self.refused(t, "query_logs", source=t.metrics_source, expr=t.logs_expr, start=t.start, end=t.end)

    async def test_windows_are_bounded_by_the_server(self, observability_target: Target) -> None:
        t = observability_target
        end = datetime.fromisoformat(t.end.replace("Z", "+00:00"))
        wide = _stamp(end - timedelta(days=30))
        await self.refused(t, "query_metrics", source=t.metrics_source, expr=t.metrics_expr, start=wide, end=t.end)
        await self.refused(t, "query_logs", source=t.logs_source, expr=t.logs_expr, start=wide, end=t.end)
        await self.refused(t, "list_annotations", start=wide, end=t.end)
        await self.refused(t, "query_metrics", source=t.metrics_source, expr=t.metrics_expr, start=t.end, end=t.start)
        await self.refused(t, "query_logs", source=t.logs_source, expr="", start=t.start, end=t.end)

    # ---- silences -------------------------------------------------------------------------------

    async def test_create_silence_is_idempotent_for_the_same_action_key_and_compensation_undoes_it(
        self, observability_target: Target
    ) -> None:
        t = observability_target
        key = f"act-{uuid.uuid4().hex[:8]}"
        async with self.silenced(t, key) as made:
            assert made["created"] is True and made["matched_alerts"] >= 1
            assert made["matchers"] == t.alert_labels
            listed = {s["id"]: s for s in (await self.call(t, "list_silences", state="active"))["silences"]}
            assert listed[made["silence_id"]]["created_by"], "a silence says who made it"
            assert {(m["name"], m["value"]) for m in listed[made["silence_id"]]["matchers"]} == set(
                t.alert_labels.items()
            )
            assert not any(m.get("is_regex") for m in listed[made["silence_id"]]["matchers"])
            replay = await self.call(
                t,
                "create_silence",
                matchers=t.alert_labels,
                duration_minutes=5,
                comment="replay",
                action_key=key,
            )
            assert replay["created"] is False and replay["silence_id"] == made["silence_id"]
            hidden = await self.call(t, "list_firing_alerts", labels=t.alert_labels)
            assert hidden["alerts"] == [], "a silenced alert is not listed unless asked"
            shown = await self.call(t, "list_firing_alerts", labels=t.alert_labels, include_silenced=True)
            assert [a["silenced_by"] for a in shown["alerts"]] == [[made["silence_id"]]]

            undo = _operations(t)["create_silence"]["compensation"]
            assert undo["operation"] == "expire_silence"
            gone = await self.call(t, undo["operation"], **_render(undo["arguments"], made))
            assert gone["expired"] is True and gone["state"] == "expired"
            assert (await self.call(t, "list_firing_alerts", labels=t.alert_labels))["alerts"], "visible again"
            # Undoing twice is harmless.
            again = await self.call(t, undo["operation"], **_render(undo["arguments"], made))
            assert again["expired"] is False

    async def test_create_silence_refuses_what_would_hide_too_much_or_nothing(
        self, observability_target: Target
    ) -> None:
        t = observability_target
        name = t.alert_labels["alertname"]
        refused = (
            {"matchers": {"severity": t.alert_labels.get("severity", "x")}, "duration_minutes": 5},  # no alertname
            {"matchers": {"alertname": _unique()}, "duration_minutes": 5},  # matches no firing alert
            {"matchers": {"alertname": name + ".*"}, "duration_minutes": 5},  # a pattern is not a value
            {"matchers": t.alert_labels, "duration_minutes": 10**6},  # far past any cap
            {"matchers": t.alert_labels, "duration_minutes": 0},
            {"matchers": {}, "duration_minutes": 5},
        )
        for arguments in refused:
            await self.refused(t, "create_silence", comment="conformance", **arguments)
        await self.refused(t, "create_silence", matchers=t.alert_labels, duration_minutes=5, comment="  ")
        assert (await self.call(t, "list_firing_alerts", labels=t.alert_labels))["alerts"], "nothing was silenced"

    async def test_a_silence_this_server_did_not_create_is_not_expired(self, observability_target: Target) -> None:
        t = observability_target
        if t.foreign_silence_id is None:
            pytest.skip("this target has no silence made by a person")
        await self.refused(t, "expire_silence", silence_id=t.foreign_silence_id)
        await self.refused(t, "expire_silence", silence_id=_unique())

    # ---- annotations ----------------------------------------------------------------------------

    async def test_add_annotation_is_idempotent_for_the_same_action_key(self, observability_target: Target) -> None:
        t = observability_target
        tag, key = _unique(), f"act-{uuid.uuid4().hex[:8]}"
        args = {"text": f"conformance {tag}", "tags": [tag], "time": t.end, "action_key": key}
        first = await self.call(t, "add_annotation", **args)
        assert first["created"] is True and tag in first["tags"]
        replay = await self.call(t, "add_annotation", **args)
        assert replay["created"] is False and replay["id"] == first["id"]
        found = (await self.call(t, "list_annotations", start=t.start, end=t.end, tags=[tag]))["annotations"]
        assert [a["id"] for a in found] == [first["id"]]
        await self.refused(t, "add_annotation", text="   ")

    def test_the_profile_itself_is_well_formed(self) -> None:
        profile = load_profile()
        names = [t["name"] for t in profile["tools"]]
        assert len(names) == len(set(names)) == 13
        assert {t["name"] for t in profile["tools"] if t["access"] == "write"} == {
            "create_silence",
            "expire_silence",
            "add_annotation",
        }
        silence = next(t for t in profile["tools"] if t["name"] == "create_silence")
        assert silence["human_approval"] is True
