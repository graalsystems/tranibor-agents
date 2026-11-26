# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

Each field of `servers/lan-unifi.yaml`'s `config_schema` reaches the server as the variable named in its
`x-env`. `TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094): the bearer token it presents on every call;
the server refuses to start without it, except on the fake with `TRANIBOR_SERVER_INSECURE_DEV=1` said
explicitly. `LAN_UNIFI_FAKE=1` replaces UniFi with a seeded in-memory site, so tests and demos need no console.

Two ways in (`UNIFI_CONNECTION`): `local` (default), the console's own address `UNIFI_BASE_URL` with its
Integration API key; `cloud`, Ubiquiti's official connector: `https://api.ui.com/v1/connector/consoles/<id>/...`
with `UNIFI_CONSOLE_ID` and a Site Manager key (`UNIFI_SITE_MANAGER_API_KEY`), so the only host reached is
api.ui.com and the console needs neither a public address nor a private route. The same tools run on both.

TLS is never switched off: a console with its own certificate is trusted through `UNIFI_CA_CERTIFICATE`
(the PEM of the certificate, or of the CA that signed it), which then replaces the system store for that
console alone.

The core names the pod's variable `TRANIBOR_SERVER_TOKEN` since its `TRANIBOR_*` rename (core ADR 0176);
an older core sets `CHOREGOS_SERVER_TOKEN`, still read when the new name is absent.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr

from .client import CONSOLE_ID

TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")

#: What the fake answers as: documentation names (RFC 2606) and a key that is obviously not one.
FAKE_BASE_URL = "https://unifi.example.com"
FAKE_CLOUD_URL = "https://api.ui.com"
FAKE_API_KEY = "fake-api-key-not-a-secret"
FAKE_CONSOLE_ID = "0A1B2C3D4E5F0000000000000000000000000000:1234567890"


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    return next((source[name] for name in names if source.get(name)), "")


def _https(name: str, value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query:
        raise ValueError(f"{name} must be an https URL without credentials or query: {value!r}")
    return value.rstrip("/")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    fake: bool = False
    #: The name of this instance of the server (its connector name), told by `get_provider`.
    instance: str = "lan-unifi"
    port: int = 8080
    server_token: SecretStr | None = None
    insecure_dev: bool = False
    #: `local`: the console's own address; `cloud`: Ubiquiti's cloud connector (api.ui.com).
    connection: Literal["local", "cloud"] = "local"
    #: The console's id in the Site Manager (`GET /v1/hosts`), cloud mode only.
    console_id: str = ""
    #: The console (UniFi OS) the Integration API is reached on, e.g. https://unifi.example.com.
    base_url: str = FAKE_BASE_URL
    api_key: SecretStr = SecretStr(FAKE_API_KEY)
    #: PEM of the console's certificate or its CA; empty: the system store.
    ca_certificate: str = ""
    #: The Site Manager (cloud) API: both fields, or neither.
    site_manager_url: str = ""
    site_manager_api_key: SecretStr | None = None

    @property
    def cloud_enabled(self) -> bool:
        return bool(self.site_manager_url) and self.site_manager_api_key is not None

    @property
    def cloud_connection(self) -> bool:
        return self.connection == "cloud"

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses "
            "to start without the platform's token (only LAN_UNIFI_FAKE=1 with TRANIBOR_SERVER_INSECURE_DEV=1 "
            "runs without it)"
        )

    def ssl_context(self) -> ssl.SSLContext:
        """Verification stays on. With a pinned certificate it is the only one trusted; else the system's."""
        if not self.ca_certificate.strip():
            return ssl.create_default_context()
        try:
            return ssl.create_default_context(cadata=self.ca_certificate)
        except (ssl.SSLError, ValueError) as error:
            raise ValueError("UNIFI_CA_CERTIFICATE is not a PEM certificate") from error

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        token = _first(source, TOKEN_ENVS)
        fake = source.get("LAN_UNIFI_FAKE", "") in ("1", "true", "yes")
        connection = source.get("UNIFI_CONNECTION", "") or "local"
        if connection not in ("local", "cloud"):
            raise ValueError(f"UNIFI_CONNECTION must be local or cloud: {connection!r}")
        base_url = source.get("UNIFI_BASE_URL", "")
        api_key = source.get("UNIFI_API_KEY", "")
        console_id = source.get("UNIFI_CONSOLE_ID", "")
        cloud_url = source.get("UNIFI_SITE_MANAGER_URL", "")
        cloud_key = source.get("UNIFI_SITE_MANAGER_API_KEY", "")
        if connection == "cloud":
            if fake:
                cloud_url = cloud_url or FAKE_CLOUD_URL
                cloud_key = cloud_key or FAKE_API_KEY
                console_id = console_id or FAKE_CONSOLE_ID
            if not cloud_key or not console_id or not cloud_url:
                # The URL is not implied: the platform opens the egress from this very field.
                raise ValueError(
                    "UNIFI_CONNECTION=cloud needs UNIFI_CONSOLE_ID, UNIFI_SITE_MANAGER_API_KEY and "
                    "UNIFI_SITE_MANAGER_URL=https://api.ui.com (the egress is opened from it)"
                )
            if not CONSOLE_ID.fullmatch(console_id):
                raise ValueError("UNIFI_CONSOLE_ID is not a console id (the id of GET /v1/hosts)")
        elif not fake and (not base_url or not api_key):
            raise ValueError("UNIFI_BASE_URL and UNIFI_API_KEY are required (UNIFI_CONNECTION=local)")
        if bool(cloud_url) != bool(cloud_key):
            raise ValueError("UNIFI_SITE_MANAGER_URL and UNIFI_SITE_MANAGER_API_KEY go together: set both or neither")
        return cls(
            fake=fake,
            instance=source.get("TRANIBOR_INSTANCE_NAME") or "lan-unifi",
            port=int(source.get("PORT", "8080")),
            server_token=SecretStr(token) if token else None,
            insecure_dev=_first(source, INSECURE_DEV_ENVS) == "1",
            connection="cloud" if connection == "cloud" else "local",
            console_id=console_id,
            base_url=_https("UNIFI_BASE_URL", base_url) if base_url else FAKE_BASE_URL,
            api_key=SecretStr(api_key or FAKE_API_KEY),
            ca_certificate=source.get("UNIFI_CA_CERTIFICATE", ""),
            site_manager_url=_https("UNIFI_SITE_MANAGER_URL", cloud_url) if cloud_url else "",
            site_manager_api_key=SecretStr(cloud_key) if cloud_key else None,
        )
