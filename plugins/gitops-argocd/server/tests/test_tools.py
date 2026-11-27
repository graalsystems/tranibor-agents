# SPDX-License-Identifier: Apache-2.0
"""The tools on the seeded fake: reads, the project allow-list, the diagnosis cases and the four writes."""

from __future__ import annotations

import json

from tranibor_gitops_argocd.fake import REV_LAST, REV_NEW, REV_OLD, FakeArgoCD

from .conftest import Mcp


def _writes(fake: FakeArgoCD) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _ in fake.requests if m != "GET"]


async def test_list_is_restricted_to_the_allow_list(mcp: Mcp, fake: FakeArgoCD) -> None:
    listed = await mcp.call("list_applications")
    names = {a["name"] for a in listed["applications"]}
    assert "platform-dns" not in names and {"shop-web", "billing-ledger"} <= names
    assert listed["scopes"] == ["billing", "shop"]
    out = await mcp.call("list_applications", sync_status="OutOfSync", scope="shop")
    assert {a["name"] for a in out["applications"]} >= {"shop-api", "shop-worker", "shop-cron"}
    assert all(a["scope"] == "shop" for a in out["applications"])
    assert "refused" in await mcp.error("list_applications", scope="platform")


async def test_a_server_that_ignores_the_project_filter_is_still_filtered(mcp: Mcp, fake: FakeArgoCD) -> None:
    fake.honour_project_filter = False
    names = {a["name"] for a in (await mcp.call("list_applications"))["applications"]}
    assert "platform-dns" not in names and "shop-web" in names


async def test_every_tool_refuses_an_application_of_another_project(mcp: Mcp, fake: FakeArgoCD) -> None:
    calls: list[tuple[str, dict[str, object]]] = [
        ("get_application", {}),
        ("list_resources", {}),
        ("get_diff", {}),
        ("list_events", {}),
        ("get_sync_windows", {}),
        ("get_history", {}),
        ("get_revision_metadata", {"revision": REV_LAST}),
        ("plan_sync", {}),
        ("refresh_application", {}),
        ("sync_application", {}),
        ("set_auto_sync", {"enabled": False}),
        ("rollback_application", {"history_id": 1}),
    ]
    for tool, arguments in calls:
        error = await mcp.error(tool, name="platform-dns", **arguments)
        assert "allow-list" in error, tool
    assert _writes(fake) == []
    # Nothing was refreshed either: the project is checked before the refresh is sent.
    assert not any(p.endswith("platform-dns") and "refresh" in str(b) for _, p, b in fake.requests)


async def test_an_unknown_application_is_an_error_not_a_crash(mcp: Mcp) -> None:
    assert "404" in await mcp.error("get_application", name="nope")
    assert "no slash" in await mcp.error("get_application", name="a/b")


async def test_get_application_reports_the_state_a_diagnosis_needs(mcp: Mcp) -> None:
    app = await mcp.call("get_application", name="shop-api")
    assert (app["sync_status"], app["health"], app["scope"], app["auto_sync"]) == (
        "OutOfSync",
        "Healthy",
        "shop",
        False,
    )
    assert app["revision_moved"] is False  # drift: git did not move
    assert app["resources_not_synced"][0]["name"] == "shop-api"
    assert app["history"][0]["id"] == 2 and app["history_note"]


async def test_drift_versus_pending_commit(mcp: Mcp) -> None:
    drift = await mcp.call("get_diff", name="shop-api")
    change = drift["resources"][0]
    assert change["reason"] == "differs"
    assert change["changes"][0]["path"] == "/spec/template/spec/containers/0/image"
    assert "hotfix" in change["changes"][0]["live"] and "2.0.0" in change["changes"][0]["desired"]
    pending = await mcp.call("get_application", name="shop-worker")
    assert pending["revision_moved"] is True
    assert (pending["last_deployed_revision"], pending["compared_revision"]) == (REV_LAST, REV_NEW)


async def test_a_lingering_selective_sync_is_surfaced(mcp: Mcp) -> None:
    app = await mcp.call("get_application", name="shop-cron")
    lingering = app["lingering_partial_sync"]
    assert lingering["detected"] is True
    assert lingering["resources"] == [
        {"group": "", "kind": "ConfigMap", "namespace": "prod", "name": "shop-cron-config"}
    ]
    assert app["partial_sync_recorded"] is True
    # A healthy application shows none.
    assert (await mcp.call("get_application", name="shop-web"))["lingering_partial_sync"] is None
    listed = {a["name"]: a for a in (await mcp.call("list_applications"))["applications"]}
    assert listed["shop-cron"]["partial_sync_recorded"] is True


async def test_a_failed_hook_is_surfaced_with_its_message(mcp: Mcp) -> None:
    app = await mcp.call("get_application", name="shop-front")
    state = app["operation_state"]
    assert state["phase"] == "Failed" and state["retry_count"] == 2
    hook = state["failed_resources"][0]
    assert (hook["kind"], hook["hook_type"], hook["hook_phase"]) == ("Job", "PreSync", "Failed")
    events = await mcp.call("list_events", name="shop-front")
    assert events["warnings"] == 1 and events["events"][0]["reason"] == "BackoffLimitExceeded"


async def test_controller_owned_noise_is_flagged(mcp: Mcp) -> None:
    diff = await mcp.call("get_diff", name="billing-ledger")
    change = diff["resources"][0]
    assert change["changes"][0]["path"] == "/spec/replicas"
    assert change["all_changes_look_controller_owned"] is True and change["noise_hints"]
    real = (await mcp.call("get_diff", name="shop-api"))["resources"][0]
    assert real["all_changes_look_controller_owned"] is False


async def test_secret_values_and_repo_credentials_never_come_out(mcp: Mcp) -> None:
    diff = await mcp.call("get_diff", name="billing-reports")
    text = json.dumps(diff)
    assert "bm90LXRoZS1zYW1l" not in text and "c3VwZXItZmFrZS1wYXNzd29yZA" not in text
    assert diff["resources"][0]["changes"][0]["live"] == "(hidden)"
    app = await mcp.call("get_application", name="billing-ledger")
    assert app["repo"] == "https://git.example.com/billing/deploy.git"
    assert "not-a-real-token" not in json.dumps(await mcp.call("list_applications"))


async def test_tree_history_windows_and_revision(mcp: Mcp) -> None:
    tree = await mcp.call("list_resources", name="shop-front", unhealthy_only=True)
    assert [n["name"] for n in tree["nodes"]] == ["shop-front"]
    history = await mcp.call("get_history", name="shop-web")
    assert history["rollback_possible"] is True and history["history"][0]["id"] == 2
    assert (await mcp.call("get_history", name="billing-ledger"))["rollback_possible"] is False
    windows = await mcp.call("get_sync_windows", name="billing-reports")
    assert windows["can_sync"] is False and windows["active_windows"][0]["kind"] == "deny"
    meta = await mcp.call("get_revision_metadata", name="shop-web", revision=REV_LAST)
    assert meta["author"].startswith("Alice") and "planted line" in meta["message"]


async def test_dry_run_writes_nothing_and_keeps_the_operation_state(mcp: Mcp, fake: FakeArgoCD) -> None:
    before = json.dumps(fake.apps["shop-cron"]["status"]["operationState"])
    plan = await mcp.call("plan_sync", name="shop-cron")
    assert plan["mode"] == "full" and plan["would_update"] == ["Deployment/shop-cron"]
    assert any("selective sync" in w for w in plan["warnings"])
    assert _writes(fake) == []
    assert json.dumps(fake.apps["shop-cron"]["status"]["operationState"]) == before
    blocked = await mcp.call("plan_sync", name="billing-reports")
    assert blocked["blocked"] == ["a sync window denies syncs now"]
    sel = await mcp.call("plan_sync", name="shop-cron", resources=[{"kind": "ConfigMap", "name": "shop-cron-config"}])
    assert sel["mode"] == "selective" and sel["would_update"] == []


async def test_refresh_is_sent_and_changes_no_state(mcp: Mcp, fake: FakeArgoCD) -> None:
    out = await mcp.call("refresh_application", name="shop-api", mode="hard")
    assert out["refresh"] == "hard"
    assert ("GET", "/api/v1/applications/shop-api") in [(m, p) for m, p, _ in fake.requests]
    assert _writes(fake) == []
    assert await mcp.error("refresh_application", name="shop-api", mode="weird")


async def test_full_sync_is_the_default_and_prune_is_off(mcp: Mcp, fake: FakeArgoCD) -> None:
    out = await mcp.call("sync_application", name="shop-api", action_key="k-1")
    assert (out["mode"], out["prune"], out["resources"]) == ("full", False, [])
    body = next(b for m, p, b in fake.requests if m == "POST" and p.endswith("/sync"))
    assert body["prune"] is False and body["dryRun"] is False and "resources" not in body
    assert body["infos"] == [{"name": "action_key", "value": "k-1"}]
    assert (await mcp.call("get_application", name="shop-api"))["sync_status"] == "Synced"
    assert out["previous_history_id"] == 2
    assert (await mcp.call("get_history", name="shop-api"))["history"][0]["id"] == 3


async def test_sync_is_idempotent_through_action_key(mcp: Mcp, fake: FakeArgoCD) -> None:
    await mcp.call("sync_application", name="shop-api", action_key="k-2")
    again = await mcp.call("sync_application", name="shop-api", action_key="k-2")
    assert again["already_applied"] is True
    assert len([1 for m, p, _ in fake.requests if m == "POST" and p.endswith("/sync")]) == 1


async def test_selective_sync_needs_a_justification_and_is_checked(mcp: Mcp, fake: FakeArgoCD) -> None:
    ref = [{"kind": "Deployment", "name": "shop-cron"}]
    assert "justification" in await mcp.error("sync_application", name="shop-cron", resources=ref)
    assert "justification" in await mcp.error(
        "sync_application", name="shop-cron", resources=ref, justification="short"
    )
    assert "not a resource" in await mcp.error(
        "sync_application",
        name="shop-cron",
        resources=[{"kind": "Secret", "name": "nope"}],
        justification="the secret rotation must go out alone, hooks are not needed",
    )
    assert _writes(fake) == []
    out = await mcp.call(
        "sync_application",
        name="shop-cron",
        resources=ref,
        justification="only the deployment image moved and the rest is mid-review",
    )
    assert out["mode"] == "selective" and out["resources"][0]["namespace"] == "prod"
    assert any("not recorded in the history" in w for w in out["warnings"])
    # The trap the steward looks for is now recorded; the history did not grow.
    app = await mcp.call("get_application", name="shop-cron")
    assert app["lingering_partial_sync"]["resources"][0]["name"] == "shop-cron"
    assert app["history"][0]["id"] == 2


async def test_sync_refuses_a_running_operation_and_a_deny_window(mcp: Mcp, fake: FakeArgoCD) -> None:
    assert "already running" in await mcp.error("sync_application", name="shop-jobs")
    assert "sync window" in await mcp.error("sync_application", name="billing-reports")
    assert _writes(fake) == []


async def test_prune_is_passed_only_when_asked(mcp: Mcp, fake: FakeArgoCD) -> None:
    await mcp.call("sync_application", name="shop-api", prune=True)
    body = next(b for m, p, b in fake.requests if m == "POST")
    assert body["prune"] is True


async def test_rollback_names_what_undoes_it(mcp: Mcp, fake: FakeArgoCD) -> None:
    out = await mcp.call("rollback_application", name="shop-web", history_id=1)
    assert (out["id"], out["previous_id"], out["previous_revision"]) == (1, 2, REV_LAST)
    assert (await mcp.call("get_history", name="shop-web"))["history"][0]["id"] == 3
    # Git still points at REV_LAST: what runs is no longer what git says.
    assert (await mcp.call("get_application", name="shop-web"))["sync_status"] == "OutOfSync"
    # The undo: a sync to the revision that was in force, with the arguments the platform renders (strings).
    undo = await mcp.call("sync_application", name="shop-web", revision=out["previous_revision"], prune="false")
    assert undo["revision"] == REV_LAST and undo["prune"] is False
    after = await mcp.call("get_application", name="shop-web")
    assert after["history"][0]["revision"] == REV_LAST and after["sync_status"] == "Synced"
    body = next(b for m, p, b in fake.requests if m == "POST" and p.endswith("/sync"))
    assert body["revision"] == REV_LAST


async def test_a_sync_to_a_revision_is_refused_where_argo_cd_would_refuse_it(mcp: Mcp, fake: FakeArgoCD) -> None:
    assert "automated sync" in await mcp.error("sync_application", name="billing-ledger", revision=REV_OLD)
    ref = [{"kind": "Deployment", "name": "shop-cron"}]
    assert "full sync" in await mcp.error(
        "sync_application",
        name="shop-cron",
        resources=ref,
        revision=REV_OLD,
        justification="only the deployment image moved and the rest is mid-review",
    )
    assert "slash" in await mcp.error("sync_application", name="shop-api", revision="a/b")
    assert _writes(fake) == []


async def test_rollback_refusals(mcp: Mcp, fake: FakeArgoCD) -> None:
    assert "automated sync" in await mcp.error("rollback_application", name="billing-ledger", history_id=1)
    assert "not in this application's history" in await mcp.error("rollback_application", name="shop-web", history_id=9)
    assert "already the latest" in await mcp.error("rollback_application", name="shop-web", history_id=2)
    assert "already running" in await mcp.error("rollback_application", name="shop-jobs", history_id=1)
    assert _writes(fake) == []


async def test_set_auto_sync_pauses_keeps_the_settings_and_resumes_with_them(mcp: Mcp, fake: FakeArgoCD) -> None:
    fake.apps["billing-ledger"]["spec"]["syncPolicy"]["automated"] = {"prune": True, "selfHeal": True}
    paused = await mcp.call("set_auto_sync", name="billing-ledger", enabled=False)
    assert (paused["changed"], paused["enabled"], paused["previous_enabled"]) == (True, False, True)
    assert any("app-of-apps" in w for w in paused["warnings"])
    patch = next(b for m, p, b in fake.requests if m == "PATCH")
    assert patch["patchType"] == "merge" and patch["name"] == "billing-ledger"
    assert json.loads(patch["patch"])["spec"] == {"syncPolicy": {"automated": None}}
    app = fake.apps["billing-ledger"]
    assert "automated" not in app["spec"]["syncPolicy"]
    assert json.loads(app["metadata"]["annotations"]["tranibor.io/paused-automated"]) == {
        "prune": True,
        "selfHeal": True,
    }
    assert (await mcp.call("get_history", name="billing-ledger"))["rollback_possible"] is True

    again = await mcp.call("set_auto_sync", name="billing-ledger", enabled=False)
    assert again["changed"] is False and len([1 for m, _, _ in fake.requests if m == "PATCH"]) == 1

    # The undo the descriptor declares: `enabled` arrives as text.
    resumed = await mcp.call("set_auto_sync", name="billing-ledger", enabled="true")
    assert (resumed["changed"], resumed["enabled"], resumed["previous_enabled"]) == (True, True, False)
    assert app["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
    assert "tranibor.io/paused-automated" not in app["metadata"]["annotations"]
    assert any("OutOfSync" in w for w in resumed["warnings"])
    assert not any("plain default" in w for w in resumed["warnings"])


async def test_resuming_without_saved_settings_says_so(mcp: Mcp, fake: FakeArgoCD) -> None:
    out = await mcp.call("set_auto_sync", name="shop-api", enabled=True)
    assert out["changed"] is True and any("plain default" in w for w in out["warnings"])
    assert fake.apps["shop-api"]["spec"]["syncPolicy"]["automated"] == {}


async def test_the_patch_only_ever_touches_the_sync_policy_and_its_annotation(mcp: Mcp, fake: FakeArgoCD) -> None:
    await mcp.call("set_auto_sync", name="shop-api", enabled=True)
    await mcp.call("set_auto_sync", name="shop-api", enabled=False)
    for method, _, body in fake.requests:
        if method == "PATCH":
            patch = json.loads(body["patch"])
            assert set(patch) == {"metadata", "spec"}
            assert set(patch["metadata"]) == {"annotations"} and set(patch["spec"]) == {"syncPolicy"}
            assert set(patch["spec"]["syncPolicy"]) == {"automated"}


async def test_get_provider_names_the_instance(mcp: Mcp) -> None:
    assert await mcp.call("get_provider") == {
        "instance": "gitops-argocd",
        "provider": "argocd",
        "scopes": ["billing", "shop"],
    }


async def test_no_tool_deletes_edits_or_administers(mcp: Mcp) -> None:
    names = await mcp.tool_names()
    assert names == {
        "get_provider", "list_applications", "get_application", "list_resources", "get_diff", "list_events",
        "get_sync_windows", "get_history", "get_revision_metadata", "plan_sync", "refresh_application",
        "sync_application", "rollback_application", "set_auto_sync",
    }  # fmt: skip
    assert not [
        n for n in names if any(w in n for w in ("delete", "patch", "update", "create", "project", "rbac", "terminate"))
    ]
