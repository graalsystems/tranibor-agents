# SPDX-License-Identifier: Apache-2.0
"""The `identity/identity@1` profile's conformance suite (plugins/identity/conformance), run against this
server over the Keycloak fake. The `identity_target` fixture (conftest.py) is all the suite needs."""

from __future__ import annotations

from tranibor_identity_conformance import Conformance


class TestIdentityProfile(Conformance):
    """Every `test_*` it inherits runs here."""
