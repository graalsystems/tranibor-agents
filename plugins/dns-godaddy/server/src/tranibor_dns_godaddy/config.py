# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

`DNS_GODADDY_ZONES` is the allow-list: the server touches no other zone, not even to read it. The credential
is one personal access token (PAT) of GoDaddy's developer dashboard, sent as `Authorization: Bearer`; its
scopes (`domains.domain:read`, `domains.dns:update`) are the first lock, the allow-list the second. A PAT is
NOT restricted to chosen domains by GoDaddy: it can touch every domain of the account, so the allow-list is
what keeps an agent to the zones an administrator listed.

`TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094). The server refuses to start without it, except on the
fake with `TRANIBOR_SERVER_INSECURE_DEV=1` said explicitly. `DNS_GODADDY_FAKE=1` replaces GoDaddy with a seeded
in-memory account, so tests and demos need no account.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr

#: The platform's token variable, the current core's name first, then an older core's.
TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")

#: GoDaddy's API roots. Fixed: an administrator picks an environment, never a host. The OTE host follows the
#: server variable `api.{environment-subdomain}.com` of GoDaddy's OpenAPI document (value `ote-godaddy`); the
#: documentation pages do not print it as a URL, so it is NOT confirmed by a real call.
ENVIRONMENTS: dict[str, str] = {
    "production": "https://api.godaddy.com",
    "ote": "https://api.ote-godaddy.com",
}

#: The fake account's zones the allow-list defaults to when nobody configured one (the fake also holds a
#: zone outside it, to prove it is refused).
FAKE_ZONES = ("example.com", "example.net")
FAKE_TOKEN = "fake-personal-access-token"  # noqa: S105 - the fake's own credential

#: Set by whoever installs the server (or the platform, when it does): how this instance names itself.
INSTANCE_ENV = "TRANIBOR_INSTANCE_NAME"

ZONE_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$")


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    return next((source[name] for name in names if source.get(name)), "")


def normalise_zone(name: str) -> str:
    """Lower case, no trailing dot."""
    return name.strip().lower().rstrip(".")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    fake: bool = False
    #: The name of this instance of the server: its connector's name in the platform (`get_provider` says it).
    instance: str = "dns-godaddy"
    port: int = 8080
    server_token: SecretStr | None = None
    insecure_dev: bool = False
    environment: str = "production"
    token: SecretStr = SecretStr("")
    zones: tuple[str, ...] = ()

    @property
    def base_url(self) -> str:
        return ENVIRONMENTS[self.environment]

    def require_server_token(self) -> SecretStr | None:
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses "
            "to start without the platform's token (only DNS_GODADDY_FAKE=1 with TRANIBOR_SERVER_INSECURE_DEV=1 "
            "runs without it)"
        )

    def require_account(self) -> None:
        """A real account needs its environment, its token and a non-empty allow-list."""
        if self.environment not in ENVIRONMENTS:
            raise ValueError(f"DNS_GODADDY_ENVIRONMENT must be one of {sorted(ENVIRONMENTS)}, not {self.environment!r}")
        for variable, present in (
            ("DNS_GODADDY_TOKEN", bool(self.token.get_secret_value())),
            ("DNS_GODADDY_ZONES", bool(self.zones)),
        ):
            if not present:
                raise ValueError(f"{variable} is not set: the server needs it to reach GoDaddy")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        token = _first(source, TOKEN_ENVS)
        fake = source.get("DNS_GODADDY_FAKE", "") in ("1", "true", "yes")
        zones = tuple(
            dict.fromkeys(normalise_zone(z) for z in source.get("DNS_GODADDY_ZONES", "").split(",") if z.strip())
        )
        for zone in zones:
            if not ZONE_NAME.fullmatch(zone):
                raise ValueError(f"DNS_GODADDY_ZONES: {zone!r} is not a zone name")
        if fake:
            zones = zones or FAKE_ZONES
        return cls(
            fake=fake,
            instance=source.get(INSTANCE_ENV, "") or "dns-godaddy",
            port=int(source.get("PORT", "8080")),
            server_token=SecretStr(token) if token else None,
            insecure_dev=_first(source, INSECURE_DEV_ENVS) == "1",
            environment=source.get("DNS_GODADDY_ENVIRONMENT", "production") or "production",
            token=SecretStr(FAKE_TOKEN if fake else source.get("DNS_GODADDY_TOKEN", "")),
            zones=zones,
        )
