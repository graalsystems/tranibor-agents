# SPDX-License-Identifier: Apache-2.0
"""Opt-in checks against a real Argo CD (see `conftest.py`). Not run in CI."""

from __future__ import annotations

import pytest

from tranibor_gitops_argocd.config import Settings

from ..conftest import Mcp
from .conftest import env, missing


async def test_list_is_restricted_to_the_allow_list(live: Mcp, live_settings: Settings) -> None:
    listed = await live.call("list_applications")
    assert {a["scope"] for a in listed["applications"]} <= set(live_settings.projects)


async def test_reads_of_the_first_application(live: Mcp) -> None:
    """The unconfirmed response shapes: managed-resources states, resource-tree nodes, sync windows, events."""
    listed = await live.call("list_applications", limit=1)
    if not listed["applications"]:
        pytest.skip("the allowed projects hold no Application")
    name = listed["applications"][0]["name"]
    app = await live.call("get_application", name=name)
    assert app["sync_status"] in ("Synced", "OutOfSync", "Unknown")
    tree = await live.call("list_resources", name=name)
    assert tree["total"] >= 0 and all(n["kind"] for n in tree["nodes"])
    diff = await live.call("get_diff", name=name)
    assert diff["managed_total"] >= 0
    assert "password" not in str(diff).lower() or "(hidden)" in str(diff)
    assert "can_sync" in await live.call("get_sync_windows", name=name)
    await live.call("list_events", name=name)
    await live.call("get_history", name=name)
    plan = await live.call("plan_sync", name=name)
    assert plan["mode"] == "full"


async def test_refresh_changes_nothing_in_the_cluster(live: Mcp) -> None:
    listed = await live.call("list_applications", limit=1)
    if not listed["applications"]:
        pytest.skip("the allowed projects hold no Application")
    out = await live.call("refresh_application", name=listed["applications"][0]["name"])
    assert out["refresh"] == "normal"


async def test_an_application_of_another_project_is_refused(live: Mcp) -> None:
    error = await live.error("list_applications", scope="definitely-not-allowed-project")
    assert "allow-list" in error


async def test_full_sync_of_the_throwaway_application(live: Mcp) -> None:
    name = env("SYNC_APP")
    if not name:
        missing("TRANIBOR_LIVE_ARGOCD_SYNC_APP (a throwaway Application)")
    before = await live.call("get_application", name=name)
    if before["operation_in_progress"]:
        pytest.skip("an operation is already running on the throwaway Application")
    out = await live.call("sync_application", name=name, action_key="live-test")
    assert out["mode"] == "full" and out["prune"] is False
