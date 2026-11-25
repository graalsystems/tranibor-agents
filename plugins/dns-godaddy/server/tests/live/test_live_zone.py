# SPDX-License-Identifier: Apache-2.0
"""The profile's conformance suite against a REAL GoDaddy account (OTE preferred): opt-in.

    TRANIBOR_LIVE_DNS_GODADDY_ENVIRONMENT=ote \\
    TRANIBOR_LIVE_DNS_GODADDY_TOKEN=... TRANIBOR_LIVE_DNS_GODADDY_ZONE=<a test zone you own> \\
      uv run pytest tests/live -q

- Use a THROWAWAY zone (a token cannot be restricted to it: the server's allow-list is). The suite writes records named
  `conf-<run>` in that zone and deletes them again; it touches no other record.
- `TRANIBOR_LIVE_REQUIRED=dns-godaddy` turns a missing variable into a failure, as the core's live suite does.
- This suite has NEVER been run by its author (no account): treat its first run as the first real test of the
  client's bearer auth, the record shapes and the endpoint behaviours the procedure lists as unconfirmed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from tranibor_dns_conformance import Conformance, McpTarget

from tranibor_dns_godaddy.app import Runtime, build_server
from tranibor_dns_godaddy.config import Settings

from ..conftest import DESCRIPTOR


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_DNS_GODADDY_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"dns-godaddy", "all"})


class Counting(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        return await self.inner.handle_async_request(request)


@pytest.fixture
async def dns_target() -> AsyncIterator[McpTarget]:
    missing = [n for n in ("TOKEN", "ZONE") if not _env(n)]
    if missing:
        message = f"live GoDaddy test not configured: TRANIBOR_LIVE_DNS_GODADDY_{', '.join(missing)}"
        if _required():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    zone = _env("ZONE")
    settings = Settings.from_env(
        {
            "DNS_GODADDY_ENVIRONMENT": _env("ENVIRONMENT") or "ote",
            "DNS_GODADDY_TOKEN": _env("TOKEN"),
            "DNS_GODADDY_ZONES": zone,
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
            stages_changes=False,
        )
    finally:
        await runtime.client.aclose()


class TestLiveGoDaddy(Conformance):
    """The same checks as tests/test_conformance.py, over the real API."""
