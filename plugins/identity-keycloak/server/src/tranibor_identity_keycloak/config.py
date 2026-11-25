# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` and its sealed `secret_fields`
(`servers/identity-keycloak.yaml`). `KEYCLOAK_FAKE=1` replaces Keycloak with a seeded in-memory one, so tests
and demos need no account.

`TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094): the bearer token it presents on every call. The server
refuses to start without it, except on the fake with `TRANIBOR_SERVER_INSECURE_DEV=1` said explicitly, for a
local demo. The core names the pod's variable `TRANIBOR_SERVER_TOKEN` since its `TRANIBOR_*` rename (core ADR
0176, S75-04); an older core sets `CHOREGOS_SERVER_TOKEN`, still read when the new name is absent (and
`CHOREGOS_SERVER_INSECURE_DEV` likewise).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

#: What the fake Keycloak accepts (they protect nothing: the fake is in memory).
FAKE_BASE_URL = "https://keycloak.example.test"
FAKE_REALMS = ("demo",)
FAKE_CLIENT_ID = "tranibor-iam"
FAKE_CLIENT_SECRET = "fake-client-secret"  # noqa: S105 - the fake's own credential, not a secret
#: The platform's token variable, the current core's name first, then an older core's.
TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")

#: A realm name as Keycloak accepts it in a URL path: no slash, no dot-segment.
REALM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    """The value of the first of `names` set to something, else empty."""
    return next((source[name] for name in names if source.get(name)), "")


def _split(text: str) -> tuple[str, ...]:
    """A comma-joined list (how the platform passes an array), without blanks."""
    return tuple(part.strip() for part in text.split(",") if part.strip())


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str
    #: The allow-list: the realms (the profile's "directories") this server may touch, even to read.
    realms: tuple[str, ...]
    client_id: str
    client_secret: SecretStr
    #: The realm whose token endpoint issues the service account's token, when the client lives in another
    #: realm than the administered ones (typically the `master` realm). Empty: the first administered realm.
    token_realm: str = ""
    fake: bool = False
    port: int = 8080
    #: The name of this instance of the server (the connector's name), answered by `get_provider`.
    instance: str = "identity-keycloak"
    #: The bearer token the platform presents on every call but the health probe (ADR 0094).
    server_token: SecretStr | None = None
    #: `TRANIBOR_SERVER_INSECURE_DEV=1`: no token, accepted on the fake only.
    insecure_dev: bool = False

    @model_validator(mode="after")
    def _valid(self) -> Settings:
        if not self.base_url.startswith("https://") and not self.fake:
            raise ValueError("KEYCLOAK_BASE_URL must be an https:// URL")
        if not self.realms:
            raise ValueError("KEYCLOAK_REALMS is not set")
        for value in (*self.realms, self.token_realm):
            if value and not REALM.fullmatch(value):
                raise ValueError(f"{value!r} is not a realm name (KEYCLOAK_REALMS, KEYCLOAK_TOKEN_REALM)")
        if len(set(self.realms)) != len(self.realms):
            raise ValueError("KEYCLOAK_REALMS lists a realm twice")
        if not self.client_id or not self.client_secret.get_secret_value():
            raise ValueError("KEYCLOAK_CLIENT_ID and KEYCLOAK_CLIENT_SECRET are required")
        return self

    @property
    def root_url(self) -> str:
        """The server's URL without a trailing slash. Keycloak 17 and later (Quarkus) has no `/auth` prefix:
        a deployment that still serves one writes it in `base_url`."""
        return self.base_url.rstrip("/")

    @property
    def issuing_realm(self) -> str:
        return self.token_realm or self.realms[0]

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses to "
            "start without the platform's token (only KEYCLOAK_FAKE=1 with TRANIBOR_SERVER_INSECURE_DEV=1 runs "
            "without it)"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        token = _first(source, TOKEN_ENVS)
        common = {
            "port": int(source.get("PORT", "8080")),
            "instance": source.get("TRANIBOR_INSTANCE_NAME") or "identity-keycloak",
            "server_token": SecretStr(token) if token else None,
            "insecure_dev": _first(source, INSECURE_DEV_ENVS) == "1",
        }
        if source.get("KEYCLOAK_FAKE", "") in ("1", "true", "yes"):
            return cls(
                base_url=FAKE_BASE_URL,
                realms=FAKE_REALMS,
                client_id=FAKE_CLIENT_ID,
                client_secret=SecretStr(FAKE_CLIENT_SECRET),
                fake=True,
                **common,
            )
        return cls(
            base_url=source.get("KEYCLOAK_BASE_URL", ""),
            realms=_split(source.get("KEYCLOAK_REALMS", "")),
            client_id=source.get("KEYCLOAK_CLIENT_ID", ""),
            client_secret=SecretStr(source.get("KEYCLOAK_CLIENT_SECRET", "")),
            token_realm=source.get("KEYCLOAK_TOKEN_REALM", ""),
            **common,
        )
