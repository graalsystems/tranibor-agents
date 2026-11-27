# SPDX-License-Identifier: Apache-2.0
"""The tools on the seeded fake cluster: what they read, and what the two writes refuse."""

from __future__ import annotations

import pytest

from tranibor_backup_velero.errors import Failure
from tranibor_backup_velero.fake import FakeCluster
from tranibor_backup_velero.tools import Tools

GOOD = "daily-apps-20261125021700"


def by_name(rows: list[dict[str, object]], key: str = "id") -> dict[str, dict[str, object]]:
    return {str(r[key]): r for r in rows}


async def test_list_schedules(tools: Tools) -> None:
    rows = by_name((await tools.list_schedules())["schedules"])
    assert set(rows) == {"daily-apps", "weekly-archive", "hourly-db"}
    daily = rows["daily-apps"]
    assert daily["cron"] == "0 2 * * *"
    assert daily["period_hours"] == 24
    assert daily["next_run"] == "2026-11-26T02:00:00Z"
    assert daily["last_backup"]["status"] == "succeeded"  # type: ignore[index]
    assert rows["hourly-db"]["last_backup"]["status"] == "failed"  # type: ignore[index]


async def test_list_backups_filters_and_orders(tools: Tools) -> None:
    everything = await tools.list_backups()
    assert everything["total"] == 5
    starts = [b["start"] for b in everything["backups"]]
    assert starts == sorted(starts, reverse=True)
    assert [b["id"] for b in (await tools.list_backups(status="failed"))["backups"]] == ["hourly-db-20261125041700"]
    only = await tools.list_backups(schedule="daily-apps", limit=1)
    assert only["total"] == 2 and only["truncated"] is True and len(only["backups"]) == 1


async def test_list_backups_refuses_a_silly_limit(tools: Tools) -> None:
    with pytest.raises(Failure) as error:
        await tools.list_backups(limit=0)
    assert error.value.code == "invalid_arguments"


async def test_get_backup_has_no_logs_url(tools: Tools, fake: FakeCluster) -> None:
    detail = (await tools.get_backup("hourly-db-20261125041700"))["backup"]
    assert detail["status"] == "failed" and detail["provider_status"] == "Failed"
    assert detail["failure_reason"] == "timed out waiting for the volume snapshot"
    assert detail["errors"] == 3
    assert not any("url" in key.lower() or "log" in key.lower() for key in detail)
    assert not any("downloadrequests" in path for _, path in fake.requests)


async def test_get_backup_missing_and_malformed(tools: Tools) -> None:
    with pytest.raises(Failure) as missing:
        await tools.get_backup("nope-20250101000000")
    assert missing.value.code == "not_found"
    with pytest.raises(Failure) as bad:
        await tools.get_backup("../../etc")
    assert bad.value.code == "invalid_arguments"


async def test_list_locations(tools: Tools) -> None:
    rows = by_name((await tools.list_locations())["locations"])
    assert rows["default"]["available"] is True and rows["default"]["default"] is True
    assert rows["offsite"]["available"] is False
    assert "AccessDenied" in str(rows["offsite"]["message"])


async def test_backup_freshness_gives_the_three_cases(tools: Tools) -> None:
    rows = by_name((await tools.backup_freshness())["schedules"], "schedule")
    assert rows["daily-apps"]["freshness"] == "fresh"
    assert rows["daily-apps"]["last_good_backup"] == GOOD
    assert rows["weekly-archive"]["freshness"] == "fresh"
    db = rows["hourly-db"]
    assert db["freshness"] == "stale"
    assert db["age_hours"] == 7.5
    assert db["newest_backup_status"] == "failed"  # a failed backup never counts as a fresh one
    assert db["last_good_backup"] == "hourly-db-20261125014700"


async def test_backup_freshness_with_an_explicit_age(tools: Tools) -> None:
    rows = by_name((await tools.backup_freshness(max_age_hours=6))["schedules"], "schedule")
    assert rows["daily-apps"]["freshness"] == "stale"  # 7 h > 6 h
    assert rows["daily-apps"]["basis"] == "max_age_hours argument"
    with pytest.raises(Failure):
        await tools.backup_freshness(max_age_hours=0)


async def test_list_and_get_restore(tools: Tools) -> None:
    listed = await tools.list_restores()
    assert [r["id"] for r in listed["restores"]] == ["daily-apps-shop-rehearsal"]
    assert listed["restores"][0]["made_by_this_server"] is False
    got = await tools.get_restore("daily-apps-shop-rehearsal")
    assert got["restore"]["items_restored"] == 310
    comparison = got["comparison"]
    # the backup holds shop + blog (480 items): a one-namespace restore is NOT comparable to it
    assert comparison["comparable"] is False
    assert comparison["backup_items"] == 480
    assert "shop" in comparison["reason"]


async def test_storage_inventory(tools: Tools) -> None:
    listing = await tools.storage_inventory()
    assert listing["configured"] is True
    assert listing["object_count"] == 15
    assert listing["newest_object_age_hours"] is not None
    only = await tools.storage_inventory(prefix="backups/daily-apps-20261125021700")
    assert only["object_count"] == 3


async def test_storage_inventory_refuses_dotdot(tools: Tools) -> None:
    with pytest.raises(Failure):
        await tools.storage_inventory(prefix="../other")


# --- the writes ------------------------------------------------------------------------------------------------


async def test_run_restore_test_stays_inside_the_prefix(tools: Tools, fake: FakeCluster) -> None:
    done = await tools.run_restore_test(GOOD, "shop", action_key="key-1")
    assert done["target"] == "restore-test-shop-20261125"
    assert done["already_existed"] is False
    namespace = fake.namespaces["restore-test-shop-20261125"]["metadata"]
    assert namespace["labels"]["tranibor.io/test-restore"] == "true"
    restore = fake.restores[done["restore_id"]]
    assert restore["spec"]["namespaceMapping"] == {"shop": "restore-test-shop-20261125"}
    assert restore["spec"]["includeClusterResources"] is False
    assert restore["spec"]["includedNamespaces"] == ["shop"]
    assert restore["spec"]["restorePVs"] is False
    writes = [(m, p) for m, p in fake.requests if m != "GET"]
    assert writes == [
        ("POST", "/api/v1/namespaces"),
        ("POST", "/apis/velero.io/v1/namespaces/velero/restores"),
    ]


async def test_run_restore_test_is_idempotent_on_the_action_key(tools: Tools, fake: FakeCluster) -> None:
    first = await tools.run_restore_test(GOOD, "shop", action_key="key-1")
    again = await tools.run_restore_test(GOOD, "shop", action_key="key-1")
    assert again["already_existed"] is True
    assert again["restore_id"] == first["restore_id"]
    assert len([m for m, _ in fake.requests if m == "POST"]) == 2  # nothing more was created
    other = await tools.run_restore_test(GOOD, "shop", action_key="key-2")
    assert other["restore_id"] != first["restore_id"]


async def test_a_created_restore_completes_and_compares(tools: Tools) -> None:
    done = await tools.run_restore_test(GOOD, "shop", name="shop-nightly", action_key="k")
    assert done["target"] == "restore-test-shop-nightly-20261125"
    got = await tools.get_restore(str(done["restore_id"]))
    assert got["restore"]["status"] == "succeeded"
    assert got["restore"]["made_by_this_server"] is True
    assert got["restore"]["items_restored"] == 310


async def test_run_restore_test_refuses_what_is_not_restorable(tools: Tools) -> None:
    for backup, code in (("hourly-db-20261125041700", "refused"), ("nope-20250101000000", "not_found")):
        with pytest.raises(Failure) as error:
            await tools.run_restore_test(backup, "shop")
        assert error.value.code == code


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("cache", "invalid_arguments"),  # not a namespace of that backup
        ("restore-test-shop-old", "refused"),  # the source is itself a test namespace
        ("Shop", "invalid_arguments"),
        ("", "invalid_arguments"),
    ],
)
async def test_run_restore_test_validates_the_source(tools: Tools, source: str, code: str) -> None:
    with pytest.raises(Failure) as error:
        await tools.run_restore_test(GOOD, source)
    assert error.value.code == code


async def test_run_restore_test_refuses_a_label_that_escapes(tools: Tools, fake: FakeCluster) -> None:
    for label in ("../kube-system", "A", "a_b", "x" * 80):
        with pytest.raises(Failure):
            await tools.run_restore_test(GOOD, "shop", name=label)
    assert not [r for r in fake.requests if r[0] != "GET"]


async def test_run_restore_test_will_not_take_over_a_foreign_namespace(tools: Tools, fake: FakeCluster) -> None:
    fake.namespaces["restore-test-shop-20261125"] = {"metadata": {"name": "restore-test-shop-20261125", "labels": {}}}
    with pytest.raises(Failure) as error:
        await tools.run_restore_test(GOOD, "shop")
    assert error.value.code == "refused"
    assert not any(m == "POST" for m, _ in fake.requests)


async def test_exclude_resources_are_accepted_as_json_or_csv(tools: Tools, fake: FakeCluster) -> None:
    done = await tools.run_restore_test(GOOD, "shop", exclude='["cronjobs.batch","events"]', action_key="a")
    assert fake.restores[done["restore_id"]]["spec"]["excludedResources"] == ["cronjobs.batch", "events"]
    done = await tools.run_restore_test(GOOD, "shop", exclude="jobs.batch, pods", action_key="b")
    assert fake.restores[done["restore_id"]]["spec"]["excludedResources"] == ["jobs.batch", "pods"]
    with pytest.raises(Failure):
        await tools.run_restore_test(GOOD, "shop", exclude="Not A Resource!", action_key="c")


@pytest.mark.parametrize(
    "namespace",
    ["shop", "kube-system", "velero", "default", "restore-test-", "restore-tes", "Restore-test-x", "restore-test-/x"],
)
async def test_delete_refuses_every_namespace_outside_the_prefix(
    tools: Tools, fake: FakeCluster, namespace: str
) -> None:
    with pytest.raises(Failure) as error:
        await tools.cleanup_restore_test(namespace)
    assert error.value.code in ("refused", "invalid_arguments")
    assert not [r for r in fake.requests if r[0] != "GET"]


async def test_delete_refuses_a_prefixed_namespace_it_did_not_create(tools: Tools, fake: FakeCluster) -> None:
    with pytest.raises(Failure) as error:
        await tools.cleanup_restore_test("restore-test-manual")
    assert error.value.code == "refused"
    assert not [r for r in fake.requests if r[0] == "DELETE"]


async def test_delete_removes_one_it_created_and_is_idempotent(tools: Tools, fake: FakeCluster) -> None:
    done = await tools.run_restore_test(GOOD, "shop", action_key="k")
    gone = await tools.cleanup_restore_test(str(done["target"]))
    assert gone["deleted"] is True
    assert [r for r in fake.requests if r[0] == "DELETE"] == [
        ("DELETE", "/api/v1/namespaces/restore-test-shop-20261125")
    ]
    again = await tools.cleanup_restore_test(str(done["target"]))
    assert again["deleted"] is False and again["already_gone"] is True
    missing = await tools.cleanup_restore_test("restore-test-never-existed")
    assert missing["already_gone"] is True


async def test_nothing_ever_writes_to_a_backup_a_schedule_or_a_source_namespace(
    tools: Tools, fake: FakeCluster
) -> None:
    done = await tools.run_restore_test(GOOD, "shop", action_key="k")
    await tools.cleanup_restore_test(str(done["target"]))
    for method, path in fake.requests:
        if method != "GET":
            assert "/backups" not in path and "/schedules" not in path
            assert path != "/api/v1/namespaces/shop"


async def test_a_prefix_wider_than_the_restore_namespaces_never_reaches_velero(tools: Tools) -> None:
    from tranibor_backup_velero.config import prefix_problem

    assert prefix_problem("velero-", "velero")
    assert prefix_problem("kube-", "velero")
    assert prefix_problem("restore-test-", "velero") is None
