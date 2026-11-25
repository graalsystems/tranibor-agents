# SPDX-License-Identifier: Apache-2.0
"""A seeded, in-memory stand-in for the TLS probe, so tests and demos need no network.

Everything is relative to the clock given, so a demo stays meaningful whatever the day. All names are
invented (example.com); nothing here is a real certificate. The seeded endpoints:

  shop.example.com:443        healthy, 60 days left
  api.shop.example.com:443    9 days left
  legacy.example.com:8443     4 days left, the server leaves out the intermediate: incomplete and not trusted
  old.example.com:443         expired 3 days ago
  wrong.example.com:443       valid 200 days, but for another name (a default backend answers)
  down.example.com:443        does not answer
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from .errors import SourceError
from .probe import hostname_matches

Clock = Callable[[], datetime]

#: host -> (days left, chain complete, trusted, trust error, names served)
SEED: dict[str, tuple[int, bool, bool, str | None, list[str] | None]] = {
    "shop.example.com": (60, True, True, None, None),
    "api.shop.example.com": (9, True, True, None, None),
    "legacy.example.com": (4, False, False, "unable to get local issuer certificate", None),
    "old.example.com": (-3, True, False, "certificate has expired", None),
    "wrong.example.com": (200, True, True, None, ["default.example.net"]),
}


class FakeProber:
    """Seeded probe results; `down.example.com` does not answer. `calls` counts the probes made."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self.calls = 0

    async def probe(self, host: str, port: int) -> dict[str, Any]:
        self.calls += 1
        now = self._clock()
        if host not in SEED:
            raise SourceError(f"{host}:{port}: no TLS handshake (TimeoutError)")
        left, complete, trusted, error, names = SEED[host]
        san = names or [host]
        return {
            "endpoint": f"{host}:{port}",
            "tls_version": "TLSv1.3",
            "subject": san[0],
            "issuer": "Example Public CA R1, Example Public CA",
            "serial": "0a1b2c3d",
            "names": san,
            "san_dns": san,
            "not_before": now + timedelta(days=left - 90),
            "not_after": now + timedelta(days=left),
            "self_signed": False,
            "sha256": "00" * 32,
            "chain_length": 2 if complete else 1,
            "chain_complete": complete,
            "hostname_matches": hostname_matches(san, host),
            "trusted": trusted,
            "trust_error": error,
        }
