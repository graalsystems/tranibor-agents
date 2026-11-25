# SPDX-License-Identifier: Apache-2.0
"""A call fails with a typed error: its code first, then why. The code is what an agent acts on.

- `invalid_arguments`: refused before any call to UniFi (a malformed id, a value out of range, a port
  without PoE, a voucher request the API cannot honour).
- `not_configured`: the optional Site Manager (cloud) API is not configured on this connector.
- `unauthorized`: UniFi refused the API key (wrong, revoked, or not an Integration API key).
- `forbidden`: the key is valid but its rights do not cover the call.
- `not_found`, `rate_limited`, `upstream_error`: what UniFi answered, or that it could not be reached.
"""

from __future__ import annotations

from typing import Literal

Code = Literal[
    "invalid_arguments",
    "not_configured",
    "unauthorized",
    "forbidden",
    "not_found",
    "rate_limited",
    "upstream_error",
]


class Failure(Exception):
    """A typed failure. Its message never carries a key, nor a body UniFi returned."""

    def __init__(self, code: Code, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code: Code = code
        self.status = status

    def __str__(self) -> str:
        return f"{self.code}: {self.args[0]}"


def invalid(message: str) -> Failure:
    return Failure("invalid_arguments", message)
