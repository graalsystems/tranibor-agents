# SPDX-License-Identifier: Apache-2.0
"""The `backup/backup@1` profile's conformance suite (plugins/backup/conformance), run against this server over
the Velero fake. The `backup_target` fixture (conftest.py) is all the suite needs."""

from __future__ import annotations

from tranibor_backup_conformance import Conformance


class TestBackupProfile(Conformance):
    """Every `test_*` it inherits runs here."""
