# SPDX-License-Identifier: Apache-2.0
"""`servers/identity-keycloak.yaml` and the server agree. The profile's own checks (governance floor,
compensation by replay, the allow-list) are in the conformance suite; this is what is specific to this
descriptor."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from starlette.testclient import TestClient

from tranibor_identity_keycloak.app import build_app
from tranibor_identity_keycloak.config import Settings

from .conftest import HttpMcp, uid

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR = ROOT / "servers" / "identity-keycloak.yaml"
PROCEDURE = ROOT / "procedures" / "connector.mcp.identity-keycloak.md"
KEYCLOAK_ONLY = {"list_clients", "get_realm_summary"}
ALWAYS_A_PERSON = {"create_user", "add_user_to_group", "disable_user", "enable_user", "logout_user", "send_setup_email"}
PLATFORM_ARGUMENTS = {"create_user": {"action_key"}, "send_setup_email": {"action_key"}}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


def test_the_descriptor_implements_the_profile_and_the_plugin_requires_it() -> None:
    assert descriptor()["implements"] == ["identity/identity@1"]
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert plugin["requires"]["plugins"] == [{"name": "identity", "version": ">=0.1.0,<1.0.0"}]


def test_every_served_tool_is_declared_and_every_declared_tool_served(web: HttpMcp) -> None:
    served = {tool["name"] for tool in web.rpc("tools/list", {})["tools"]}
    assert served == set(operations())
    assert len(served) == 26
    assert not {name for name in served if "delete" in name or "impersonat" in name or "secret" in name}
    assert not {name for name in served if "credential" in name or "password" in name or "realm_setting" in name}


def test_the_keycloak_only_tools_are_the_two_reads_outside_the_profile() -> None:
    profile = yaml.safe_load((ROOT.parent / "identity" / "profiles" / "identity.yaml").read_text())
    names = {t["name"] for t in profile["tools"]}
    assert set(operations()) - names == KEYCLOAK_ONLY
    assert names <= set(operations())
    assert all(operations()[n]["access"] == "read" for n in KEYCLOAK_ONLY)


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
        "send_setup_email",
    }
    for name, op in ops.items():
        if name not in writes:
            assert not {"human_approval", "compensation", "platform_arguments"} & set(op), name
    assert {n for n, op in ops.items() if op.get("human_approval") is True} == ALWAYS_A_PERSON
    for name, arguments in PLATFORM_ARGUMENTS.items():
        assert set(ops[name]["platform_arguments"]) == arguments


def test_every_platform_argument_is_a_parameter_of_its_tool(web: HttpMcp) -> None:
    schemas = {t["name"]: t["inputSchema"]["properties"] for t in web.rpc("tools/list", {})["tools"]}
    for name, arguments in PLATFORM_ARGUMENTS.items():
        assert arguments <= set(schemas[name]), name


def test_compensations_render_from_the_write_s_own_result(web: HttpMcp) -> None:
    """Each compensation's arguments are Jinja over `result`; render them on a real result of the fake."""
    env = SandboxedEnvironment(undefined=StrictUndefined)
    ops = operations()
    group = web.fake.demo.group_ids["finance"]
    results = {
        "add_user_to_group": web.call("add_user_to_group", directory="demo", user_id=uid("hugo.faure"), group_id=group),
        "remove_user_from_group": web.call(
            "remove_user_from_group", directory="demo", user_id=uid("hugo.faure"), group_id=group
        ),
        "disable_user": web.call("disable_user", directory="demo", user_id=uid("alice.martin")),
        "enable_user": web.call("enable_user", directory="demo", user_id=uid("felix.bernard")),
    }
    inverse = {
        "add_user_to_group": "remove_user_from_group",
        "remove_user_from_group": "add_user_to_group",
        "disable_user": "enable_user",
        "enable_user": "disable_user",
    }
    for name, result in results.items():
        compensation = ops[name]["compensation"]
        assert compensation["operation"] == inverse[name]
        rendered = {k: env.from_string(v).render(result=result) for k, v in compensation["arguments"].items()}
        again = web.call(compensation["operation"], **rendered)
        assert again["user_id"] == result["user_id"]
    assert not [n for n, op in ops.items() if "compensation" in op and op["access"] != "write"]


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/identity-keycloak-mcp@sha256:0{64}", d["image"])
    assert (d["name"], d["port"], d["protocol"], d["paths"]) == (
        "identity-keycloak",
        8080,
        "mcp-http",
        {"mcp": "/mcp", "health": "/healthz"},
    )
    assert d["egress"] == {"from_fields": ["base_url"]}
    assert d["secret_fields"] == ["client_secret"]
    properties = d["config_schema"]["properties"]
    assert set(d["config_schema"]["required"]) == {"base_url", "realms", "client_id", "client_secret"}
    assert {p["x-env"] for p in properties.values()} == {
        "KEYCLOAK_BASE_URL",
        "KEYCLOAK_REALMS",
        "KEYCLOAK_CLIENT_ID",
        "KEYCLOAK_CLIENT_SECRET",
        "KEYCLOAK_TOKEN_REALM",
    }
    # Every field tells the administrator where to find the value (ADR 0224 section 5); the config is flat.
    for name, p in properties.items():
        assert p.get("title") and p.get("description") and p.get("x-help") and p.get("x-link"), name
        assert p["type"] in ("string", "array")


def test_the_procedure_describes_every_field() -> None:
    front = yaml.safe_load(PROCEDURE.read_text().split("---")[1])
    assert set(front["fields"]) == set(descriptor()["config_schema"]["properties"])
    assert front["applies_to"] == {"connector": "mcp/identity-keycloak"}


def errors(config: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(descriptor()["config_schema"])
    return [e.message for e in validator.iter_errors(config)]


GOOD = {
    "base_url": "https://sso.example.com",
    "realms": ["staff", "contractors"],
    "client_id": "tranibor-iam",
    "client_secret": "x",
}


def test_the_configuration_schema() -> None:
    assert errors(GOOD) == []
    assert errors({**GOOD, "token_realm": "master"}) == []
    assert errors({k: v for k, v in GOOD.items() if k != "client_secret"})
    assert errors({k: v for k, v in GOOD.items() if k != "realms"})
    assert errors({**GOOD, "realms": []})
    assert errors({**GOOD, "base_url": "http://sso.example.com"})
    assert errors({**GOOD, "realms": ["../master"]})
    assert errors({**GOOD, "unknown": "x"})


def test_every_config_field_reaches_the_server_by_its_declared_variable() -> None:
    props = descriptor()["config_schema"]["properties"]
    env = {p["x-env"]: f"v-{n}" for n, p in props.items()}
    env["KEYCLOAK_BASE_URL"] = "https://sso.example.com"
    env["KEYCLOAK_REALMS"], env["KEYCLOAK_TOKEN_REALM"] = "staff, contractors", "master"
    settings = Settings.from_env({**env, "TRANIBOR_SERVER_TOKEN": "t"})
    assert (settings.client_id, settings.realms, settings.token_realm) == (
        "v-client_id",
        ("staff", "contractors"),
        "master",
    )
    assert settings.client_secret.get_secret_value() == "v-client_secret"
    assert Settings.from_env({**env, "TRANIBOR_INSTANCE_NAME": "idp-eu"}).instance == "idp-eu"


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    d = descriptor()
    assert d["fake"] == {"env": {"KEYCLOAK_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("TRANIBOR_", "CHOREGOS_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without it, the same environment is a real account nobody configured: it refuses to start.
    with pytest.raises(ValueError, match="KEYCLOAK_"):
        build_app(Settings.from_env({"TRANIBOR_SERVER_TOKEN": "the-platform-s-token"}))
