# SPDX-License-Identifier: Apache-2.0
"""The server against a REAL cluster, crt.sh and TLS host: opt-in, skipped without its variables.

    TRANIBOR_LIVE_CERT_WATCH_KUBE_API_URL=https://<api-server>:6443 \\
    TRANIBOR_LIVE_CERT_WATCH_KUBE_TOKEN=<token of the read-only service account> \\
    TRANIBOR_LIVE_CERT_WATCH_KUBE_CA_CERT="$(cat ca.crt)"            # when the CA is private \\
    TRANIBOR_LIVE_CERT_WATCH_DOMAINS=example.org                      # a domain you own \\
    TRANIBOR_LIVE_CERT_WATCH_PROBE_HOSTS=www.example.org:443 \\
      uv run pytest tests/live -q

Only the cluster variables are needed for the cluster tests; `DOMAINS` enables the crt.sh test and
`PROBE_HOSTS` the probe test. `TRANIBOR_LIVE_REQUIRED=cert-watch` turns a missing variable into a failure,
as the core's live suite does.
"""

from __future__ import annotations

import os

import pytest

from tranibor_cert_watch.app import Runtime, build_server
from tranibor_cert_watch.config import Settings

from ..conftest import Mcp


def env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_CERT_WATCH_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"cert-watch", "cert_watch", "all"})


def need(*names: str) -> None:
    missing = [n for n in names if not env(n)]
    if not missing:
        return
    what = ", ".join(f"TRANIBOR_LIVE_CERT_WATCH_{n}" for n in missing)
    if _required():
        pytest.fail(f"live cert-watch test required but not configured: {what}", pytrace=False)
    pytest.skip(f"live cert-watch test skipped: {what}")


@pytest.fixture
def live_mcp() -> Mcp:
    need("KUBE_API_URL", "KUBE_TOKEN")
    settings = Settings.from_env(
        {
            "CERT_WATCH_KUBE_API_URL": env("KUBE_API_URL"),
            "CERT_WATCH_KUBE_TOKEN": env("KUBE_TOKEN"),
            "CERT_WATCH_KUBE_CA_CERT": env("KUBE_CA_CERT"),
            "CERT_WATCH_NAMESPACES": env("NAMESPACES"),
            "CERT_WATCH_DOMAINS": env("DOMAINS"),
            "CERT_WATCH_PROBE_HOSTS": env("PROBE_HOSTS"),
            "TRANIBOR_SERVER_INSECURE_DEV": "1",
        }
    )
    runtime = Runtime(settings)
    return Mcp(build_server(runtime))
