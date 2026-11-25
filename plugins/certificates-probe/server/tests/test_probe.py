# SPDX-License-Identifier: Apache-2.0
"""The probe's own logic: host parsing, the allow-list and the certificate reading, on a certificate made here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tranibor_certificates_probe.config import Settings, format_host_port, parse_host_port
from tranibor_certificates_probe.errors import Refused
from tranibor_certificates_probe.probe import check_target, describe, hostname_matches


def _make(cn: str, issuer_cn: str, sans: list[str], days: int) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example.COM", ("example.com", 443)),
        ("example.com:8443", ("example.com", 8443)),
        ("example.com.:993", ("example.com", 993)),
        ("[2001:db8::1]:8443", ("2001:db8::1", 8443)),
        ("192.0.2.7", ("192.0.2.7", 443)),
    ],
)
def test_parse_host_port(raw: str, expected: tuple[str, int]) -> None:
    assert parse_host_port(raw) == expected


@pytest.mark.parametrize("raw", ["", "bad host", "example.com:0", "example.com:99999", "http://example.com"])
def test_parse_refuses_what_is_not_a_host(raw: str) -> None:
    with pytest.raises(ValueError, match=r"host|port"):
        parse_host_port(raw)


def test_format_host_port() -> None:
    assert format_host_port("example.com", 443) == "example.com:443"
    assert format_host_port("2001:db8::1", 443) == "[2001:db8::1]:443"


def test_only_configured_hosts_are_accepted() -> None:
    settings = Settings.from_env({"CERTIFICATES_PROBE_HOSTS": "a.example.com, b.example.com:993"})
    assert check_target(settings, "a.example.com:443") == ("a.example.com", 443)
    assert check_target(settings, "a.example.com") == ("a.example.com", 443)
    assert check_target(settings, "b.example.com") == ("b.example.com", 993)
    for refused in ("c.example.com:443", "a.example.com:8443", "b.example.com:443"):
        with pytest.raises(Refused, match="configured"):
            check_target(settings, refused)


def test_hosts_are_required_outside_the_fake() -> None:
    with pytest.raises(ValueError, match="CERTIFICATES_PROBE_HOSTS"):
        Settings.from_env({})


def test_describe_a_leaf_alone_is_an_incomplete_chain() -> None:
    leaf = _make("www.example.com", "Some CA", ["www.example.com", "example.com"], 30)
    seen = describe([leaf])
    assert seen["names"] == ["example.com", "www.example.com"]
    assert seen["chain_complete"] is False
    assert seen["self_signed"] is False
    assert describe([leaf, _make("Some CA", "Some CA", [], 900)])["chain_complete"] is True


def test_hostname_matches_wildcards_one_label_deep() -> None:
    assert hostname_matches(["*.example.com"], "www.example.com")
    assert not hostname_matches(["*.example.com"], "a.b.example.com")
    assert not hostname_matches(["example.net"], "example.com")
