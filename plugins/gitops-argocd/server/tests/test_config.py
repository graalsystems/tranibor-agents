# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from pydantic import SecretStr

from tranibor_gitops_argocd.config import Settings

REAL = {
    "GITOPS_ARGOCD_BASE_URL": "https://argocd.example.com",
    "GITOPS_ARGOCD_TOKEN": "fake-token",
    "GITOPS_ARGOCD_PROJECTS": "shop, billing",
}
PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


def test_projects_are_split_and_trimmed() -> None:
    assert Settings.from_env(REAL).projects == ("shop", "billing")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"GITOPS_ARGOCD_BASE_URL": "http://argocd.example.com"}, "https"),
        ({"GITOPS_ARGOCD_TOKEN": ""}, "GITOPS_ARGOCD_TOKEN"),
        ({"GITOPS_ARGOCD_PROJECTS": ""}, "allow-list"),
        ({"GITOPS_ARGOCD_PROJECTS": "shop,*"}, "wildcard"),
        ({"GITOPS_ARGOCD_CA_CERT": "not a certificate"}, "PEM"),
    ],
)
def test_invalid_configuration_is_refused(change: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings.from_env({**REAL, **change})


def test_a_valid_ca_is_accepted() -> None:
    assert Settings.from_env({**REAL, "GITOPS_ARGOCD_CA_CERT": PEM}).ca_cert == PEM


def test_the_token_is_never_printed() -> None:
    settings = Settings.from_env(REAL)
    assert "fake-token" not in repr(settings) and isinstance(settings.token, SecretStr)


def test_server_token_and_the_older_name() -> None:
    assert Settings.from_env({**REAL, "TRANIBOR_SERVER_TOKEN": "a"}).require_server_token() is not None
    assert Settings.from_env({**REAL, "CHOREGOS_SERVER_TOKEN": "b"}).require_server_token() is not None
    with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
        Settings.from_env(REAL).require_server_token()


def test_insecure_dev_is_for_the_fake_only() -> None:
    assert (
        Settings.from_env({"GITOPS_ARGOCD_FAKE": "1", "TRANIBOR_SERVER_INSECURE_DEV": "1"}).require_server_token()
        is None
    )
    with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
        Settings.from_env({"GITOPS_ARGOCD_FAKE": "1"}).require_server_token()
    with pytest.raises(ValueError, match="TRANIBOR_SERVER_TOKEN"):
        Settings.from_env({**REAL, "TRANIBOR_SERVER_INSECURE_DEV": "1"}).require_server_token()
