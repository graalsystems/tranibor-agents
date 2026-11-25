# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` and its sealed `secret_fields`
(`servers/cert-watch.yaml`). `CERT_WATCH_FAKE=1` replaces every source (the Kubernetes API, the CT search
and the TLS probe) with a seeded in-memory one, so tests and demos need no cluster and no network.

`TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094): the bearer token it presents on every call. The
server refuses to start without it, except on the fake with `TRANIBOR_SERVER_INSECURE_DEV=1` said
explicitly, for a local demo. An older core sets `CHOREGOS_SERVER_TOKEN`, still read when the new name is
absent (and `CHOREGOS_SERVER_INSECURE_DEV` likewise).
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

#: What the fake serves: addresses and names are invented (RFC 6761 `.test` / `example.com`).
FAKE_KUBE_API_URL = "https://kube-api.fake.test:6443"
FAKE_KUBE_TOKEN = "fake-kube-token"  # noqa: S105 - the fake's own credential, not a secret
FAKE_DOMAINS = ("example.com",)
FAKE_PROBE_HOSTS = (
    "shop.example.com:443",
    "api.shop.example.com:443",
    "legacy.example.com:8443",
    "down.example.com:443",
)
DEFAULT_CT_URL = "https://crt.sh"
#: The platform's token variable, the current core's name first, then an older core's.
TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")

_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"^{_LABEL}(\.{_LABEL})*$")
_NAMESPACE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    """The value of the first of `names` set to something, else empty."""
    return next((source[name] for name in names if source.get(name)), "")


def _split(value: str) -> tuple[str, ...]:
    """A list field as the platform delivers it: items joined by commas (or newlines)."""
    return tuple(part.strip() for part in re.split(r"[,\n]", value) if part.strip())


def normalise_domain(value: str) -> str:
    """`Example.COM.`, `*.example.com` and `%.example.com` are all the domain `example.com`."""
    domain = value.strip().lower().rstrip(".")
    for prefix in ("*.", "%."):
        domain = domain.removeprefix(prefix)
    if not _HOSTNAME.fullmatch(domain) or "." not in domain:
        raise ValueError(f"{value!r} is not a domain name")
    return domain


def parse_host_port(value: str) -> tuple[str, int]:
    """`host`, `host:port` or `[v6]:port` as a normalised `(host, port)`; the port is 443 when absent."""
    text = value.strip().lower()
    host, port = text, 443
    if text.startswith("["):
        end = text.find("]")
        host = text[1:end] if end > 0 else ""
        rest = text[end + 1 :] if end > 0 else ""
        if rest.startswith(":"):
            port = _port(rest[1:], value)
    elif text.count(":") == 1:
        host, _, raw = text.partition(":")
        port = _port(raw, value)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        host = host.rstrip(".")
        if not _HOSTNAME.fullmatch(host):
            raise ValueError(f"{value!r} is not a host name or address with an optional port") from None
    return host, port


def _port(raw: str, original: str) -> int:
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ValueError(f"{original!r}: the port must be 1 to 65535")
    return int(raw)


def format_host_port(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    kube_api_url: str
    #: PEM text of the CA that signed the API server's certificate; the system roots when absent.
    kube_ca_cert: str | None = None
    kube_token: SecretStr | None = None
    #: Optional allow-list: the only namespaces ever read. Empty means every namespace the token may list.
    namespaces: tuple[str, ...] = ()
    #: The domains whose Certificate Transparency entries are searched (and their subdomains).
    domains: tuple[str, ...] = ()
    #: The only hosts ever probed, as `(host, port)`.
    probe_hosts: tuple[tuple[str, int], ...] = ()
    ct_url: str = DEFAULT_CT_URL
    fake: bool = False
    port: int = 8080
    #: The bearer token the platform presents on every call but the health probe (ADR 0094).
    server_token: SecretStr | None = None
    #: `TRANIBOR_SERVER_INSECURE_DEV=1`: no token, accepted on the fake only.
    insecure_dev: bool = False

    @model_validator(mode="after")
    def _valid(self) -> Settings:
        if not self.fake:
            if not self.kube_api_url.startswith("https://"):
                raise ValueError("CERT_WATCH_KUBE_API_URL must be an https:// URL")
            if self.kube_token is None or not self.kube_token.get_secret_value():
                raise ValueError("CERT_WATCH_KUBE_TOKEN is required: the service account's bearer token")
            if not self.ct_url.startswith("https://"):
                raise ValueError("CERT_WATCH_CT_URL must be an https:// URL")
        for namespace in self.namespaces:
            if not _NAMESPACE.fullmatch(namespace):
                raise ValueError(f"CERT_WATCH_NAMESPACES: {namespace!r} is not a namespace name")
        return self

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses to "
            "start without the platform's token (only CERT_WATCH_FAKE=1 with TRANIBOR_SERVER_INSECURE_DEV=1 runs "
            "without it)"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        token = _first(source, TOKEN_ENVS)
        common = {
            "port": int(source.get("PORT", "8080")),
            "server_token": SecretStr(token) if token else None,
            "insecure_dev": _first(source, INSECURE_DEV_ENVS) == "1",
        }
        namespaces = _split(source.get("CERT_WATCH_NAMESPACES", ""))
        if source.get("CERT_WATCH_FAKE", "") in ("1", "true", "yes"):
            return cls(
                kube_api_url=FAKE_KUBE_API_URL,
                kube_token=SecretStr(FAKE_KUBE_TOKEN),
                namespaces=namespaces,
                domains=FAKE_DOMAINS,
                probe_hosts=tuple(parse_host_port(h) for h in FAKE_PROBE_HOSTS),
                fake=True,
                **common,
            )
        kube_token = source.get("CERT_WATCH_KUBE_TOKEN", "")
        return cls(
            kube_api_url=source.get("CERT_WATCH_KUBE_API_URL", "").strip().rstrip("/"),
            kube_ca_cert=source.get("CERT_WATCH_KUBE_CA_CERT") or None,
            kube_token=SecretStr(kube_token.strip()) if kube_token.strip() else None,
            namespaces=namespaces,
            domains=tuple(dict.fromkeys(normalise_domain(d) for d in _split(source.get("CERT_WATCH_DOMAINS", "")))),
            probe_hosts=tuple(
                dict.fromkeys(parse_host_port(h) for h in _split(source.get("CERT_WATCH_PROBE_HOSTS", "")))
            ),
            ct_url=(source.get("CERT_WATCH_CT_URL") or DEFAULT_CT_URL).strip().rstrip("/"),
            **common,
        )
