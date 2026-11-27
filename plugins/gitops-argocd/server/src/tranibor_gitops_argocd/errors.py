# SPDX-License-Identifier: Apache-2.0
"""The two ways a call fails: refused before reaching Argo CD, or refused by Argo CD."""

from __future__ import annotations


class ArgoCDError(Exception):
    """Argo CD answered with an error, or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Refused(ValueError):
    """The request breaks a rule of this server (project allow-list, an operation already running, a
    selective sync without a justification, a rollback while auto-sync is on…).

    Raised before any write reaches Argo CD: a refused request changes nothing."""
