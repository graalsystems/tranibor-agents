# SPDX-License-Identifier: Apache-2.0
"""The `certificates/certificates@1` profile's conformance suite (plugins/certificates/conformance), run against this
server over its fake. The `certificates_target` fixture (conftest.py) is all the suite needs."""

from __future__ import annotations

from tranibor_certificates_conformance import Conformance


class TestCertificatesProfile(Conformance):
    """Every `test_*` it inherits runs here."""
