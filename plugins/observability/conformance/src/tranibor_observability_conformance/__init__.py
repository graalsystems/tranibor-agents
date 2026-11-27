# SPDX-License-Identifier: Apache-2.0
"""Conformance suite of the `observability/observability@1` profile (plugins/observability/profiles/observability.yaml).

An implementation's own tests run it:

    # tests/conftest.py
    @pytest.fixture
    def observability_target(server, runtime): return McpTarget(server, ...)

    # tests/test_conformance.py
    from tranibor_observability_conformance import Conformance
    class TestObservabilityProfile(Conformance): ...

It needs the implementation's fake (or a throwaway live stack), never the platform.
"""

from .profile import load_profile
from .suite import Conformance
from .target import McpTarget, Target, ToolFailed

__all__ = ["Conformance", "McpTarget", "Target", "ToolFailed", "load_profile"]
