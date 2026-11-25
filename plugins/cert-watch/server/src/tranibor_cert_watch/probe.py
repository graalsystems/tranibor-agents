# SPDX-License-Identifier: Apache-2.0
"""A live TLS probe: what a client connecting to `host:port` would be served.

Only hosts named in CERT_WATCH_PROBE_HOSTS are ever dialled. The handshake is made with verification OFF
(`CERT_NONE`) on purpose, to be able to read an expired or mis-chained certificate; trust is then judged
separately, in a second handshake that verifies against the system roots, and reported as `trusted`.
No application data is sent.

The served chain: Python 3.12 has no public API for it (`SSLSocket.get_unverified_chain` arrives in 3.13).
The private `_sslobj.get_unverified_chain()` exists in 3.12 (CPython `Modules/_ssl.c`) but is undocumented:
it is used when present, and `chain_complete` is `None` (unknown) when it is not, never a guess.
"""

from __future__ import annotations

import _ssl
import asyncio
import socket
import ssl
from datetime import UTC, datetime
from typing import Any, Protocol

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtensionOID, NameOID

from .config import Settings, format_host_port, parse_host_port
from .errors import Refused, SourceError
from .views import clip, days_until, iso

TIMEOUT = 8.0


class Prober(Protocol):
    async def probe(self, host: str, port: int) -> dict[str, Any]: ...


def _name(name: x509.Name) -> str:
    cn = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    org = name.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    return clip(", ".join(str(a.value) for a in (*cn, *org)) or name.rfc4514_string(), 300)


def describe(der_chain: list[bytes], now: datetime) -> dict[str, Any]:
    """The leaf's facts and whether the chain served ends at a self-signed root or at a certificate a client
    must already know (so the server did not send the intermediates it should have)."""
    certs = [x509.load_der_x509_certificate(der) for der in der_chain]
    leaf = certs[0]
    try:
        sans = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        if not isinstance(sans, x509.SubjectAlternativeName):
            raise x509.ExtensionNotFound("not a SAN", ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        names = [str(n) for n in sans.get_values_for_type(x509.DNSName)]
        addresses = [str(a) for a in sans.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names, addresses = [], []
    not_after = leaf.not_valid_after_utc
    served = len(certs)
    ends_self_signed = certs[-1].issuer == certs[-1].subject
    # A leaf that is itself self-signed is complete by definition; otherwise the last certificate served
    # should be signed by something a client holds as a root, so we only call it incomplete when the
    # served chain is the leaf alone (the common mistake: the intermediate was left out).
    complete = ends_self_signed or served > 1
    return {
        "subject": _name(leaf.subject),
        "issuer": _name(leaf.issuer),
        "serial": format(leaf.serial_number, "x"),
        "san_dns": sorted(names),
        "san_ip": sorted(addresses),
        "not_before": iso(leaf.not_valid_before_utc),
        "not_after": iso(not_after),
        "days_remaining": days_until(not_after, now),
        "expired": not_after < now,
        "self_signed": leaf.issuer == leaf.subject,
        "sha256": leaf.fingerprint(hashes.SHA256()).hex(),
        "chain_length": served,
        "chain_complete": complete,
    }


def hostname_matches(names: list[str], host: str) -> bool:
    from .ct import covers

    return any(covers(n, host) for n in names)


class TlsProber:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def probe(self, host: str, port: int) -> dict[str, Any]:
        return await asyncio.to_thread(self._probe_sync, host, port)

    def _handshake(self, host: str, port: int, verify: bool) -> tuple[list[bytes], str | None, str | None]:
        context = ssl.create_default_context()
        if not verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        with (
            socket.create_connection((host, port), timeout=TIMEOUT) as raw,
            context.wrap_socket(raw, server_hostname=host) as tls,
        ):
            chain: list[bytes] = []
            sslobj = getattr(tls, "_sslobj", None)
            getter = getattr(sslobj, "get_unverified_chain", None)
            if callable(getter):
                chain = [cert.public_bytes(_ssl.ENCODING_DER) for cert in getter()]
            if not chain:
                leaf = tls.getpeercert(binary_form=True)
                chain = [leaf] if leaf else []
            return chain, tls.version(), None

    def _probe_sync(self, host: str, port: int) -> dict[str, Any]:
        now = datetime.now(UTC)
        try:
            chain, version, _ = self._handshake(host, port, verify=False)
        except (OSError, ssl.SSLError) as error:
            raise SourceError(f"{format_host_port(host, port)}: no TLS handshake ({type(error).__name__})") from error
        if not chain:
            raise SourceError(f"{format_host_port(host, port)}: the server presented no certificate")
        view = describe(chain, now)
        trusted, trust_error = True, None
        try:
            self._handshake(host, port, verify=True)
        except ssl.SSLCertVerificationError as error:
            trusted, trust_error = False, clip(error.verify_message or str(error), 200)
        except (OSError, ssl.SSLError) as error:
            trusted, trust_error = False, clip(type(error).__name__, 100)
        return {
            "endpoint": format_host_port(host, port),
            "tls_version": version,
            **view,
            "hostname_matches": hostname_matches(view["san_dns"], host),
            "trusted": trusted,
            "trust_error": trust_error,
        }


def check_target(settings: Settings, target: str) -> tuple[str, int]:
    """`host` or `host:port`, and it must be one the configuration names (an unqualified host takes the
    port its configured entry has)."""
    try:
        host, port = parse_host_port(target)
    except ValueError as error:
        raise Refused(str(error)) from error
    allowed = settings.probe_hosts
    if (host, port) in allowed:
        return host, port
    if ":" not in target.strip("[]") and not target.strip().endswith("]"):
        matches = [entry for entry in allowed if entry[0] == host]
        if len(matches) == 1:
            return matches[0]
    raise Refused(f"{target!r} is not one of the configured probe hosts (CERT_WATCH_PROBE_HOSTS)")
