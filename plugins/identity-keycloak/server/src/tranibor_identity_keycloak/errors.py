# SPDX-License-Identifier: Apache-2.0
"""A call fails with a typed error: its code first, then why. The code is what an agent acts on.

- `invalid_arguments`: refused before any call to Keycloak.
- `auth_failed`: Keycloak refused the client credentials (wrong id or secret, client disabled, no service
  account).
- `forbidden`: Keycloak refused the service account (a realm-management role it was not given).
- `not_found`, `conflict`, `rate_limited`, `upstream_error`: what Keycloak answered, or that it could not
  be reached.
"""

from __future__ import annotations

from typing import Literal

Code = Literal[
    "invalid_arguments",
    "auth_failed",
    "forbidden",
    "not_found",
    "conflict",
    "rate_limited",
    "upstream_error",
]


class Failure(Exception):
    """A typed failure. Its message never carries a secret, a token, nor a body Keycloak returned."""

    def __init__(self, code: Code, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code: Code = code
        self.status = status

    def __str__(self) -> str:
        return f"{self.code}: {self.args[0]}"


def invalid(message: str) -> Failure:
    return Failure("invalid_arguments", message)
