# SPDX-License-Identifier: Apache-2.0
"""The profile's conformance suite against REAL endpoints: opt-in, skipped without its variables.

    TRANIBOR_LIVE_CERTIFICATES_PROBE_HOSTS=host1.example.org,host2.example.org:8443 \\
      uv run pytest tests/live -q

- The hosts must be ones you operate: the suite only opens TLS connections to them and to nothing else.
- The suite's window and ordering checks need certificates both inside and outside a 30-day window among the
  hosts: with fewer, those checks fail on the data, not on the server. Set
  `TRANIBOR_LIVE_CERTIFICATES_PROBE_WINDOW_DAYS` to a window that splits them.
- `TRANIBOR_LIVE_REQUIRED=certificates-probe` turns a missing variable into a failure.
- This suite has NEVER been run by its author (the fake is all it saw): treat its first run as the first real
  test of the handshake, the chain reading and the trust check.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from tranibor_certificates_conformance import Conformance, McpTarget

from tranibor_certificates_probe.app import Runtime, build_server
from tranibor_certificates_probe.config import Settings
from tranibor_certificates_probe.probe import TlsProber

from ..conftest import DESCRIPTOR


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_CERTIFICATES_PROBE_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"certificates-probe", "all"})


class Counting(TlsProber):
    def __init__(self) -> None:
        self.count = 0

    async def probe(self, host: str, port: int) -> dict:  # type: ignore[type-arg]
        self.count += 1
        return await super().probe(host, port)


@pytest.fixture
def certificates_target() -> Iterator[McpTarget]:
    if not _env("HOSTS"):
        message = "live probe test not configured: TRANIBOR_LIVE_CERTIFICATES_PROBE_HOSTS"
        if _required():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    settings = Settings.from_env({"CERTIFICATES_PROBE_HOSTS": _env("HOSTS")})
    prober = Counting()
    first = settings.hosts[0]
    yield McpTarget(
        build_server(Runtime(settings, prober)),
        descriptor_path=DESCRIPTOR,
        foreign_id="not-configured.invalid:443",
        unknown_id=f"{first[0]}:{first[1]}",
        upstream_calls=lambda: prober.count,
        window_days=int(_env("WINDOW_DAYS") or 30),
    )


class TestLiveProbe(Conformance):
    """The same checks as tests/test_conformance.py, over real handshakes."""
