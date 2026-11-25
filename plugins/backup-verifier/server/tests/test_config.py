# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from tranibor_backup_verifier.config import Settings, prefix_problem
from tranibor_backup_verifier.cron import next_fire, period_hours

from .conftest import NOW

REAL = {
    "TRANIBOR_SERVER_TOKEN": "t",
    "VELERO_KUBE_API_URL": "https://kubernetes.example.com:6443/",
    "VELERO_KUBE_TOKEN": "not-a-real-token",
    "VELERO_RESTORE_NAMESPACE_PREFIX": "restore-test-",
}


def problem(**override: str) -> str | None:
    env = {**REAL, **override}
    return Settings.from_env({k: v for k, v in env.items() if v != "<unset>"}).problem()


def test_a_complete_configuration_is_fine() -> None:
    assert problem() is None
    assert Settings.from_env(REAL).kube_api_url == "https://kubernetes.example.com:6443"


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"VELERO_KUBE_API_URL": "http://kubernetes.example.com"}, "kube_api_url"),
        ({"VELERO_KUBE_TOKEN": "<unset>"}, "kube_token"),
        ({"VELERO_RESTORE_NAMESPACE_PREFIX": "<unset>"}, "restore_namespace_prefix"),
        ({"VELERO_RESTORE_NAMESPACE_PREFIX": "restore"}, "restore_namespace_prefix"),
        ({"VELERO_S3_BUCKET": "backups"}, "bucket needs"),
        ({"VELERO_S3_ENDPOINT": "http://s3.example.com"}, "bucket needs"),
    ],
)
def test_what_is_missing_is_said(override: dict[str, str], fragment: str) -> None:
    assert fragment in (problem(**override) or "")


def test_http_bucket_endpoint_is_refused() -> None:
    why = problem(
        VELERO_S3_ENDPOINT="http://s3.example.com",
        VELERO_S3_BUCKET="b",
        VELERO_S3_ACCESS_KEY="a",
        VELERO_S3_SECRET_KEY="s",
    )
    assert why and "https" in why


@pytest.mark.parametrize(
    "prefix",
    [
        "kube-",
        "kube-system-",
        "velero-",
        "default-",
        "istio-",
        "monitoring-x-",
        "ingress-",
        "openshift-",
        "cert-manager-",
        "k",
        "Restore-",
        "a_b-",
        "restore-test",
    ],
)
def test_a_prefix_that_could_cover_a_system_namespace_is_refused(prefix: str) -> None:
    assert prefix_problem(prefix, "velero")


def test_a_prefix_may_not_cover_the_velero_namespace() -> None:
    assert prefix_problem("backup-", "backup-system")


def test_fake_mode_defaults_its_prefix() -> None:
    settings = Settings.from_env({"VELERO_FAKE": "1"})
    assert settings.restore_namespace_prefix == "restore-test-" and settings.problem() is None


def test_cron() -> None:
    assert period_hours("0 2 * * *", NOW) == 24
    assert period_hours("*/15 * * * *", NOW) == 0.25
    assert period_hours("0 3 * * 0", NOW) == 168
    assert period_hours("@every 6h", NOW) == 6
    assert period_hours("nonsense", NOW) is None
    fire = next_fire("30 4 * * 1-5", NOW)  # 2026-11-25 is a Wednesday, 09:17 is past 04:30
    assert fire is not None and fire.isoformat() == "2026-11-26T04:30:00+00:00"
    assert next_fire("0 0 31 2 *", NOW) is None  # no 31st of February
