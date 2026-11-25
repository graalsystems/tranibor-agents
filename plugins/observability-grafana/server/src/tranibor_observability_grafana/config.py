# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up. `TRANIBOR_INSTANCE_NAME` names the instance.

`GRAFANA_BASE_URL` and `GRAFANA_TOKEN` (a service-account token, Viewer role) reach Grafana. The caps are
enforced by the server whatever an agent asks: `GRAFANA_MAX_RANGE_HOURS` (the widest metric or log window),
`GRAFANA_MAX_LINES` (the most log lines returned) and `GRAFANA_MAX_SILENCE_MINUTES` (the longest silence).
`GRAFANA_DATASOURCES` is an allow-list of datasource uids, joined with commas; empty means every Prometheus
or Loki datasource the token can see. `GRAFANA_ORG_ID` selects the organization (`X-Grafana-Org-Id`); empty
means the token's own organization (a service account belongs to one).

`TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094): the bearer token it presents on every call. The server
refuses to start without it, except on the fake with `TRANIBOR_SERVER_INSECURE_DEV=1` said explicitly.
`OBSERVABILITY_GRAFANA_FAKE=1` replaces Grafana with a seeded in-memory stack, so tests and demos need no account.
An older core names the pod's variable `CHOREGOS_SERVER_TOKEN` (and `CHOREGOS_SERVER_INSECURE_DEV`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr

TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")

#: Hard ceilings: an administrator can lower the defaults, never exceed these.
CEILING_RANGE_HOURS = 24
CEILING_LINES = 1000
CEILING_SILENCE_MINUTES = 1440
DEFAULT_RANGE_HOURS = 6
DEFAULT_LINES = 200
DEFAULT_SILENCE_MINUTES = 120
FAKE_URL = "https://grafana.example.com"
#: Set by whoever installs the server (or the platform, when it does): how this instance names itself.
INSTANCE_ENV = "TRANIBOR_INSTANCE_NAME"


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    return next((source[name] for name in names if source.get(name)), "")


def _bounded(source: Mapping[str, str], name: str, default: int, ceiling: int) -> int:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as bad:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from bad
    if not 1 <= value <= ceiling:
        raise ValueError(f"{name} must be between 1 and {ceiling}, got {value}")
    return value


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    fake: bool = False
    #: The name of this instance of the server: its connector's name in the platform (`get_provider` says it).
    instance: str = "observability-grafana"
    port: int = 8080
    base_url: str = ""
    token: SecretStr | None = None
    org_id: int | None = None
    datasources: tuple[str, ...] = ()
    max_range_hours: int = DEFAULT_RANGE_HOURS
    max_lines: int = DEFAULT_LINES
    max_silence_minutes: int = DEFAULT_SILENCE_MINUTES
    server_token: SecretStr | None = None
    insecure_dev: bool = False

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses "
            "to start without the platform's token (only OBSERVABILITY_GRAFANA_FAKE=1 with "
            "TRANIBOR_SERVER_INSECURE_DEV=1 runs without it)"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        fake = source.get("OBSERVABILITY_GRAFANA_FAKE", "") in ("1", "true", "yes")
        base_url = source.get("GRAFANA_BASE_URL", "").strip().rstrip("/")
        token = source.get("GRAFANA_TOKEN", "")
        if fake:
            base_url = base_url or FAKE_URL
        else:
            if not base_url.startswith("https://"):
                raise ValueError("GRAFANA_BASE_URL is not set to an https:// URL")
            if not token:
                raise ValueError("GRAFANA_TOKEN is not set (a service-account token with the Viewer role)")
        org = source.get("GRAFANA_ORG_ID", "").strip()
        server_token = _first(source, TOKEN_ENVS)
        uids = tuple(u.strip() for u in source.get("GRAFANA_DATASOURCES", "").split(",") if u.strip())
        return cls(
            fake=fake,
            instance=source.get(INSTANCE_ENV, "") or "observability-grafana",
            port=int(source.get("PORT", "8080")),
            base_url=base_url,
            token=SecretStr(token) if token else None,
            org_id=int(org) if org else None,
            datasources=uids,
            max_range_hours=_bounded(source, "GRAFANA_MAX_RANGE_HOURS", DEFAULT_RANGE_HOURS, CEILING_RANGE_HOURS),
            max_lines=_bounded(source, "GRAFANA_MAX_LINES", DEFAULT_LINES, CEILING_LINES),
            max_silence_minutes=_bounded(
                source, "GRAFANA_MAX_SILENCE_MINUTES", DEFAULT_SILENCE_MINUTES, CEILING_SILENCE_MINUTES
            ),
            server_token=SecretStr(server_token) if server_token else None,
            insecure_dev=_first(source, INSECURE_DEV_ENVS) == "1",
        )
