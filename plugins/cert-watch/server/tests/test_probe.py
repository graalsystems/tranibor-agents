# SPDX-License-Identifier: Apache-2.0
"""The TLS probe, for real, against a throw-away TLS server on loopback with generated certificates (no
network beyond 127.0.0.1), and the pure helpers around it."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tranibor_cert_watch.config import Settings
from tranibor_cert_watch.errors import Refused, SourceError
from tranibor_cert_watch.probe import TlsProber, check_target, describe, hostname_matches

from .conftest import NOW


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def make_cert(
    subject: str,
    issuer: tuple[str, ec.EllipticCurvePrivateKey] | None = None,
    days: int = 30,
    ca: bool = False,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    signer_name, signer_key = issuer or (subject, key)
    start = datetime.now().astimezone() - timedelta(days=1)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(_name(signer_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName("*.example.com"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
    return builder.sign(signer_key, hashes.SHA256()), key


def pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


class TlsServer:
    def __init__(self, directory: Path, certs: list[x509.Certificate], key: ec.EllipticCurvePrivateKey) -> None:
        chain_file = directory / "chain.pem"
        chain_file.write_bytes(b"".join(pem(c) for c in certs))
        key_file = directory / "key.pem"
        key_file.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(chain_file, key_file)
        self._socket = socket.create_server(("127.0.0.1", 0))
        self._socket.settimeout(0.2)
        self.port: int = self._socket.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                raw, _ = self._socket.accept()
            except OSError:
                continue
            try:
                with self._context.wrap_socket(raw, server_side=True):
                    pass
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        self._socket.close()


def settings_for(port: int) -> Settings:
    return Settings.from_env(
        {
            "CERT_WATCH_KUBE_API_URL": "https://kube.example.com:6443",
            "CERT_WATCH_KUBE_TOKEN": "fake-token-not-real",
            "CERT_WATCH_PROBE_HOSTS": f"127.0.0.1:{port}",
        }
    )


@pytest.fixture
def serve(tmp_path: Path) -> Iterator[object]:
    servers: list[TlsServer] = []

    def start(certs: list[x509.Certificate], key: ec.EllipticCurvePrivateKey) -> TlsServer:
        server = TlsServer(tmp_path, certs, key)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


async def test_a_self_signed_leaf_is_read_but_not_trusted(serve: object) -> None:
    leaf, key = make_cert("probe.example.com")
    server = serve([leaf], key)  # type: ignore[operator]
    result = await TlsProber(settings_for(server.port)).probe("127.0.0.1", server.port)
    assert result["endpoint"] == f"127.0.0.1:{server.port}"
    assert result["subject"] == "CN=probe.example.com" or "probe.example.com" in result["subject"]
    assert result["self_signed"] is True and result["chain_length"] == 1 and result["chain_complete"] is True
    assert result["san_dns"] == ["*.example.com", "localhost"] and result["san_ip"] == ["127.0.0.1"]
    assert result["days_remaining"] in (29, 28) and result["expired"] is False
    assert len(result["sha256"]) == 64 and result["tls_version"].startswith("TLSv1")
    assert result["trusted"] is False and result["trust_error"]


async def test_a_leaf_served_without_its_intermediate_is_an_incomplete_chain(serve: object) -> None:
    ca, ca_key = make_cert("Test Intermediate CA", days=90, ca=True)
    leaf, key = make_cert("probe.example.com", ("Test Intermediate CA", ca_key))
    alone = serve([leaf], key)  # type: ignore[operator]
    result = await TlsProber(settings_for(alone.port)).probe("127.0.0.1", alone.port)
    assert result["self_signed"] is False and result["chain_length"] == 1 and result["chain_complete"] is False
    full = serve([leaf, ca], key)  # type: ignore[operator]
    result = await TlsProber(settings_for(full.port)).probe("127.0.0.1", full.port)
    assert result["chain_length"] == 2 and result["chain_complete"] is True
    assert result["trusted"] is False  # its root is not in any trust store


async def test_nothing_listening_is_a_source_error() -> None:
    free = socket.create_server(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    with pytest.raises(SourceError, match="no TLS handshake"):
        await TlsProber(settings_for(port)).probe("127.0.0.1", port)


def test_describe_flags_an_expired_leaf() -> None:
    leaf, _ = make_cert("old.example.com", days=1)
    der = leaf.public_bytes(serialization.Encoding.DER)
    later = NOW.replace(year=NOW.year + 5)
    view = describe([der], later)
    assert view["expired"] is True and view["days_remaining"] < 0


def test_hostname_matching() -> None:
    assert hostname_matches(["*.example.com"], "a.example.com")
    assert not hostname_matches(["*.example.com"], "example.com")
    assert not hostname_matches([], "a.example.com")


def test_check_target_resolves_only_what_the_configuration_names() -> None:
    s = Settings.from_env(
        {
            "CERT_WATCH_KUBE_API_URL": "https://kube.example.com:6443",
            "CERT_WATCH_KUBE_TOKEN": "fake-token-not-real",
            "CERT_WATCH_PROBE_HOSTS": "a.example.com, b.example.com:8443, 198.51.100.7:443",
        }
    )
    assert check_target(s, "a.example.com") == ("a.example.com", 443)
    assert check_target(s, "b.example.com") == ("b.example.com", 8443)
    assert check_target(s, "B.example.com:8443") == ("b.example.com", 8443)
    assert check_target(s, "198.51.100.7") == ("198.51.100.7", 443)
    for bad in ("b.example.com:443", "c.example.com", "10.0.0.1:443", "a.example.com:x", ""):
        with pytest.raises(Refused):
            check_target(s, bad)
