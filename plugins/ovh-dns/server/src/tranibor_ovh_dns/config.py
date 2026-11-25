# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

`OVH_DNS_ZONES` is the allow-list: the server touches no other zone, not even to read it. The three
credentials (application key, application secret, consumer key) identify one OVHcloud API application and
the consumer key's access rules (the procedure restricts them to the same zones; the allow-list is the
second lock, not the only one).

`TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094). The server refuses to start without it, except on the
fake with `TRANIBOR_SERVER_INSECURE_DEV=1` said explicitly. `OVH_DNS_FAKE=1` replaces OVHcloud with a seeded
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

#: OVHcloud's regional API roots (API v1). Fixed: an administrator picks a region, never a host.
ENDPOINTS: dict[str, str] = {
    "ovh-eu": "https://eu.api.ovh.com/1.0",
    "ovh-ca": "https://ca.api.ovh.com/1.0",
    "ovh-us": "https://api.us.ovhcloud.com/1.0",
}

#: The fake account's zones the allow-list defaults to when nobody configured one (the fake also holds a
#: zone outside it, to prove it is refused).
FAKE_ZONES = ("example.com", "example.net")
FAKE_APPLICATION_KEY = "fake-application-key"
FAKE_APPLICATION_SECRET = "fake-application-secret"  # noqa: S105 - the fake's own credential
FAKE_CONSUMER_KEY = "fake-consumer-key"

ZONE_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z0-9-]{2,63}$")


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    return next((source[name] for name in names if source.get(name)), "")


def normalise_zone(name: str) -> str:
    """Lower case, no trailing dot."""
    return name.strip().lower().rstrip(".")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    fake: bool = False
    port: int = 8080
    server_token: SecretStr | None = None
    insecure_dev: bool = False
    endpoint: str = "ovh-eu"
    application_key: str = ""
    application_secret: SecretStr = SecretStr("")
    consumer_key: SecretStr = SecretStr("")
    zones: tuple[str, ...] = ()

    @property
    def base_url(self) -> str:
        return ENDPOINTS[self.endpoint]

    def require_server_token(self) -> SecretStr | None:
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses "
            "to start without the platform's token (only OVH_DNS_FAKE=1 with TRANIBOR_SERVER_INSECURE_DEV=1 "
            "runs without it)"
        )

    def require_account(self) -> None:
        """A real account needs its endpoint, its three credentials and a non-empty allow-list."""
        if self.endpoint not in ENDPOINTS:
            raise ValueError(f"OVH_DNS_ENDPOINT must be one of {sorted(ENDPOINTS)}, not {self.endpoint!r}")
        for variable, present in (
            ("OVH_DNS_APPLICATION_KEY", bool(self.application_key)),
            ("OVH_DNS_APPLICATION_SECRET", bool(self.application_secret.get_secret_value())),
            ("OVH_DNS_CONSUMER_KEY", bool(self.consumer_key.get_secret_value())),
            ("OVH_DNS_ZONES", bool(self.zones)),
        ):
            if not present:
                raise ValueError(f"{variable} is not set: the server needs it to reach OVHcloud")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        token = _first(source, TOKEN_ENVS)
        fake = source.get("OVH_DNS_FAKE", "") in ("1", "true", "yes")
        zones = tuple(dict.fromkeys(normalise_zone(z) for z in source.get("OVH_DNS_ZONES", "").split(",") if z.strip()))
        for zone in zones:
            if not ZONE_NAME.fullmatch(zone):
                raise ValueError(f"OVH_DNS_ZONES: {zone!r} is not a zone name")
        if fake:
            zones = zones or FAKE_ZONES
        return cls(
            fake=fake,
            port=int(source.get("PORT", "8080")),
            server_token=SecretStr(token) if token else None,
            insecure_dev=_first(source, INSECURE_DEV_ENVS) == "1",
            endpoint=source.get("OVH_DNS_ENDPOINT", "ovh-eu") or "ovh-eu",
            application_key=FAKE_APPLICATION_KEY if fake else source.get("OVH_DNS_APPLICATION_KEY", ""),
            application_secret=SecretStr(
                FAKE_APPLICATION_SECRET if fake else source.get("OVH_DNS_APPLICATION_SECRET", "")
            ),
            consumer_key=SecretStr(FAKE_CONSUMER_KEY if fake else source.get("OVH_DNS_CONSUMER_KEY", "")),
            zones=zones,
        )
