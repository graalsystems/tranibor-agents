# SPDX-License-Identifier: Apache-2.0
"""Conformance suite of the `lan/lan@1` profile (plugins/lan/profiles/lan.yaml).

An implementation's own tests run it:

    # tests/conftest.py
    @pytest.fixture
    def lan_target(server, runtime): return McpTarget(server, ...)

    # tests/test_conformance.py
    from tranibor_lan_conformance import Conformance
    class TestLanProfile(Conformance): ...

It needs the implementation's fake (or a throwaway lab site), never the platform.
"""

from .profile import load_profile
from .suite import Conformance
from .target import McpTarget, Target, ToolFailed

__all__ = ["Conformance", "McpTarget", "Target", "ToolFailed", "load_profile"]
