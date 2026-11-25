# SPDX-License-Identifier: Apache-2.0
"""Conformance suite of the `dns/dns@1` profile (plugins/dns/profiles/dns.yaml).

An implementation's own tests run it:

    # tests/conftest.py
    @pytest.fixture
    def dns_target(server, runtime): return McpTarget(server, ...)

    # tests/test_conformance.py
    from tranibor_dns_conformance import Conformance
    class TestDnsProfile(Conformance): ...

It needs the implementation's fake (or a throwaway live zone), never the platform.
"""

from .profile import load_profile
from .suite import Conformance
from .target import McpTarget, Target, ToolFailed

__all__ = ["Conformance", "McpTarget", "Target", "ToolFailed", "load_profile"]
