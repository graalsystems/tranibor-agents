# SPDX-License-Identifier: Apache-2.0
"""The profile's conformance suite against a REAL Keycloak: opt-in, skipped without its variables.

    TRANIBOR_LIVE_IDENTITY_KEYCLOAK_BASE_URL=https://keycloak.example.com \\
    TRANIBOR_LIVE_IDENTITY_KEYCLOAK_CLIENT_ID=tranibor-iam \\
    TRANIBOR_LIVE_IDENTITY_KEYCLOAK_CLIENT_SECRET=... \\
    TRANIBOR_LIVE_IDENTITY_KEYCLOAK_REALM=<a test realm you own> \\
      uv run pytest tests/live -q

- Use a THROWAWAY realm and a service account limited to the roles the procedure lists. The suite creates
  users named `conf-<run>` in that realm, adds them to a group, disables and removes them again, and touches
  no other user. It cannot delete what it creates (the profile has no delete): remove the `conf-*` users by hand.
- The realm needs at least one group. Set `TRANIBOR_LIVE_IDENTITY_KEYCLOAK_MAIL=1` when it has a mail server, so the
  setup-mail checks expect a send.
- `TRANIBOR_LIVE_IDENTITY_KEYCLOAK_TOKEN_REALM` (default: the realm) is where the service account lives.
- `TRANIBOR_LIVE_REQUIRED=identity-keycloak` turns a missing variable into a failure, as the core's live suite does.
- This suite has NEVER been run by its author (no Keycloak): treat its first run as the first real test of the
  client, the response shapes and the endpoint behaviours the procedure lists as unconfirmed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from tranibor_identity_conformance import Conformance, McpTarget

from tranibor_identity_keycloak.app import Runtime, build_server
from tranibor_identity_keycloak.config import Settings

from ..conftest import DESCRIPTOR


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_IDENTITY_KEYCLOAK_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"identity-keycloak", "all"})


class Counting(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        return await self.inner.handle_async_request(request)


@pytest.fixture
async def identity_target() -> AsyncIterator[McpTarget]:
    missing = [n for n in ("BASE_URL", "CLIENT_ID", "CLIENT_SECRET", "REALM") if not _env(n)]
    if missing:
        message = f"live Keycloak test not configured: TRANIBOR_LIVE_IDENTITY_KEYCLOAK_{', '.join(missing)}"
        if _required():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    realm = _env("REALM")
    settings = Settings.from_env(
        {
            "KEYCLOAK_BASE_URL": _env("BASE_URL"),
            "KEYCLOAK_CLIENT_ID": _env("CLIENT_ID"),
            "KEYCLOAK_CLIENT_SECRET": _env("CLIENT_SECRET"),
            "KEYCLOAK_REALMS": realm,
            "KEYCLOAK_TOKEN_REALM": _env("TOKEN_REALM") or realm,
        }
    )
    transport = Counting()
    runtime = Runtime(settings, transport)
    try:
        yield McpTarget(
            build_server(runtime),
            descriptor_path=DESCRIPTOR,
            directory=realm,
            foreign_directory="not-allowed-realm",
            upstream_calls=lambda: transport.count,
            mail_configured=bool(_env("MAIL")),
        )
    finally:
        await runtime.client.aclose()


class TestLiveKeycloak(Conformance):
    """The same checks as tests/test_conformance.py, over the real API."""
