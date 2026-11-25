# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` and its sealed `secret_fields`
(`servers/certificates-cert-manager.yaml`). `CERTIFICATES_CERT_MANAGER_FAKE=1` replaces the Kubernetes API with a
seeded in-memory one, so tests and demos need no cluster. The token variables are shared (`envs.py`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from pydantic import SecretStr, model_validator

from .envs import CommonSettings, common, split

#: What the fake serves: the address is invented (RFC 6761 `.test`).
FAKE_KUBE_API_URL = "https://kube-api.fake.test:6443"
FAKE_KUBE_TOKEN = "fake-kube-token"  # noqa: S105 - the fake's own credential, not a secret

_NAMESPACE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


class Settings(CommonSettings):
    kube_api_url: str
    #: PEM text of the CA that signed the API server's certificate; the system roots when absent.
    kube_ca_cert: str | None = None
    kube_token: SecretStr | None = None
    #: Optional allow-list: the only namespaces ever read. Empty means every namespace the token may list.
    namespaces: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _valid(self) -> Settings:
        if not self.fake:
            if not self.kube_api_url.startswith("https://"):
                raise ValueError("CERTIFICATES_CERT_MANAGER_KUBE_API_URL must be an https:// URL")
            if self.kube_token is None or not self.kube_token.get_secret_value():
                raise ValueError("CERTIFICATES_CERT_MANAGER_KUBE_TOKEN is required: the service account's bearer token")
        for namespace in self.namespaces:
            if not _NAMESPACE.fullmatch(namespace):
                raise ValueError(f"CERTIFICATES_CERT_MANAGER_NAMESPACES: {namespace!r} is not a namespace name")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        base = common(source, "certificates-cert-manager")
        namespaces = split(source.get("CERTIFICATES_CERT_MANAGER_NAMESPACES", ""))
        if source.get("CERTIFICATES_CERT_MANAGER_FAKE", "") in ("1", "true", "yes"):
            return cls(
                kube_api_url=FAKE_KUBE_API_URL,
                kube_token=SecretStr(FAKE_KUBE_TOKEN),
                namespaces=namespaces,
                fake=True,
                **base,
            )
        kube_token = source.get("CERTIFICATES_CERT_MANAGER_KUBE_TOKEN", "")
        return cls(
            kube_api_url=source.get("CERTIFICATES_CERT_MANAGER_KUBE_API_URL", "").strip().rstrip("/"),
            kube_ca_cert=source.get("CERTIFICATES_CERT_MANAGER_KUBE_CA_CERT") or None,
            kube_token=SecretStr(kube_token.strip()) if kube_token.strip() else None,
            namespaces=namespaces,
            **base,
        )
