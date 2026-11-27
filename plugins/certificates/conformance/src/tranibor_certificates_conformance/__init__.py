# SPDX-License-Identifier: Apache-2.0
"""Conformance suite of the `certificates/certificates@1` profile (plugins/certificates/profiles/certificates.yaml).

An implementation's own tests run it:

    # tests/conftest.py
    @pytest.fixture
    def certificates_target(server, runtime): return McpTarget(server, ...)

    # tests/test_conformance.py
    from tranibor_certificates_conformance import Conformance
    class TestCertificatesProfile(Conformance): ...

It needs the implementation's fake (or a throwaway live source), never the platform. The profile is read-only:
the suite writes nothing anywhere.
"""

from .profile import load_profile
from .suite import Conformance
from .target import McpTarget, Target, ToolFailed

__all__ = ["Conformance", "McpTarget", "Target", "ToolFailed", "load_profile"]
