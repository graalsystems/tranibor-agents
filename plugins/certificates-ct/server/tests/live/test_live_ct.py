# SPDX-License-Identifier: Apache-2.0
"""The profile's conformance suite against the REAL Certificate Transparency search: opt-in, skipped without its
variables.

    TRANIBOR_LIVE_CERTIFICATES_CT_DOMAINS=example.org uv run pytest tests/live -q

- The domains must be ones whose certificates you may look up (the log is public); crt.sh is rate limited and
  slow on a large domain, so a 5xx or a timeout fails the run on the source, not on the server.
- The suite's window and ordering checks need logged certificates both inside and outside a 30-day window:
  set `TRANIBOR_LIVE_CERTIFICATES_CT_WINDOW_DAYS` to a window that splits them.
- `TRANIBOR_LIVE_REQUIRED=certificates-ct` turns a missing variable into a failure.
- This suite has NEVER been run by its author (the fake is all it saw), and crt.sh's JSON output is not
  officially documented: treat its first run as the first real test of the client.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest
from tranibor_certificates_conformance import Conformance, McpTarget

from tranibor_certificates_ct.app import Runtime, build_server
from tranibor_certificates_ct.config import Settings

from ..conftest import DESCRIPTOR


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_CERTIFICATES_CT_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"certificates-ct", "all"})


class Counting(httpx.AsyncHTTPTransport):
    count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        return await super().handle_async_request(request)


@pytest.fixture
def certificates_target() -> Iterator[McpTarget]:
    if not _env("DOMAINS"):
        message = "live CT test not configured: TRANIBOR_LIVE_CERTIFICATES_CT_DOMAINS"
        if _required():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    settings = Settings.from_env({"CERTIFICATES_CT_DOMAINS": _env("DOMAINS")})
    transport = Counting()
    yield McpTarget(
        build_server(Runtime(settings, transport)),
        descriptor_path=DESCRIPTOR,
        foreign_id="not-configured.invalid:443",
        unknown_id="ct-1",
        upstream_calls=lambda: transport.count,
        window_days=int(_env("WINDOW_DAYS") or 30),
    )


class TestLiveCt(Conformance):
    """The same checks as tests/test_conformance.py, over the real log."""
