# SPDX-License-Identifier: Apache-2.0
"""`servers/keycloak-admin.yaml` and the server agree: what is served is declared, with its governance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from starlette.testclient import TestClient

from tranibor_keycloak_admin.app import build_app
from tranibor_keycloak_admin.config import Settings

from .conftest import Mcp, uid

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "keycloak-admin.yaml"
AGENT = Path(__file__).resolve().parents[2] / "agents" / "iam-steward.yaml"
ALWAYS_A_PERSON = {"create_user", "disable_user", "logout_user", "send_required_actions_email"}
PLATFORM_ARGUMENTS = {"create_user": {"action_key"}, "send_required_actions_email": {"action_key"}}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


def test_every_served_tool_is_declared_and_every_declared_tool_served(mcp: Mcp) -> None:
    served = {tool["name"] for tool in mcp.rpc("tools/list", {})["tools"]}
    assert served == set(operations())


def test_reads_are_reads_and_writes_carry_their_governance() -> None:
    ops = operations()
    writes = {n for n, op in ops.items() if op["access"] == "write"}
    assert writes == {
        "create_user",
        "add_user_to_group",
        "remove_user_from_group",
        "disable_user",
        "enable_user",
        "logout_user",
        "send_required_actions_email",
    }
    for name, op in ops.items():
        if name not in writes:
            assert op["access"] == "read"
            assert not {"human_approval", "compensation", "platform_arguments"} & set(op), name
    approved = {n for n, op in ops.items() if op.get("human_approval") is True}
    assert approved == ALWAYS_A_PERSON
    for name, arguments in PLATFORM_ARGUMENTS.items():
        assert set(ops[name]["platform_arguments"]) == arguments


def test_every_platform_argument_is_a_parameter_of_its_tool(mcp: Mcp) -> None:
    schemas = {t["name"]: t["inputSchema"]["properties"] for t in mcp.rpc("tools/list", {})["tools"]}
    for name, arguments in PLATFORM_ARGUMENTS.items():
        assert arguments <= set(schemas[name]), name


def test_compensations_render_from_the_write_s_own_result(mcp: Mcp) -> None:
    """Each compensation's arguments are Jinja over `result`; render them on a real result of the fake."""
    env = SandboxedEnvironment(undefined=StrictUndefined)
    ops = operations()
    group = mcp.fake.group_ids["finance"]
    results = {
        "add_user_to_group": mcp.call("add_user_to_group", user_id=uid("hugo.faure"), group_id=group),
        "remove_user_from_group": mcp.call("remove_user_from_group", user_id=uid("hugo.faure"), group_id=group),
        "disable_user": mcp.call("disable_user", user_id=uid("alice.martin")),
    }
    inverse = {
        "add_user_to_group": "remove_user_from_group",
        "remove_user_from_group": "add_user_to_group",
        "disable_user": "enable_user",
    }
    for name, result in results.items():
        compensation = ops[name]["compensation"]
        assert compensation["operation"] == inverse[name]
        rendered = {k: env.from_string(v).render(result=result) for k, v in compensation["arguments"].items()}
        again = mcp.call(compensation["operation"], **rendered)
        assert again["user_id"] == result["user_id"]
    assert not [n for n, op in ops.items() if "compensation" in op and op["access"] != "write"]


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/keycloak-admin-mcp@sha256:[0-9a-f]{64}", d["image"])
    assert (d["name"], d["port"], d["protocol"], d["paths"]) == (
        "keycloak",
        8080,
        "mcp-http",
        {"mcp": "/mcp", "health": "/healthz"},
    )
    assert d["egress"] == {"from_fields": ["base_url"]}
    assert d["secret_fields"] == ["client_secret"]
    assert not {"implements", "webhook"} & set(d)


def errors(config: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(descriptor()["config_schema"])
    return [e.message for e in validator.iter_errors(config)]


GOOD = {"base_url": "https://sso.example.com", "realm": "acme", "client_id": "tranibor-iam", "client_secret": "x"}


def test_the_configuration_schema() -> None:
    assert errors(GOOD) == []
    assert errors({**GOOD, "token_realm": "master"}) == []
    assert errors({k: v for k, v in GOOD.items() if k != "client_secret"})
    assert errors({**GOOD, "base_url": "http://sso.example.com"})
    assert errors({**GOOD, "realm": "../master"})
    assert errors({**GOOD, "unknown": "x"})


def test_every_config_field_reaches_the_server_by_its_declared_variable() -> None:
    props = descriptor()["config_schema"]["properties"]
    env = {p["x-env"]: f"v-{n}" for n, p in props.items()}
    env["KEYCLOAK_BASE_URL"] = "https://sso.example.com"
    env["KEYCLOAK_REALM"], env["KEYCLOAK_TOKEN_REALM"] = "acme", "master"
    settings = Settings.from_env({**env, "TRANIBOR_SERVER_TOKEN": "t"})
    assert (settings.client_id, settings.realm, settings.token_realm) == ("v-client_id", "acme", "master")
    assert settings.client_secret.get_secret_value() == "v-client_secret"


def test_the_agent_may_call_exactly_the_tools_the_server_serves() -> None:
    agent = yaml.safe_load(AGENT.read_text())["agent"]["spec"]
    called = set(agent["mcp_servers"][0]["tools"])
    assert agent["mcp_servers"][0]["connector"] == "keycloak"
    assert called == set(operations())
    text = agent["instructions"]
    for name in called:
        assert f"keycloak__{name}" in text or name in {
            "find_users",
            "get_user",
            "list_user_groups",
            "list_login_events",
            "enable_user",
            "create_user",
        }, name


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"KEYCLOAK_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    reached = {p.get("x-env", name.upper()) for name, p in d["config_schema"]["properties"].items()}
    assert not variables & reached
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
