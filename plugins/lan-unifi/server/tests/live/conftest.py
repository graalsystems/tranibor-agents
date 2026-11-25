# SPDX-License-Identifier: Apache-2.0
"""The server against a REAL UniFi console: opt-in, skipped without its variables.

    TRANIBOR_LIVE_UNIFI_BASE_URL=https://unifi.example.com \\
    TRANIBOR_LIVE_UNIFI_API_KEY=... \\
    TRANIBOR_LIVE_UNIFI_CA_CERTIFICATE="$(cat console-ca.pem)"          # optional: a pinned CA \\
    TRANIBOR_LIVE_UNIFI_SITE_MANAGER_API_KEY=...                         # optional: the cloud tool \\
      uv run pytest tests/live -q

- The reads need a read-only API key. Nothing here writes: restarts, PoE power-cycles, guest
  authorisations and vouchers are exercised on the fake only, and a live write is a person's decision.

`TRANIBOR_LIVE_REQUIRED=unifi` turns a missing variable into a failure, as the core's live suite does.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tranibor_lan_unifi.app import Runtime
from tranibor_lan_unifi.config import Settings


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_UNIFI_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"unifi", "all"})


def missing(what: str) -> None:
    if _required():
        pytest.fail(f"live UniFi test required but not configured: {what}", pytrace=False)
    pytest.skip(f"live UniFi test skipped: {what}")


@pytest.fixture(scope="session")
def live() -> Iterator[Runtime]:
    if not (_env("BASE_URL") and _env("API_KEY")):
        missing("TRANIBOR_LIVE_UNIFI_BASE_URL and TRANIBOR_LIVE_UNIFI_API_KEY")
    env = {
        "UNIFI_BASE_URL": _env("BASE_URL"),
        "UNIFI_API_KEY": _env("API_KEY"),
        "UNIFI_CA_CERTIFICATE": _env("CA_CERTIFICATE"),
        "UNIFI_SITE_MANAGER_API_KEY": _env("SITE_MANAGER_API_KEY"),
        "UNIFI_SITE_MANAGER_URL": "https://api.ui.com" if _env("SITE_MANAGER_API_KEY") else "",
        "TRANIBOR_SERVER_TOKEN": "live-test-token",
    }
    yield Runtime(Settings.from_env({k: v for k, v in env.items() if v}))
