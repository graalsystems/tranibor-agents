# SPDX-License-Identifier: Apache-2.0
"""A call fails with a typed error: its code first, then why. The code is what an agent acts on.

- `invalid_arguments`: refused by the server's own bounds before anything is sent to Grafana.
- `not_allowed`: the datasource is outside the administrator's allow-list.
- `unauthorized`, `forbidden`: Grafana refused the service-account token (missing, expired, or a role too low).
- `not_found`, `conflict`, `rate_limited`, `upstream_error`: what Grafana answered, or that it was unreachable.
"""

from __future__ import annotations

from typing import Literal

Code = Literal[
    "invalid_arguments",
    "not_allowed",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "upstream_error",
]


class Failure(Exception):
    """A typed failure. Its message never carries the token, nor a body Grafana returned."""

    def __init__(self, code: Code, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code: Code = code
        self.status = status

    def __str__(self) -> str:
        return f"{self.code}: {self.args[0]}"


def invalid(message: str) -> Failure:
    return Failure("invalid_arguments", message)
