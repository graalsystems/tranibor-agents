# SPDX-License-Identifier: Apache-2.0
"""Live suite: runs only against a real realm, when its credentials are in the environment.

TRANIBOR_LIVE_KEYCLOAK_BASE_URL, _REALM, _CLIENT_ID, _CLIENT_SECRET   (and optionally _TOKEN_REALM)
TRANIBOR_LIVE_KEYCLOAK_WRITE=1          also exercise the writes, on a user the suite creates itself
TRANIBOR_LIVE_REQUIRED=keycloak         a missing variable fails instead of skipping
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator

import pytest

from tranibor_keycloak_admin.client import KeycloakClient
from tranibor_keycloak_admin.config import Settings
from tranibor_keycloak_admin.tools import Tools

PREFIX = "TRANIBOR_LIVE_KEYCLOAK_"
FIELDS = ("BASE_URL", "REALM", "CLIENT_ID", "CLIENT_SECRET")


def _settings() -> Settings | None:
    values = {f: os.environ.get(PREFIX + f, "") for f in FIELDS}
    if not all(values.values()):
        if "keycloak" in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(","):
            pytest.fail("live Keycloak variables are required but not all set")
        return None
    env = {f"KEYCLOAK_{f}": v for f, v in values.items()}
    env["TRANIBOR_SERVER_TOKEN"] = "unused-in-live-tests"
    token_realm = os.environ.get(PREFIX + "TOKEN_REALM", "")
    if token_realm:
        env["KEYCLOAK_TOKEN_REALM"] = token_realm
    return Settings.from_env(env)


@pytest.fixture
async def live() -> AsyncIterator[Tools]:
    settings = _settings()
    if settings is None:
        pytest.skip("no live Keycloak credentials in the environment")
    yield Tools(KeycloakClient(settings))


@pytest.fixture
def write_enabled() -> None:
    if os.environ.get(PREFIX + "WRITE") != "1":
        pytest.skip("set TRANIBOR_LIVE_KEYCLOAK_WRITE=1 to exercise the writes")


@pytest.fixture
def run_tag() -> str:
    return f"tranibor-live-{secrets.token_hex(4)}"
