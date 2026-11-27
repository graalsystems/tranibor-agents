# SPDX-License-Identifier: Apache-2.0
"""The `dns/dns@1` profile's conformance suite (plugins/dns/conformance), run against this server over the
OVHcloud fake. The `dns_target` fixture (conftest.py) is all the suite needs."""

from __future__ import annotations

from tranibor_dns_conformance import Conformance


class TestDnsProfile(Conformance):
    """Every `test_*` it inherits runs here."""
