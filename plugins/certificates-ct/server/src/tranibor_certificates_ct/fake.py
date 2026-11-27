# SPDX-License-Identifier: Apache-2.0
"""A seeded, in-memory stand-in for crt.sh's JSON search, so tests and demos need no network.

Everything is relative to the clock given. All names are invented (example.com); nothing here is a real
certificate. The seeded entries (all logged for example.com):

  1001  shop.example.com, www.shop.example.com   issued 30 days ago, 60 left
  1002  api.shop.example.com                     9 days left
  1003  legacy.example.com                       4 days left
  1004  vpn.example.com, another authority       78 left
  1005  staging.example.com                      expired 310 days ago
  1006  shop.example.com                         10 days left, replaced by 1001 (which covers the same name)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx

from .config import normalise_domain

Clock = Callable[[], datetime]

CA = "C=XX, O=Example Public CA, CN=Example Public CA R1"
OTHER = "C=XX, O=Other Authority, CN=Other Authority DV CA"


def seed_ct(now: datetime) -> list[dict[str, Any]]:
    def entry(ct_id: int, names: list[str], issuer: str, days_ago: int, left: int) -> dict[str, Any]:
        issued = now - timedelta(days=days_ago)
        stamp = issued.strftime("%Y-%m-%dT%H:%M:%S")
        return {
            "issuer_ca_id": 1,
            "issuer_name": issuer,
            "common_name": names[0],
            "name_value": "\n".join(names),
            "id": ct_id,
            "entry_timestamp": stamp + ".123",
            "not_before": stamp,
            "not_after": (now + timedelta(days=left)).strftime("%Y-%m-%dT%H:%M:%S"),
            "serial_number": f"{ct_id:032x}",
            "result_count": 1,
        }

    return [
        entry(1001, ["shop.example.com", "www.shop.example.com"], CA, 30, 60),
        entry(1002, ["api.shop.example.com"], CA, 81, 9),
        entry(1003, ["legacy.example.com"], CA, 86, 4),
        entry(1004, ["vpn.example.com"], OTHER, 12, 78),
        entry(1005, ["staging.example.com"], CA, 400, -310),
        entry(1006, ["shop.example.com"], CA, 80, 10),
    ]


class FakeCt:
    """An `httpx` handler imitating crt.sh's `?q=...&output=json`; `queries` records what was asked."""

    def __init__(self, now: datetime | Callable[[], datetime] | None = None) -> None:
        self._now = now if callable(now) else (lambda: now or datetime.now(UTC))
        self.entries = seed_ct(self._now())
        self.queries: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.queries)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = unquote(request.url.params.get("q", ""))
        self.queries.append(query)
        if request.url.params.get("output") != "json":
            return httpx.Response(200, text="<html>not json</html>")
        wildcard = query.startswith("%.")
        domain = normalise_domain(query)
        hits = [
            e
            for e in self.entries
            if any(n == domain or (wildcard and n.endswith("." + domain)) for n in e["name_value"].splitlines())
        ]
        return httpx.Response(200, content=json.dumps(hits), headers={"content-type": "application/json"})
