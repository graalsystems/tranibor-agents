# SPDX-License-Identifier: Apache-2.0
"""The server against a REAL Grafana: opt-in, skipped without its variables.

    TRANIBOR_LIVE_GRAFANA_URL=https://grafana.example.com \\
    TRANIBOR_LIVE_GRAFANA_TOKEN=<service-account token, Viewer> \\
    TRANIBOR_LIVE_GRAFANA_WRITE_TOKEN=<service-account token, Editor: optional, enables the write tests> \\
      uv run pytest tests/live -q

- Use a test stack. The write tests create one silence (for a label no alert carries, so nothing real is
  muted... but the server refuses a matcher that selects no firing alert, so the silence test needs a
  firing alert: name it with `TRANIBOR_LIVE_GRAFANA_ALERTNAME`) and one annotation, tagged
  `tranibor-live`, and expire the silence at the end. The annotation stays: delete it by hand.
- `TRANIBOR_LIVE_GRAFANA_RULE_UID` is an alert rule's uid for the rule test, `..._DATASOURCE_UID` a
  Prometheus-type datasource's uid for the metric test, `..._LOKI_UID` a Loki one's.

`TRANIBOR_LIVE_REQUIRED=grafana` turns a missing variable into a failure, as the core's live suite does.
Nobody has run this suite yet: it was written from the documentation, without a Grafana account.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest

from tranibor_alert_triage.app import Runtime, build_server
from tranibor_alert_triage.config import Settings

from ..conftest import Mcp


def env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_GRAFANA_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"grafana", "all"})


def missing(what: str) -> None:
    if _required():
        pytest.fail(f"TRANIBOR_LIVE_REQUIRED is set but {what} is missing")
    pytest.skip(f"{what} is not set: the live Grafana tests are opt-in")


def _mcp(token: str) -> Mcp:
    settings = Settings.from_env(
        {"GRAFANA_BASE_URL": env("URL"), "GRAFANA_TOKEN": token, "TRANIBOR_SERVER_TOKEN": "unused-in-process"}
    )
    return Mcp(build_server(Runtime(settings)))


@pytest.fixture
def live() -> Mcp:
    if not env("URL") or not env("TOKEN"):
        missing("TRANIBOR_LIVE_GRAFANA_URL and TRANIBOR_LIVE_GRAFANA_TOKEN")
    return _mcp(env("TOKEN"))


@pytest.fixture
def live_writer() -> Mcp:
    if not env("URL") or not env("WRITE_TOKEN"):
        missing("TRANIBOR_LIVE_GRAFANA_URL and TRANIBOR_LIVE_GRAFANA_WRITE_TOKEN")
    return _mcp(env("WRITE_TOKEN"))


@pytest.fixture
def need() -> Callable[[str], str]:
    def value(name: str) -> str:
        found = env(name)
        if not found:
            missing(f"TRANIBOR_LIVE_GRAFANA_{name}")
        return found

    return value
