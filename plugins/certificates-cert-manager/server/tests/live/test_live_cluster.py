# SPDX-License-Identifier: Apache-2.0
"""The profile's conformance suite against a REAL cluster: opt-in, skipped without its variables.

    TRANIBOR_LIVE_CERTIFICATES_CERT_MANAGER_KUBE_API_URL=https://k8s.example.org:6443 \\
    TRANIBOR_LIVE_CERTIFICATES_CERT_MANAGER_KUBE_TOKEN=... \\
    TRANIBOR_LIVE_CERTIFICATES_CERT_MANAGER_NAMESPACE=<a namespace with a Certificate> \\
      uv run pytest tests/live -q

- Read-only: the suite only sends GET requests, through the service account of the procedure.
- The suite's window and ordering checks need Certificates both inside and outside a 30-day window in the
  namespace: with fewer, those checks fail on the data, not on the server. Set
  `TRANIBOR_LIVE_CERTIFICATES_CERT_MANAGER_WINDOW_DAYS` to a window that splits them, and
  `..._UNKNOWN_ID` / `..._FOREIGN_NAMESPACE` if the defaults (`<namespace>/no-such-certificate`, `kube-system`)
  do not suit the cluster.
- `TRANIBOR_LIVE_REQUIRED=certificates-cert-manager` turns a missing variable into a failure.
- This suite has NEVER been run by its author (no cluster): treat its first run as the first real test of the
  client's paths, the RBAC of the procedure and the reading of real cert-manager objects.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from tranibor_certificates_conformance import Conformance, McpTarget

from tranibor_certificates_cert_manager.app import Runtime, build_server
from tranibor_certificates_cert_manager.config import Settings

from ..conftest import DESCRIPTOR

PREFIX = "TRANIBOR_LIVE_CERTIFICATES_CERT_MANAGER_"


def _env(name: str) -> str:
    return os.environ.get(f"{PREFIX}{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"certificates-cert-manager", "all"})


class Counting(httpx.AsyncBaseTransport):
    def __init__(self, verify: object) -> None:
        self.inner = httpx.AsyncHTTPTransport(verify=verify)  # type: ignore[arg-type]
        self.count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        return await self.inner.handle_async_request(request)


@pytest.fixture
async def certificates_target() -> AsyncIterator[McpTarget]:
    missing = [n for n in ("KUBE_API_URL", "KUBE_TOKEN", "NAMESPACE") if not _env(n)]
    if missing:
        message = f"live cert-manager test not configured: {PREFIX}{', '.join(missing)}"
        if _required():
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    namespace = _env("NAMESPACE")
    settings = Settings.from_env(
        {
            "CERTIFICATES_CERT_MANAGER_KUBE_API_URL": _env("KUBE_API_URL"),
            "CERTIFICATES_CERT_MANAGER_KUBE_TOKEN": _env("KUBE_TOKEN"),
            "CERTIFICATES_CERT_MANAGER_KUBE_CA_CERT": _env("KUBE_CA_CERT"),
            "CERTIFICATES_CERT_MANAGER_NAMESPACES": namespace,
        }
    )
    from tranibor_certificates_cert_manager.kube import ssl_context

    transport = Counting(ssl_context(settings.kube_ca_cert))
    runtime = Runtime(settings, transport)
    try:
        yield McpTarget(
            build_server(runtime),
            descriptor_path=DESCRIPTOR,
            foreign_id=f"{_env('FOREIGN_NAMESPACE') or 'kube-system'}/anything",
            unknown_id=_env("UNKNOWN_ID") or f"{namespace}/no-such-certificate",
            upstream_calls=lambda: transport.count,
            window_days=int(_env("WINDOW_DAYS") or 30),
        )
    finally:
        await runtime.client.aclose()


class TestLiveCertManager(Conformance):
    """The same checks as tests/test_conformance.py, over a real cluster."""
