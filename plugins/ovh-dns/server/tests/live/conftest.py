# SPDX-License-Identifier: Apache-2.0
"""The server against a REAL OVHcloud account: opt-in, skipped without its variables.

    TRANIBOR_LIVE_OVH_DNS_ENDPOINT=ovh-eu \\
    TRANIBOR_LIVE_OVH_DNS_APPLICATION_KEY=… TRANIBOR_LIVE_OVH_DNS_APPLICATION_SECRET=… \\
    TRANIBOR_LIVE_OVH_DNS_CONSUMER_KEY=… TRANIBOR_LIVE_OVH_DNS_ZONE=<a test zone you own> \\
      uv run pytest tests/live -q

- The consumer key must be restricted to the test zone (see the procedure); the run writes ONE record named
  `tranibor-live-<run>` in that zone, refreshes the zone, deletes the record and refreshes again. It touches
  no other record. Use a throwaway zone anyway.

`TRANIBOR_LIVE_REQUIRED=ovh-dns` turns a missing variable into a failure, as the core's live suite does.
This suite has NEVER been run by its author (no account): treat its first run as the first real test of the
client's signature, the record shapes and the endpoint behaviours the procedure lists as unconfirmed.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest

from tranibor_ovh_dns.app import Runtime, build_server
from tranibor_ovh_dns.config import Settings

from ..conftest import Mcp

RUN = uuid.uuid4().hex[:8]
NAME = f"tranibor-live-{RUN}"


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_OVH_DNS_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"ovh-dns", "all"})


@pytest.fixture(scope="session")
def zone() -> str:
    missing = [n for n in ("APPLICATION_KEY", "APPLICATION_SECRET", "CONSUMER_KEY", "ZONE") if not _env(n)]
    if missing:
        message = f"live OVHcloud test not configured: TRANIBOR_LIVE_OVH_DNS_{', '.join(missing)}"
        if _required():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    return _env("ZONE")


@pytest.fixture
async def live(zone: str) -> AsyncIterator[Mcp]:
    settings = Settings.from_env(
        {
            "OVH_DNS_ENDPOINT": _env("ENDPOINT") or "ovh-eu",
            "OVH_DNS_APPLICATION_KEY": _env("APPLICATION_KEY"),
            "OVH_DNS_APPLICATION_SECRET": _env("APPLICATION_SECRET"),
            "OVH_DNS_CONSUMER_KEY": _env("CONSUMER_KEY"),
            "OVH_DNS_ZONES": zone,
        }
    )
    runtime = Runtime(settings)
    try:
        yield Mcp(build_server(runtime))
    finally:
        await runtime.client.aclose()
