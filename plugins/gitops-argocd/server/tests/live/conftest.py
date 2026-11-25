# SPDX-License-Identifier: Apache-2.0
"""The server against a REAL Argo CD: opt-in, skipped without its variables.

    TRANIBOR_LIVE_ARGOCD_URL=https://argocd.example.com \\
    TRANIBOR_LIVE_ARGOCD_TOKEN=<API token of a dedicated local account> \\
    TRANIBOR_LIVE_ARGOCD_PROJECTS=<comma-separated AppProjects the account may read> \\
      uv run pytest tests/live -q

- Read-only by default: the reads, the refresh and the dry run need only `applications, get`.
- `TRANIBOR_LIVE_ARGOCD_SYNC_APP=<name>` also runs the sync test, on that Application only: use a
  throwaway Application of a test project, never a production one. It needs `applications, sync`.
- `TRANIBOR_LIVE_ARGOCD_CA_CERT` is the PEM text of a private CA, when Argo CD uses one.
- `TRANIBOR_LIVE_REQUIRED=argocd` turns a missing variable into a failure, as the core's live suite does.

These tests were written without access to a real Argo CD and have NOT been run: the points the client
marks as unconfirmed (see the docstring of `client.py`) are what they check first.
"""

from __future__ import annotations

import os

import pytest
from pydantic import SecretStr

from tranibor_gitops_argocd.app import Runtime, build_server
from tranibor_gitops_argocd.config import Settings

from ..conftest import Mcp


def env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_ARGOCD_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"argocd", "all"})


def missing(what: str) -> None:
    if _required():
        pytest.fail(f"live Argo CD test required but not configured: {what}", pytrace=False)
    pytest.skip(f"live Argo CD test skipped: {what}")


@pytest.fixture
def live_settings() -> Settings:
    if not (env("URL") and env("TOKEN") and env("PROJECTS")):
        missing("TRANIBOR_LIVE_ARGOCD_URL, _TOKEN and _PROJECTS")
    return Settings(
        base_url=env("URL"),
        token=SecretStr(env("TOKEN")),
        projects=tuple(p.strip() for p in env("PROJECTS").split(",") if p.strip()),
        ca_cert=env("CA_CERT") or None,
    )


@pytest.fixture
def live(live_settings: Settings) -> Mcp:
    return Mcp(build_server(Runtime(live_settings)))
