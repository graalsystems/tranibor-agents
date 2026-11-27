# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

from tranibor_gitops_argocd.summary import resource_change, safe_url, trunc


def test_safe_url_strips_credentials_and_keeps_the_rest() -> None:
    assert safe_url("https://u:p@git.example.com:8443/x/y.git?a=b") == "https://git.example.com:8443/x/y.git?a=b"
    assert safe_url("https://git.example.com/x.git") == "https://git.example.com/x.git"
    assert safe_url(None) is None


def test_trunc() -> None:
    assert trunc("a" * 600).endswith("…") and len(trunc("a" * 600)) == 500


def _item(live: object, target: object, kind: str = "ConfigMap") -> dict[str, object]:
    return {
        "kind": kind,
        "name": "x",
        "namespace": "n",
        "liveState": json.dumps(live),
        "targetState": json.dumps(target),
        "normalizedLiveState": json.dumps(live),
        "predictedLiveState": json.dumps(target),
    }


def test_in_sync_and_bookkeeping_only_differences_are_not_changes() -> None:
    same = {"metadata": {"name": "x"}, "data": {"a": "1"}}
    assert resource_change(_item(same, same)) is None
    noisy = {
        "metadata": {"name": "x", "resourceVersion": "9", "managedFields": [1]},
        "data": {"a": "1"},
        "status": {"z": 1},
    }
    assert resource_change(_item(noisy, same)) is None


def test_missing_and_orphaned() -> None:
    obj = {"data": {"a": "1"}}
    item = {
        "kind": "ConfigMap",
        "name": "x",
        "liveState": "null",
        "targetState": json.dumps(obj),
        "predictedLiveState": json.dumps(obj),
    }
    assert resource_change(item) == {**resource_change(item), "reason": "missing_in_cluster"}  # type: ignore[dict-item]
    orphan = {"kind": "ConfigMap", "name": "x", "liveState": json.dumps(obj), "targetState": "null"}
    change = resource_change(orphan)
    assert change is not None and change["reason"] == "not_in_git"


def test_json_pointer_escaping_and_list_paths() -> None:
    live = {"metadata": {"annotations": {"a/b": "1"}}, "spec": {"ports": [{"port": 80}]}}
    target = {"metadata": {"annotations": {"a/b": "2"}}, "spec": {"ports": [{"port": 81}]}}
    change = resource_change(_item(live, target))
    assert change is not None
    assert [c["path"] for c in change["changes"]] == ["/metadata/annotations/a~1b", "/spec/ports/0/port"]


def test_credential_looking_fields_are_hidden_outside_secrets_too() -> None:
    change = resource_change(_item({"data": {"apiToken": "live-v"}}, {"data": {"apiToken": "git-v"}}))
    assert change is not None and change["changes"][0] == {
        "path": "/data/apiToken",
        "live": "(hidden)",
        "desired": "(hidden)",
    }


def test_long_values_are_cut() -> None:
    change = resource_change(_item({"data": {"a": "x" * 500}}, {"data": {"a": "y"}}))
    assert change is not None and len(change["changes"][0]["live"]) <= 80
