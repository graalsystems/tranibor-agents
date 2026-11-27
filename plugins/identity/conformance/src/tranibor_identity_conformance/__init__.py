# SPDX-License-Identifier: Apache-2.0
"""Conformance suite of the `identity/identity@1` profile (plugins/identity/profiles/identity.yaml).

An implementation's own tests run it:

    # tests/conftest.py
    @pytest.fixture
    def identity_target(server, runtime): return McpTarget(server, ...)

    # tests/test_conformance.py
    from tranibor_identity_conformance import Conformance
    class TestIdentityProfile(Conformance): ...

It needs the implementation's fake (or a throwaway live directory), never the platform.
"""

from .profile import load_profile
from .suite import Conformance
from .target import McpTarget, Target, ToolFailed

__all__ = ["Conformance", "McpTarget", "Target", "ToolFailed", "load_profile"]
