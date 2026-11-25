# SPDX-License-Identifier: Apache-2.0
"""Every tool through the MCP protocol, on the seeded fake: results, bounds, refusals."""

from __future__ import annotations

from collections.abc import Callable

from tranibor_observability_grafana.fake import INJECTION, FakeGrafana

from .conftest import NOW, Mcp

START = "2026-11-25T08:30:00Z"
END = "2026-11-25T09:00:00Z"
CHECKOUT = {"alertname": "HighErrorRate", "service": "checkout", "severity": "critical", "env": "production"}


async def test_firing_alerts_are_newest_first_and_hide_the_silenced(mcp: Mcp) -> None:
    out = await mcp.call("list_firing_alerts")
    assert [a["labels"]["alertname"] for a in out["alerts"]] == ["FlappingCPU", "HighErrorRate", "DiskSpaceLow"]
    assert out["untrusted"] is True and "never instructions" in out["note"]
    everything = await mcp.call("list_firing_alerts", include_silenced=True)
    assert out["total"] == 3 and everything["total"] == 4


async def test_firing_alerts_filter_by_label_and_by_start(mcp: Mcp) -> None:
    out = await mcp.call("list_firing_alerts", labels={"service": "checkout"})
    assert [a["labels"]["alertname"] for a in out["alerts"]] == ["HighErrorRate"]
    recent = await mcp.call("list_firing_alerts", since="2026-11-25T08:45:00Z")
    assert [a["labels"]["alertname"] for a in recent["alerts"]] == ["FlappingCPU"]
    assert "not a valid label name" in await mcp.error("list_firing_alerts", labels={"a b": "x"})
    assert "ISO 8601" in await mcp.error("list_firing_alerts", since="yesterday")


async def test_alert_rule(mcp: Mcp) -> None:
    rule = await mcp.call("get_alert_rule", rule_id="rule-high-error-rate")
    assert rule["title"] == "HighErrorRate" and rule["for_duration"] == "5m"
    assert rule["queries"][0]["source"] == "prom-main"
    assert "http_errors_total" in rule["queries"][0]["expr"]
    assert "not_found" in await mcp.error("get_alert_rule", rule_id="nope")
    assert "invalid_arguments" in await mcp.error("get_alert_rule", rule_id="../x")


async def test_silences_are_listed_with_their_author(mcp: Mcp) -> None:
    active = await mcp.call("list_silences", state="active")
    assert [s["id"] for s in active["silences"]] == ["sil-human"]
    assert active["silences"][0]["created_by"] == "oncall-human"
    assert (await mcp.call("list_silences"))["total"] == 2
    assert "state must be" in await mcp.error("list_silences", state="gone")


async def test_sources_exclude_what_the_tools_never_serve(mcp: Mcp, mcp_for: Callable[..., Mcp]) -> None:
    everything = mcp_for(GRAFANA_DATASOURCES="")
    sources = (await everything.call("list_sources"))["sources"]
    assert {s["id"]: s["kind"] for s in sources} == {
        "prom-main": "metrics",
        "prom-staging": "metrics",
        "loki-main": "logs",
    }
    only = mcp_for(GRAFANA_DATASOURCES="prom-main, loki-main")
    assert {s["id"] for s in (await only.call("list_sources"))["sources"]} == {"prom-main", "loki-main"}
    # the conftest's allow-list is the default
    assert {s["id"] for s in (await mcp.call("list_sources"))["sources"]} == {"prom-main", "loki-main"}


async def test_get_provider_names_the_instance_and_its_sources(mcp: Mcp, mcp_for: Callable[..., Mcp]) -> None:
    out = await mcp.call("get_provider")
    assert out["instance"] == "observability-grafana" and out["provider"] == "grafana"
    assert out["sources"] == ["prom-main", "loki-main"]
    named = mcp_for(TRANIBOR_INSTANCE_NAME="grafana-prod", GRAFANA_DATASOURCES="")
    answer = await named.call("get_provider")
    assert answer["instance"] == "grafana-prod" and answer["sources"] == ["prom-main", "prom-staging", "loki-main"]


async def test_an_alert_says_which_rule_fired_it(mcp: Mcp, fake: FakeGrafana) -> None:
    out = await mcp.call("list_firing_alerts")
    assert {a["labels"]["alertname"]: a["rule_id"] for a in out["alerts"]}["HighErrorRate"] == "rule-high-error-rate"
    # Grafana's own label wins; the generator URL is the last resort.
    fake.alerts[0]["labels"]["__alert_rule_uid__"] = "from-label"
    del fake.alerts[1]["annotations"]["rule_uid"]
    by_name = {a["labels"]["alertname"]: a["rule_id"] for a in (await mcp.call("list_firing_alerts"))["alerts"]}
    assert by_name["HighErrorRate"] == "from-label" and by_name["DiskSpaceLow"] == "rule-disk-space-low"


async def test_metrics_range_is_summarised(mcp: Mcp) -> None:
    out = await mcp.call(
        "query_metrics", source="prom-main", expr="sum(rate(http_errors_total[5m]))", start=START, end=END
    )
    series = out["series"][0]
    assert series["labels"] == {"service": "checkout"} and series["count"] == 31
    assert series["last"] > series["min"] and out["step_seconds"] == 60


async def test_metrics_step_is_raised_to_bound_the_points(mcp: Mcp) -> None:
    out = await mcp.call(
        "query_metrics",
        source="prom-main",
        expr="up",
        start="2026-11-25T03:00:00Z",
        end=END,
        step_seconds=1,
    )
    assert out["step_seconds"] == 44  # 6 h / 500 points, rounded up
    assert out["series"][0]["count"] <= 500


async def test_metrics_instant(mcp: Mcp) -> None:
    out = await mcp.call("query_metrics", source="prom-main", expr="up", start=START, end=END, instant=True)
    assert out["series"][0]["count"] == 1


async def test_metrics_refusals(mcp: Mcp, mcp_for: Callable[..., Mcp]) -> None:
    base = {"expr": "up", "start": START, "end": END}
    assert "capped at 6 hours" in await mcp.error(
        "query_metrics", source="prom-main", expr="up", start="2026-11-25T01:00:00Z", end=END
    )
    assert "end must be after" in await mcp.error("query_metrics", source="prom-main", **{**base, "end": START})
    assert "Loki" not in await mcp.error("query_metrics", source="loki-main", **base)
    assert "prometheus only" in await mcp.error("query_metrics", source="loki-main", **base)
    assert "not_found" in await mcp_for(GRAFANA_DATASOURCES="").error("query_metrics", source="nope", **base)
    assert "not_allowed" in await mcp_for(GRAFANA_DATASOURCES="loki-main").error(
        "query_metrics", source="prom-main", **base
    )
    assert "refused the query" in await mcp.error(
        "query_metrics", source="prom-main", **{**base, "expr": "sum(rate(x[5m])"}
    )
    narrow = mcp_for(GRAFANA_MAX_RANGE_HOURS="1")
    assert "capped at 1 hours" in await narrow.error(
        "query_metrics", source="prom-main", expr="up", start="2026-11-25T07:00:00Z", end=END
    )


async def test_logs_are_bounded_and_marked_as_data(mcp: Mcp, mcp_for: Callable[..., Mcp]) -> None:
    out = await mcp.call(
        "query_logs", source="loki-main", expr='{service="checkout"} |= "error"', start=START, end=END, limit=10
    )
    assert len(out["lines"]) == 10 and out["limit"] == 10 and out["untrusted"] is True
    assert any(line["line"] == INJECTION for line in out["lines"])  # handed over as data, not acted on
    assert out["lines"][0]["labels"]["service"] == "checkout"
    capped = mcp_for(GRAFANA_MAX_LINES="3")
    small = await capped.call("query_logs", source="loki-main", expr='{a="b"}', start=START, end=END, limit=900)
    assert small["limit"] == 3 and len(small["lines"]) == 3
    assert "loki only" in await mcp.error("query_logs", source="prom-main", expr="{}", start=START, end=END)


async def test_dashboards_and_panels_flatten_collapsed_rows(mcp: Mcp) -> None:
    found = await mcp.call("list_dashboards", query="check")
    assert [d["id"] for d in found["dashboards"]] == ["checkout-overview"]
    assert (await mcp.call("list_dashboards", query="zzz"))["dashboards"] == []
    panels = await mcp.call("get_dashboard", id="checkout-overview")
    assert [p["title"] for p in panels["panels"]] == ["Error ratio", "Checkout errors"]
    assert panels["panels"][1]["queries"][0]["source"] == "loki-main"
    assert "not_found" in await mcp.error("get_dashboard", id="nope")


async def test_annotations_find_the_deploy_marker(mcp: Mcp) -> None:
    out = await mcp.call("list_annotations", start="2026-11-25T08:00:00Z", end=END, tags=["deploy"])
    assert [a["text"] for a in out["annotations"]] == ["Deployed checkout v1.42.0"]
    assert out["annotations"][0]["time"] == "2026-11-25T08:35:00Z"
    assert "at most 7 days" in await mcp.error("list_annotations", start="2026-11-01T00:00:00Z", end=END)


async def test_create_silence_is_exact_bounded_and_undone_by_expire(mcp: Mcp, fake: FakeGrafana) -> None:
    made = await mcp.call(
        "create_silence", matchers=CHECKOUT, duration_minutes=30, comment="known deploy regression", action_key="k1"
    )
    assert made["created"] is True and made["matched_alerts"] == 1
    assert made["ends_at"] == "2026-11-25T09:30:00Z"
    body = fake.silences[made["silence_id"]]
    assert body["createdBy"] == "tranibor-observability-grafana" and "[k1]" in body["comment"]
    assert all(m["isRegex"] is False and m["isEqual"] is True for m in body["matchers"])
    # the silenced alert drops out of the firing list
    assert [a["labels"]["alertname"] for a in (await mcp.call("list_firing_alerts"))["alerts"]][-1] != "HighErrorRate"
    gone = await mcp.call("expire_silence", silence_id=made["silence_id"])
    assert gone == {"silence_id": made["silence_id"], "expired": True, "state": "expired"}
    again = await mcp.call("expire_silence", silence_id=made["silence_id"])
    assert again["expired"] is False


async def test_create_silence_is_idempotent_on_the_action_key(mcp: Mcp, fake: FakeGrafana) -> None:
    first = await mcp.call("create_silence", matchers=CHECKOUT, duration_minutes=30, comment="c", action_key="same")
    second = await mcp.call("create_silence", matchers=CHECKOUT, duration_minutes=30, comment="c", action_key="same")
    assert second["created"] is False and second["silence_id"] == first["silence_id"]
    assert sum(1 for s in fake.silences.values() if s["createdBy"] == "tranibor-observability-grafana") == 1


async def test_create_silence_refusals(mcp: Mcp, mcp_for: Callable[..., Mcp], fake: FakeGrafana) -> None:
    ok = {"matchers": CHECKOUT, "duration_minutes": 30, "comment": "why"}
    assert "must include alertname" in await mcp.error("create_silence", **{**ok, "matchers": {"service": "checkout"}})
    assert "no firing alert carries exactly" in await mcp.error(
        "create_silence", **{**ok, "matchers": {**CHECKOUT, "service": "check.*"}}
    )
    assert "no firing alert" in await mcp.error("create_silence", **{**ok, "matchers": {"alertname": "Nope"}})
    assert "between 1 and 120" in await mcp.error("create_silence", **{**ok, "duration_minutes": 121})
    assert "between 1 and 120" in await mcp.error("create_silence", **{**ok, "duration_minutes": 0})
    assert "comment is required" in await mcp.error("create_silence", **{**ok, "comment": "  "})
    assert "action_key" in await mcp.error("create_silence", **ok, action_key="a b")
    long = mcp_for(GRAFANA_MAX_SILENCE_MINUTES="1440")
    assert (await long.call("create_silence", **{**ok, "duration_minutes": 1440}))["created"] is True
    assert "between 1 and 1440" in await long.error("create_silence", **{**ok, "duration_minutes": 1441})
    assert not any(s["createdBy"] == "tranibor-observability-grafana" for s in fake.silences.values())


async def test_a_wide_silence_is_refused(mcp: Mcp, fake: FakeGrafana) -> None:
    fake.alerts = [
        fake._alert(f"fp-{i}", {"alertname": "Wide", "pod": f"p{i}"}, {"rule_uid": "r"}, NOW) for i in range(30)
    ]
    assert "would silence 30 alerts" in await mcp.error(
        "create_silence", matchers={"alertname": "Wide"}, duration_minutes=10, comment="c"
    )


async def test_only_this_plugins_silences_can_be_expired(mcp: Mcp, fake: FakeGrafana) -> None:
    assert "not_allowed" in await mcp.error("expire_silence", silence_id="sil-human")
    assert fake.silences["sil-human"]["status"]["state"] == "active"
    assert "not_found" in await mcp.error("expire_silence", silence_id="nope")


async def test_add_annotation_is_idempotent_and_tagged(mcp: Mcp, fake: FakeGrafana) -> None:
    first = await mcp.call("add_annotation", text="Incident: checkout 5xx", tags=["incident"], action_key="a1")
    assert first["created"] is True and first["tags"] == ["tranibor", "incident", "action:a1"]
    again = await mcp.call("add_annotation", text="Incident: checkout 5xx", tags=["incident"], action_key="a1")
    assert again["created"] is False and again["id"] == first["id"]
    assert sum(1 for a in fake.annotations if "action:a1" in a["tags"]) == 1
    region = await mcp.call(
        "add_annotation", text="window", time="2026-11-25T08:40:00Z", time_end="2026-11-25T08:50:00Z"
    )
    assert fake.annotations[-1]["timeEnd"] - fake.annotations[-1]["time"] == 600_000 and region["created"] is True
    assert "time_end must not be before" in await mcp.error(
        "add_annotation", text="x", time="2026-11-25T08:50:00Z", time_end="2026-11-25T08:40:00Z"
    )
    assert "at most 5 tags" in await mcp.error("add_annotation", text="x", tags=["a", "b", "c", "d", "e", "f"])


async def test_a_viewer_token_is_refused_on_writes_and_allowed_on_reads(mcp_for: Callable[..., Mcp]) -> None:
    viewer = mcp_for(GRAFANA_TOKEN="fake-viewer", GRAFANA_BASE_URL="https://grafana.example.com")
    assert (await viewer.call("list_firing_alerts"))["total"] == 3
    assert "forbidden" in await viewer.error("add_annotation", text="x")
    assert "forbidden" in await viewer.error("create_silence", matchers=CHECKOUT, duration_minutes=5, comment="c")
    expired = mcp_for(GRAFANA_TOKEN="fake-expired", GRAFANA_BASE_URL="https://grafana.example.com")
    assert "unauthorized" in await expired.error("list_firing_alerts")


async def test_a_workflow_action_may_send_its_arguments_as_text(mcp: Mcp) -> None:
    """A workflow renders its params to text: matchers as a JSON object, minutes as digits."""
    made = await mcp.call(
        "create_silence",
        matchers='{"alertname": "HighErrorRate", "service": "checkout"}',
        duration_minutes="30",
        comment="c",
    )
    assert made["created"] is True
    ok = {"duration_minutes": "30", "comment": "c"}
    assert "JSON text of one" in await mcp.error("create_silence", matchers="not json", **ok)
    assert "validation error" in await mcp.error("create_silence", matchers='["alertname"]', **ok)
    assert "whole number" in await mcp.error(
        "create_silence", matchers={"alertname": "HighErrorRate"}, duration_minutes="soon", comment="c"
    )
