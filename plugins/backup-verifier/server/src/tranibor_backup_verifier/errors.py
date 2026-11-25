# SPDX-License-Identifier: Apache-2.0
"""A call fails with a typed error: its code first, then why. The code is what an agent acts on.

- `not_configured`: the connector lacks what it needs (API URL, token, prefix) or what it has is unusable.
- `invalid_arguments`: refused before any call to the cluster.
- `refused`: a safety rule of the server (a namespace outside the restore prefix, a backup that cannot be
  restored); no configuration lowers it.
- `forbidden` / `unauthorized`: the Kubernetes API (or the bucket) refused the service account.
- `not_found`, `conflict`, `rate_limited`, `upstream_error`: what the API answered, or that it was unreachable.
"""

from __future__ import annotations

from typing import Literal

Code = Literal[
    "not_configured",
    "invalid_arguments",
    "refused",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "upstream_error",
]


class Failure(Exception):
    """A typed failure. Its message never carries a token nor a raw response body."""

    def __init__(self, code: Code, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code: Code = code
        self.status = status

    def __str__(self) -> str:
        return f"{self.code}: {self.args[0]}"


def invalid(message: str) -> Failure:
    return Failure("invalid_arguments", message)


def refused(message: str) -> Failure:
    return Failure("refused", message)
