# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform delivers each field of the connector's configuration as the variable `servers/velero.yaml`
names in `x-env`; the secrets (`kube_token`, `s3_access_key`, `s3_secret_key`) come from a sealed Secret.

`TRANIBOR_SERVER_TOKEN` is the platform's (ADR 0094): the bearer token it presents on every call. The server
refuses to start without it, except on the fake cluster with `TRANIBOR_SERVER_INSECURE_DEV=1` said
explicitly. `VELERO_FAKE=1` replaces the Kubernetes API and the bucket with a seeded in-memory cluster.

A server whose connector is not configured yet still starts (so the platform can discover its tools); every
tool then fails with `not_configured` and says what is missing.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr

#: The platform's token variable, the current core's name first, then an older core's.
TOKEN_ENVS = ("TRANIBOR_SERVER_TOKEN", "CHOREGOS_SERVER_TOKEN")
INSECURE_DEV_ENVS = ("TRANIBOR_SERVER_INSECURE_DEV", "CHOREGOS_SERVER_INSECURE_DEV")

#: A DNS-1123 label fragment, and a prefix: one or more such labels' characters ending with a dash.
PREFIX = re.compile(r"^[a-z0-9][a-z0-9-]{3,28}-$")
#: Namespaces no prefix may reach into, whatever an administrator types.
RESERVED = ("kube-", "default", "velero", "openshift", "istio-", "cert-manager", "ingress", "monitoring")


def _first(source: Mapping[str, str], names: tuple[str, ...]) -> str:
    return next((source[name] for name in names if source.get(name)), "")


def prefix_problem(prefix: str, velero_namespace: str) -> str | None:
    """Why `prefix` cannot be the restore prefix, or None. The server is the namespace guard (Kubernetes RBAC
    cannot restrict names by prefix), so a prefix that could cover a system namespace is refused outright."""
    if not PREFIX.fullmatch(prefix):
        return (
            "restore_namespace_prefix must be 5 to 30 lowercase letters, digits and dashes, start with a letter or "
            "digit and end with a dash (e.g. restore-test-)"
        )
    for reserved in RESERVED:
        if reserved.startswith(prefix) or prefix.startswith(reserved):
            return f"restore_namespace_prefix {prefix!r} could cover the reserved namespace {reserved!r}"
    if velero_namespace.startswith(prefix):
        return f"restore_namespace_prefix {prefix!r} covers the Velero namespace {velero_namespace!r}"
    return None


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    fake: bool = False
    port: int = 8080
    server_token: SecretStr | None = None
    insecure_dev: bool = False

    kube_api_url: str = ""
    kube_ca_cert: str = ""
    kube_token: SecretStr | None = None
    velero_namespace: str = "velero"
    restore_namespace_prefix: str = ""

    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_prefix: str = ""
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake cluster in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "TRANIBOR_SERVER_TOKEN (or an older core's CHOREGOS_SERVER_TOKEN) is not set: the server refuses "
            "to start without the platform's token (only VELERO_FAKE=1 with TRANIBOR_SERVER_INSECURE_DEV=1 runs "
            "without it)"
        )

    @property
    def s3_configured(self) -> bool:
        return bool(self.s3_endpoint and self.s3_bucket and self.s3_access_key and self.s3_secret_key)

    def problem(self) -> str | None:
        """What stops the connector from working, or None. Checked on every call, never logged with a value."""
        if self.fake:
            return None
        if not self.kube_api_url.startswith("https://"):
            return "kube_api_url (an https:// URL) is not configured"
        if self.kube_token is None or not self.kube_token.get_secret_value():
            return "kube_token is not configured"
        if not self.restore_namespace_prefix:
            return "restore_namespace_prefix is not configured"
        if why := prefix_problem(self.restore_namespace_prefix, self.velero_namespace):
            return why
        if any((self.s3_endpoint, self.s3_bucket)) and not self.s3_configured:
            return "the bucket needs s3_endpoint, s3_bucket, s3_access_key and s3_secret_key together"
        if self.s3_endpoint and not self.s3_endpoint.startswith("https://"):
            return "s3_endpoint must be an https:// URL"
        return None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        token = _first(e, TOKEN_ENVS)

        def secret(name: str) -> SecretStr | None:
            return SecretStr(e[name]) if e.get(name) else None

        fake = e.get("VELERO_FAKE", "") in ("1", "true", "yes")
        return cls(
            fake=fake,
            port=int(e.get("PORT", "8080")),
            server_token=SecretStr(token) if token else None,
            insecure_dev=_first(e, INSECURE_DEV_ENVS) == "1",
            kube_api_url=e.get("VELERO_KUBE_API_URL", "").rstrip("/"),
            kube_ca_cert=e.get("VELERO_KUBE_CA_CERT", ""),
            kube_token=secret("VELERO_KUBE_TOKEN"),
            velero_namespace=e.get("VELERO_NAMESPACE") or "velero",
            restore_namespace_prefix=e.get("VELERO_RESTORE_NAMESPACE_PREFIX", "") or ("restore-test-" if fake else ""),
            s3_endpoint=e.get("VELERO_S3_ENDPOINT", "").rstrip("/"),
            s3_bucket=e.get("VELERO_S3_BUCKET", ""),
            s3_region=e.get("VELERO_S3_REGION") or "us-east-1",
            s3_prefix=e.get("VELERO_S3_PREFIX", "").strip("/"),
            s3_access_key=secret("VELERO_S3_ACCESS_KEY"),
            s3_secret_key=secret("VELERO_S3_SECRET_KEY"),
        )
