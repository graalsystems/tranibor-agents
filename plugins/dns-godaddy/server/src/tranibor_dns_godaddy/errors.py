# SPDX-License-Identifier: Apache-2.0
"""Two kinds of failure: the server refuses a call itself (`Refused`), or GoDaddy answers an error."""

from __future__ import annotations


class Refused(Exception):
    """Refused by the server before anything is sent: a zone outside the allow-list, a record that is
    not valid. The message never carries a credential."""


class GoDaddyError(Exception):
    """GoDaddy answered an error, or could not be reached (`status` is then 0)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
