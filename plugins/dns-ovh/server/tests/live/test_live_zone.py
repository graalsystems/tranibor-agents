# SPDX-License-Identifier: Apache-2.0
"""The profile's conformance suite against a REAL OVHcloud account: opt-in, skipped without its variables.

    TRANIBOR_LIVE_DNS_OVH_ENDPOINT=ovh-eu \\
    TRANIBOR_LIVE_DNS_OVH_APPLICATION_KEY=... TRANIBOR_LIVE_DNS_OVH_APPLICATION_SECRET=... \\
    TRANIBOR_LIVE_DNS_OVH_CONSUMER_KEY=... TRANIBOR_LIVE_DNS_OVH_ZONE=<a test zone you own> \\
      uv run pytest tests/live -q

- Use a THROWAWAY zone and a consumer key restricted to it (see the procedure). The suite writes records named
  `conf-<run>` in that zone, publishes the zone, deletes them and publishes again; it touches no other record.
- `TRANIBOR_LIVE_REQUIRED=dns-ovh` turns a missing variable into a failure, as the core's live suite does.
- This suite has NEVER been run by its author (no account): treat its first run as the first real test of the
  client's signature, the record shapes and the endpoint behaviours the procedure lists as unconfirmed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from tranibor_dns_conformance import Conformance, McpTarget

from tranibor_dns_ovh.app import Runtime, build_server
from tranibor_dns_ovh.config import Settings

from ..conftest import DESCRIPTOR


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_DNS_OVH_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"dns-ovh", "all"})


class Counting(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        return await self.inner.handle_async_request(request)


@pytest.fixture
async def dns_target() -> AsyncIterator[McpTarget]:
    missing = [n for n in ("APPLICATION_KEY", "APPLICATION_SECRET", "CONSUMER_KEY", "ZONE") if not _env(n)]
    if missing:
        message = f"live OVHcloud test not configured: TRANIBOR_LIVE_DNS_OVH_{', '.join(missing)}"
        if _required():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    zone = _env("ZONE")
    settings = Settings.from_env(
        {
            "DNS_OVH_ENDPOINT": _env("ENDPOINT") or "ovh-eu",
            "DNS_OVH_APPLICATION_KEY": _env("APPLICATION_KEY"),
            "DNS_OVH_APPLICATION_SECRET": _env("APPLICATION_SECRET"),
            "DNS_OVH_CONSUMER_KEY": _env("CONSUMER_KEY"),
            "DNS_OVH_ZONES": zone,
        }
    )
    transport = Counting()
    runtime = Runtime(settings, transport)
    try:
        yield McpTarget(
            build_server(runtime),
            descriptor_path=DESCRIPTOR,
            zone=zone,
            foreign_zone="not-allowed.example.org",
            upstream_calls=lambda: transport.count,
            stages_changes=True,
        )
    finally:
        await runtime.client.aclose()


class TestLiveOvh(Conformance):
    """The same checks as tests/test_conformance.py, over the real API."""
