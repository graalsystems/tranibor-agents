# SPDX-License-Identifier: Apache-2.0
"""`servers/backup-velero.yaml` and the server agree: what is served is declared, with its access and undo."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from starlette.testclient import TestClient

from tranibor_backup_velero.app import build_app
from tranibor_backup_velero.config import Settings

from .conftest import Mcp

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "backup-velero.yaml"
READS = {
    "get_provider",
    "list_schedules",
    "list_backups",
    "get_backup",
    "list_locations",
    "list_restores",
    "get_restore",
    "backup_freshness",
    "storage_inventory",
}
WRITES = {"trigger_backup", "run_restore_test", "cleanup_restore_test"}
GOOD: dict[str, Any] = {
    "kube_api_url": "https://kubernetes.example.com:6443",
    "kube_token": "not-a-real-token",
    "restore_namespace_prefix": "restore-test-",
}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


def errors(config: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(descriptor()["config_schema"])
    return [e.message for e in validator.iter_errors(config)]


def test_every_served_tool_is_declared_with_its_access(mcp: Mcp) -> None:
    served = {tool["name"] for tool in mcp.rpc("tools/list", {})["tools"]}
    assert served == set(operations()) == READS | WRITES
    for name in READS:
        assert operations()[name]["access"] == "read"
        assert not {"human_approval", "compensation", "platform_arguments"} & set(operations()[name])
    for name in WRITES:
        assert operations()[name]["access"] == "write"


def test_the_writes_are_governed_as_designed() -> None:
    trigger = operations()["trigger_backup"]
    create, delete = operations()["run_restore_test"], operations()["cleanup_restore_test"]
    assert create["platform_arguments"] == ["action_key"]
    assert trigger["platform_arguments"] == ["action_key"] and "human_approval" not in trigger
    assert create["human_approval"] is True  # a restore writes data: a person decides
    assert create["compensation"] == {
        "operation": "cleanup_restore_test",
        "arguments": {"target": "{{ result.target }}"},
    }
    assert delete["human_approval"] is False and "compensation" not in delete


def test_the_compensation_renders_from_what_the_server_returns(mcp: Mcp) -> None:
    done = mcp.call("run_restore_test", backup_id="daily-apps-20261125021700", source="shop")
    assert "target" in done
    assert mcp.call("cleanup_restore_test", target=done["target"])["deleted"] is True


def test_the_agent_argument_of_a_restore_that_the_platform_fills_is_accepted(mcp: Mcp) -> None:
    schema = {t["name"]: t for t in mcp.rpc("tools/list", {})["tools"]}["run_restore_test"]["inputSchema"]
    assert "action_key" in schema["properties"]
    assert set(schema["required"]) == {"backup_id", "source"}


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/backup-velero-mcp@sha256:[0-9a-f]{64}", d["image"])
    assert (d["name"], d["port"], d["protocol"], d["paths"]) == (
        "backup-velero",
        8080,
        "mcp-http",
        {"mcp": "/mcp", "health": "/healthz"},
    )
    assert d["egress"] == {"from_fields": ["kube_api_url", "s3_endpoint"]}
    assert set(d["secret_fields"]) == {"kube_token", "s3_access_key", "s3_secret_key"}


def test_every_field_reaches_the_variable_the_server_reads() -> None:
    props = descriptor()["config_schema"]["properties"]
    env = {name: spec["x-env"] for name, spec in props.items()}
    assert env["kube_api_url"] == "VELERO_KUBE_API_URL"
    source = (Path(__file__).resolve().parents[1] / "src" / "tranibor_backup_velero" / "config.py").read_text()
    for variable in env.values():
        assert f'"{variable}"' in source, variable


def test_the_configuration_is_validated() -> None:
    assert errors(GOOD) == []
    assert errors({**GOOD, "s3_endpoint": "https://s3.example.com", "s3_bucket": "b"}) == []
    assert errors({k: v for k, v in GOOD.items() if k != "restore_namespace_prefix"})
    assert errors({**GOOD, "kube_api_url": "http://kubernetes.example.com"})
    assert errors({**GOOD, "restore_namespace_prefix": "restore"})
    assert errors({**GOOD, "restore_namespace_prefix": "Restore-test-"})
    assert errors({**GOOD, "surprise": "1"})


def test_the_declared_fake_switches_the_server_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"VELERO_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    reached = {p.get("x-env", name.upper()) for name, p in d["config_schema"]["properties"].items()}
    assert not variables & reached
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True


PROFILE = Path(__file__).resolve().parents[3] / "backup" / "profiles" / "backup.yaml"


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            resolved = _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
            return {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
        return {k: _inline(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def test_schemas_are_the_profiles() -> None:
    """The server's input and output schemas are the profile's, `$ref` resolved (the core proves the same)."""
    profile = yaml.safe_load(PROFILE.read_text())
    ops = operations()
    for tool in profile["tools"]:
        op = ops[tool["name"]]
        assert op["input_schema"] == _inline(tool["input"], profile["$defs"]), tool["name"]
        assert op["output_schema"] == _inline(tool["output"], profile["$defs"]), tool["name"]
