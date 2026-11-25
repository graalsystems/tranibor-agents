# SPDX-License-Identifier: Apache-2.0
"""What the fake cannot prove: the real routes, bodies and frame layouts. Opt-in, see `conftest.py`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ..conftest import Mcp


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


async def test_alerts_silences_and_datasources_are_readable(live: Mcp) -> None:
    alerts = await live.call("list_firing_alerts")
    assert alerts["untrusted"] is True
    assert isinstance(alerts["alerts"], list)
    assert isinstance((await live.call("list_silences"))["silences"], list)
    assert (await live.call("list_sources"))["sources"]


async def test_the_rule_is_readable(live: Mcp, need: Callable[[str], str]) -> None:
    rule = await live.call("get_alert_rule", rule_id=need("RULE_UID"))
    assert rule["id"] == need("RULE_UID")
    assert rule["queries"]


async def test_a_metric_query_returns_a_frame(live: Mcp, need: Callable[[str], str]) -> None:
    end = _now()
    result = await live.call(
        "query_metrics",
        source=need("DATASOURCE_UID"),
        expr="up",
        start=_iso(end - timedelta(minutes=30)),
        end=_iso(end),
    )
    assert result["series"], "the frame layout the server reads was not found in the reply"


async def test_a_log_query_returns_lines(live: Mcp, need: Callable[[str], str]) -> None:
    end = _now()
    selector = need("LOKI_SELECTOR")  # e.g. {job=~".+"}
    result = await live.call(
        "query_logs",
        source=need("LOKI_UID"),
        expr=selector,
        start=_iso(end - timedelta(minutes=30)),
        end=_iso(end),
        limit=5,
    )
    assert result["untrusted"] is True


async def test_dashboards_and_annotations_are_readable(live: Mcp) -> None:
    dashboards = await live.call("list_dashboards", limit=5)
    assert isinstance(dashboards["dashboards"], list)
    end = _now()
    annotations = await live.call("list_annotations", start=_iso(end - timedelta(hours=2)), end=_iso(end))
    assert isinstance(annotations["annotations"], list)


async def test_a_silence_is_created_and_expired(live_writer: Mcp, need: Callable[[str], str]) -> None:
    created = await live_writer.call(
        "create_silence",
        matchers={"alertname": need("ALERTNAME")},
        duration_minutes=5,
        comment="tranibor live test",
        action_key="live-test-silence",
    )
    try:
        again = await live_writer.call(
            "create_silence",
            matchers={"alertname": need("ALERTNAME")},
            duration_minutes=5,
            comment="tranibor live test",
            action_key="live-test-silence",
        )
        assert again["silence_id"] == created["silence_id"], "the action key must make the create idempotent"
    finally:
        expired = await live_writer.call("expire_silence", silence_id=created["silence_id"])
    assert expired["expired"] is True


async def test_an_annotation_is_added_once(live_writer: Mcp) -> None:
    first = await live_writer.call(
        "add_annotation", text="tranibor live test", tags=["tranibor-live"], action_key="live-test-annotation"
    )
    second = await live_writer.call(
        "add_annotation", text="tranibor live test", tags=["tranibor-live"], action_key="live-test-annotation"
    )
    assert first["id"] == second["id"]
