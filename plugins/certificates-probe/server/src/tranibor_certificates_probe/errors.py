# SPDX-License-Identifier: Apache-2.0
"""The two ways a call fails: refused before any request leaves, or a source that cannot answer."""

from __future__ import annotations


class SourceError(Exception):
    """A source (the system read, or a probed host) answered with an error or not at all."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Refused(ValueError):
    """The request breaks a rule of this server (a host, a namespace or a domain nobody configured...).

    Raised before anything is sent: a refused request never leaves the server."""
