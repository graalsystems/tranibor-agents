# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` and its sealed `secret_fields`
(`servers/argocd.yaml`). `ARGOCD_FAKE=1` replaces Argo CD with a seeded in-memory one, so tests and demos
need no account.

`TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094): the bearer token it presents on every call. The
server refuses to start without it, except on the fake with `TRANIBOR_SERVER_INSECURE_DEV=1` said
explicitly, for a local demo. The core names the pod's variable `TRANIBOR_SERVER_TOKEN` since its
`TRANIBOR_*` rename (core ADR 0176); an older core sets `CHOREGOS_SERVER_TOKEN`, still read when the new
name is absent (and `CHOREGOS_SERVER_INSECURE_DEV` likewise).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

#: What the fake Argo CD answers to (they protect nothing: the fake is in memory).
FAKE_BASE_URL = "https://argocd.fake.test"
FAKE_TOKEN = "fake-argocd-token"  # noqa: S105 - the fake's own credential, not a secret
FAKE_PROJECTS = ("shop", "billing")
#: The platform's token variable, the current core's name first, then an older core's.
TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    """The value of the first of `names` set to something, else empty."""
    return next((source[name] for name in names if source.get(name)), "")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str
    #: The bearer token of the dedicated local account (`accounts.<name>: apiKey`).
    token: SecretStr
    #: The AppProjects this server may touch. Never empty: an application of any other project is refused.
    projects: tuple[str, ...]
    #: PEM text of the CA that signed Argo CD's certificate, when it is a private one.
    ca_cert: str | None = None
    fake: bool = False
    port: int = 8080
    #: The bearer token the platform presents on every call but the health probe (ADR 0094).
    server_token: SecretStr | None = None
    #: `TRANIBOR_SERVER_INSECURE_DEV=1`: no token, accepted on the fake only.
    insecure_dev: bool = False

    @model_validator(mode="after")
    def _valid(self) -> Settings:
        if not self.base_url.startswith("https://") and not self.fake:
            raise ValueError("ARGOCD_BASE_URL must be an https:// URL")
        if not self.token.get_secret_value():
            raise ValueError("ARGOCD_TOKEN is not set")
        if not self.projects:
            raise ValueError("ARGOCD_PROJECTS names no AppProject: the server needs an allow-list")
        if any(p == "*" or not p.strip() for p in self.projects):
            raise ValueError("ARGOCD_PROJECTS lists AppProject names, never a wildcard")
        if self.ca_cert is not None and "BEGIN CERTIFICATE" not in self.ca_cert:
            raise ValueError("ARGOCD_CA_CERT must be PEM text (-----BEGIN CERTIFICATE-----)")
        return self

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses to "
            "start without the platform's token (only ARGOCD_FAKE=1 with TRANIBOR_SERVER_INSECURE_DEV=1 runs "
            "without it)"
        )

    @property
    def server_url(self) -> str:
        return self.base_url.rstrip("/")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        port = int(source.get("PORT", "8080"))
        token = _first(source, TOKEN_ENVS)
        server_token = SecretStr(token) if token else None
        insecure_dev = _first(source, INSECURE_DEV_ENVS) == "1"
        projects = tuple(p.strip() for p in source.get("ARGOCD_PROJECTS", "").split(",") if p.strip())
        if source.get("ARGOCD_FAKE", "") in ("1", "true", "yes"):
            return cls(
                base_url=FAKE_BASE_URL,
                token=SecretStr(FAKE_TOKEN),
                projects=projects or FAKE_PROJECTS,
                fake=True,
                port=port,
                server_token=server_token,
                insecure_dev=insecure_dev,
            )
        return cls(
            base_url=source.get("ARGOCD_BASE_URL", ""),
            token=SecretStr(source.get("ARGOCD_TOKEN", "")),
            projects=projects,
            ca_cert=source.get("ARGOCD_CA_CERT") or None,
            port=port,
            server_token=server_token,
            insecure_dev=insecure_dev,
        )
