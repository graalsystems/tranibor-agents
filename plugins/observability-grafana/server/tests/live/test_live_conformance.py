# SPDX-License-Identifier: Apache-2.0
"""The profile's conformance suite over a REAL Grafana: opt-in, skipped without its variables (see conftest.py).

It silences one firing alert for five minutes and expires the silence, and adds annotations tagged
`conf-<run>`, which stay (Grafana has no undo for them here): delete them by hand on a test stack.
Nobody has run it yet: it was written from the documentation, without a Grafana account.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tranibor_observability_conformance import Conformance, McpTarget

from tranibor_observability_grafana.app import Runtime, build_server
from tranibor_observability_grafana.config import Settings

from ..conftest import DESCRIPTOR
from .conftest import env, missing


class Counting(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        return await self.inner.handle_async_request(request)


@pytest.fixture
async def observability_target() -> AsyncIterator[McpTarget]:
    for name in (
        "URL",
        "WRITE_TOKEN",
        "ALERT_LABELS",
        "RULE_UID",
        "DATASOURCE_UID",
        "LOKI_UID",
        "LOKI_SELECTOR",
        "FOREIGN_UID",
    ):
        if not env(name):
            missing(f"TRANIBOR_LIVE_GRAFANA_{name}")
    labels = json.loads(env("ALERT_LABELS"))
    settings = Settings.from_env(
        {
            "GRAFANA_BASE_URL": env("URL"),
            "GRAFANA_TOKEN": env("WRITE_TOKEN"),
            "GRAFANA_DATASOURCES": f"{env('DATASOURCE_UID')},{env('LOKI_UID')}",
            "TRANIBOR_SERVER_TOKEN": "unused-in-process",
        }
    )
    transport = Counting()
    runtime = Runtime(settings, transport)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        yield McpTarget(
            build_server(runtime),
            descriptor_path=DESCRIPTOR,
            alert_labels=labels,
            rule_id=env("RULE_UID"),
            metrics_source=env("DATASOURCE_UID"),
            logs_source=env("LOKI_UID"),
            metrics_expr=env("METRICS_EXPR") or "up",
            logs_expr=env("LOKI_SELECTOR"),
            start=(now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            end=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            foreign_source=env("FOREIGN_UID"),
            upstream_calls=lambda: transport.count,
        )
    finally:
        await runtime.grafana.aclose()


class TestLiveGrafana(Conformance):
    """The same checks as tests/test_conformance.py, over the real API."""
