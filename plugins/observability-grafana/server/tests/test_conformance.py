# SPDX-License-Identifier: Apache-2.0
"""The `observability/observability@1` profile's conformance suite (plugins/observability/conformance), run
against this server over the Grafana fake. The `observability_target` fixture (conftest.py) is all it needs."""

from __future__ import annotations

from tranibor_observability_conformance import Conformance


class TestObservabilityProfile(Conformance):
    """Every `test_*` it inherits runs here."""
