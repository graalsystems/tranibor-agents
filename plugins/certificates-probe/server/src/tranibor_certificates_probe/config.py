# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` (`servers/certificates-probe.yaml`).
`CERTIFICATES_PROBE_FAKE=1` replaces the TLS probe with a seeded in-memory one, so tests and demos need no
network. The token variables are shared (`envs.py`).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping

from .envs import HOSTNAME, CommonSettings, common, split

#: What the fake serves: names are invented (RFC 6761 / example.com), nothing is dialled.
FAKE_HOSTS = (
    "shop.example.com:443",
    "api.shop.example.com:443",
    "legacy.example.com:8443",
    "old.example.com:443",
    "wrong.example.com:443",
    "down.example.com:443",
)
DEFAULT_PORT = 443


def parse_host_port(value: str) -> tuple[str, int]:
    """`host`, `host:port` or `[v6]:port` as a normalised `(host, port)`; the port is 443 when absent."""
    text = value.strip().lower()
    host, port = text, DEFAULT_PORT
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
        if not HOSTNAME.fullmatch(host):
            raise ValueError(f"{value!r} is not a host name or address with an optional port") from None
    return host, port


def _port(raw: str, original: str) -> int:
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ValueError(f"{original!r}: the port must be 1 to 65535")
    return int(raw)


def format_host_port(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class Settings(CommonSettings):
    #: The only hosts ever dialled, as `(host, port)`.
    hosts: tuple[tuple[str, int], ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        import os

        source = os.environ if env is None else env
        base = common(source, "certificates-probe")
        if source.get("CERTIFICATES_PROBE_FAKE", "") in ("1", "true", "yes"):
            return cls(hosts=tuple(parse_host_port(h) for h in FAKE_HOSTS), fake=True, **base)
        hosts = tuple(dict.fromkeys(parse_host_port(h) for h in split(source.get("CERTIFICATES_PROBE_HOSTS", ""))))
        if not hosts:
            raise ValueError("CERTIFICATES_PROBE_HOSTS is required: the hosts to probe, as host or host:port")
        return cls(hosts=hosts, **base)
