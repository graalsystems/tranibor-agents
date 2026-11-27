# SPDX-License-Identifier: Apache-2.0
"""The server against a REAL cluster running Velero: opt-in, skipped without its variables.

    TRANIBOR_LIVE_VELERO_API_URL=https://kubernetes.example.com:6443 \\
    TRANIBOR_LIVE_VELERO_TOKEN=<service account token> \\
    TRANIBOR_LIVE_VELERO_PREFIX=restore-test- \\
    TRANIBOR_LIVE_VELERO_CA_CERT="$(cat ca.crt)"        # optional \\
    TRANIBOR_LIVE_VELERO_NAMESPACE=velero               # optional \\
    TRANIBOR_LIVE_VELERO_BACKUP=<a Completed backup>    # enables the restore test \\
    TRANIBOR_LIVE_VELERO_SOURCE_NS=<one of its namespaces> \\
      uv run pytest tests/live -q

Without TRANIBOR_LIVE_VELERO_BACKUP the run only reads. The restore test creates a namespace inside the prefix
and deletes it at the end. `TRANIBOR_LIVE_REQUIRED=velero` turns a missing variable into a failure.
"""

from __future__ import annotations

import os

import pytest

from tranibor_backup_velero.app import Runtime
from tranibor_backup_velero.config import Settings
from tranibor_backup_velero.tools import Tools


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_VELERO_{name}", "")


@pytest.fixture
def live() -> Tools:
    if not (_env("API_URL") and _env("TOKEN") and _env("PREFIX")):
        message = "TRANIBOR_LIVE_VELERO_API_URL, _TOKEN and _PREFIX are not set"
        if "velero" in os.environ.get("TRANIBOR_LIVE_REQUIRED", ""):
            pytest.fail(message)
        pytest.skip(message)
    settings = Settings.from_env(
        {
            "TRANIBOR_SERVER_TOKEN": "live",
            "VELERO_KUBE_API_URL": _env("API_URL"),
            "VELERO_KUBE_TOKEN": _env("TOKEN"),
            "VELERO_KUBE_CA_CERT": _env("CA_CERT"),
            "VELERO_NAMESPACE": _env("NAMESPACE") or "velero",
            "VELERO_RESTORE_NAMESPACE_PREFIX": _env("PREFIX"),
        }
    )
    assert settings.problem() is None, settings.problem()
    return Runtime(settings).tools
