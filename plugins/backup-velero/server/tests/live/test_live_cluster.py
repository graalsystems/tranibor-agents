# SPDX-License-Identifier: Apache-2.0
"""Reads and one restore against a real cluster; see conftest.py. Never run by CI."""

from __future__ import annotations

import os

import pytest

from tranibor_backup_velero.tools import Tools


async def test_the_reads_answer(live: Tools) -> None:
    schedules = await live.list_schedules()
    assert isinstance(schedules["schedules"], list)
    assert (await live.list_locations())["locations"]
    assert "schedules" in await live.backup_freshness()
    assert "backups" in await live.list_backups(limit=5)
    assert "restores" in await live.list_restores(limit=5)


async def test_a_test_restore_round_trip(live: Tools) -> None:
    backup = os.environ.get("TRANIBOR_LIVE_VELERO_BACKUP", "")
    source = os.environ.get("TRANIBOR_LIVE_VELERO_SOURCE_NS", "")
    if not (backup and source):
        pytest.skip("TRANIBOR_LIVE_VELERO_BACKUP and _SOURCE_NS are not set: reads only")
    done = await live.run_restore_test(backup, source, name="live", action_key="live-run")
    try:
        assert str(done["target"]).startswith(live.settings.restore_namespace_prefix)
        again = await live.run_restore_test(backup, source, name="live", action_key="live-run")
        assert again["already_existed"] is True
        assert (await live.get_restore(str(done["restore_name"])))["restore"]["name"] == done["restore_name"]
    finally:
        await live.cleanup_restore_test(str(done["target"]))
