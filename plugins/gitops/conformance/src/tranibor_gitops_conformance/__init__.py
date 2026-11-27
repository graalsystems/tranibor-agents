# SPDX-License-Identifier: Apache-2.0
"""Conformance suite of the `gitops/gitops@1` profile (plugins/gitops/profiles/gitops.yaml).

An implementation's own tests run it:

    # tests/conftest.py
    @pytest.fixture
    def gitops_target(server, runtime): return McpTarget(server, ...)

    # tests/test_conformance.py
    from tranibor_gitops_conformance import Conformance
    class TestGitopsProfile(Conformance): ...

It needs the implementation's fake (or throwaway applications on a live controller), never the platform.
"""

from .profile import load_profile
from .suite import Conformance
from .target import McpTarget, Target, ToolFailed

__all__ = ["Conformance", "McpTarget", "Target", "ToolFailed", "load_profile"]
