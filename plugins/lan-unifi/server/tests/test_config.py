# SPDX-License-Identifier: Apache-2.0
"""The configuration: what is refused at start-up, and that TLS verification is never switched off."""

from __future__ import annotations

import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tranibor_lan_unifi.config import Settings

REAL = {"UNIFI_BASE_URL": "https://unifi.example.com/", "UNIFI_API_KEY": "an-api-key-for-tests-only"}


def test_a_real_console_needs_a_url_and_a_key() -> None:
    assert Settings.from_env({**REAL}).base_url == "https://unifi.example.com"
    for env in ({}, {"UNIFI_BASE_URL": REAL["UNIFI_BASE_URL"]}, {"UNIFI_API_KEY": REAL["UNIFI_API_KEY"]}):
        with pytest.raises(ValueError, match="required"):
            Settings.from_env(env)


@pytest.mark.parametrize(
    "url",
    ["http://unifi.example.com", "unifi.example.com", "https://u:p@unifi.example.com", "https://x.example.com/?a=b"],
)
def test_the_url_must_be_plain_https(url: str) -> None:
    with pytest.raises(ValueError, match="https"):
        Settings.from_env({**REAL, "UNIFI_BASE_URL": url})


def test_the_two_site_manager_fields_go_together() -> None:
    with pytest.raises(ValueError, match="both or neither"):
        Settings.from_env({**REAL, "UNIFI_SITE_MANAGER_URL": "https://api.ui.com"})
    with pytest.raises(ValueError, match="both or neither"):
        Settings.from_env({**REAL, "UNIFI_SITE_MANAGER_API_KEY": "another-key-for-tests-only"})
    both = {
        **REAL,
        "UNIFI_SITE_MANAGER_URL": "https://api.ui.com",
        "UNIFI_SITE_MANAGER_API_KEY": "another-key-for-tests-only",
    }
    assert Settings.from_env(both).cloud_enabled
    assert not Settings.from_env(REAL).cloud_enabled


def test_tls_is_verified_with_the_system_store_or_the_pinned_certificate_and_never_off() -> None:
    default = Settings.from_env(REAL).ssl_context()
    assert default.verify_mode == ssl.CERT_REQUIRED
    assert default.check_hostname
    with pytest.raises(ValueError, match="PEM"):
        Settings.from_env({**REAL, "UNIFI_CA_CERTIFICATE": "not a certificate"}).ssl_context()


def test_the_pinned_certificate_replaces_the_system_store() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "unifi.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    context = Settings.from_env({**REAL, "UNIFI_CA_CERTIFICATE": pem}).ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
