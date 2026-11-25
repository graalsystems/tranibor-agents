# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` (`servers/certificates-ct.yaml`).
`CERTIFICATES_CT_FAKE=1` replaces the Certificate Transparency search with a seeded in-memory one, so tests and
demos need no network. The token variables are shared (`envs.py`).
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import model_validator

from .envs import HOSTNAME, CommonSettings, common, split

DEFAULT_CT_URL = "https://crt.sh"
#: What the fake serves: names are invented (example.com); nothing leaves the process.
FAKE_DOMAINS = ("example.com",)


def normalise_domain(value: str) -> str:
    """`Example.COM.`, `*.example.com` and `%.example.com` are all the domain `example.com`."""
    domain = value.strip().lower().rstrip(".")
    for prefix in ("*.", "%."):
        domain = domain.removeprefix(prefix)
    if not HOSTNAME.fullmatch(domain) or "." not in domain:
        raise ValueError(f"{value!r} is not a domain name")
    return domain


class Settings(CommonSettings):
    #: The domains whose Certificate Transparency entries are searched (and their subdomains).
    domains: tuple[str, ...] = ()
    ct_url: str = DEFAULT_CT_URL

    @model_validator(mode="after")
    def _valid(self) -> Settings:
        if not self.fake and not self.ct_url.startswith("https://"):
            raise ValueError("CERTIFICATES_CT_URL must be an https:// URL")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        import os

        source = os.environ if env is None else env
        base = common(source, "certificates-ct")
        if source.get("CERTIFICATES_CT_FAKE", "") in ("1", "true", "yes"):
            return cls(domains=FAKE_DOMAINS, fake=True, **base)
        domains = tuple(dict.fromkeys(normalise_domain(d) for d in split(source.get("CERTIFICATES_CT_DOMAINS", ""))))
        if not domains:
            raise ValueError("CERTIFICATES_CT_DOMAINS is required: the domains whose issued certificates to follow")
        url = source.get("CERTIFICATES_CT_URL", "").strip().rstrip("/") or DEFAULT_CT_URL
        return cls(domains=domains, ct_url=url, **base)
