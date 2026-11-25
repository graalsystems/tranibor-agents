# SPDX-License-Identifier: Apache-2.0
"""Conformance suite of the `backup/backup@1` profile (plugins/backup/profiles/backup.yaml).

An implementation's own tests run it:

    # tests/conftest.py
    @pytest.fixture
    def backup_target(server, runtime): return McpTarget(server, ...)

    # tests/test_conformance.py
    from tranibor_backup_conformance import Conformance
    class TestBackupProfile(Conformance): ...

It needs the implementation's fake (or a throwaway live environment), never the platform.
"""

from .profile import load_profile
from .suite import Conformance
from .target import McpTarget, Target, ToolFailed

__all__ = ["Conformance", "McpTarget", "Target", "ToolFailed", "load_profile"]
