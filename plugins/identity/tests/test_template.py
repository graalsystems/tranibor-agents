# SPDX-License-Identifier: Apache-2.0
"""The templates, the agents, the skills, the evals and the profile agree with each other and with the core's schemas.

Played from a core checkout that knows profiles (stories S86-01/S86-02), as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    TRANIBOR_CORE=$PWD uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/identity/tests
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
PROFILE = PLUGIN / "profiles" / "identity.yaml"
AGENTS = {"identity-steward": "identity-review", "joiner-leaver": "joiner-leaver"}
OUTPUT_KEYS = {
    "identity-steward": "identity_steward_markdown",
    "joiner-leaver": "joiner_leaver_markdown",
}


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _schemas() -> Path:
    root = Path(os.environ.get("TRANIBOR_CORE") or Path.cwd())
    schemas = root / "packages" / "contracts" / "schemas"
    if not (schemas / "template.schema.json").is_file():
        pytest.skip("not in a core checkout (TRANIBOR_CORE)")
    return schemas


def _profile() -> dict[str, Any]:
    return _load(PROFILE)


def _spec(agent: str) -> dict[str, Any]:
    spec = _load(PLUGIN / "agents" / f"{agent}.yaml")["agent"]["spec"]
    assert isinstance(spec, dict)
    return spec


@pytest.mark.parametrize("template", sorted(set(AGENTS.values())))
def test_the_manifests_and_the_workflows_follow_the_core_s_schemas(
    template: str,
) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schemas = _schemas()
    root = PLUGIN / "templates" / template
    jsonschema.validate(
        _load(root / "manifest.yaml"),
        json.loads((schemas / "template.schema.json").read_text(encoding="utf-8")),
    )
    for workflow in (root / "workflows").glob("*.yaml"):
        jsonschema.validate(
            _load(workflow),
            json.loads((schemas / "workflow.schema.json").read_text(encoding="utf-8")),
        )


def test_the_plugin_lists_what_it_ships() -> None:
    contents = _load(PLUGIN / "plugin.yaml")["contents"]
    for kind, key in (
        ("agents", "agents"),
        ("skills", "skills"),
        ("templates", "templates"),
        ("evals", "evals"),
    ):
        for entry in contents[key]:
            assert (PLUGIN / entry).exists(), (kind, entry)
    assert contents["profiles"] == ["profiles/identity.yaml"]


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_the_agents_use_only_the_profile_s_tools_and_name_no_provider(
    agent: str,
) -> None:
    profile_tools = {tool["name"] for tool in _profile()["tools"]}
    spec = _spec(agent)
    assert [server["profile"] for server in spec["mcp_servers"]] == ["identity/identity@1"]
    assert "connector" not in spec["mcp_servers"][0]
    assert set(spec["mcp_servers"][0]["tools"]) <= profile_tools
    assert spec["tools"] == {"catalogue": [], "extensions": [], "web": []}
    text = spec["instructions"].lower()
    for provider in ("keycloak", "okta", "entra", "ldap"):
        assert provider not in text


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_an_agent_can_only_write_what_a_person_approves(agent: str) -> None:
    tools = {tool["name"]: tool for tool in _profile()["tools"]}
    for name in _spec(agent)["mcp_servers"][0]["tools"]:
        tool = tools[name]
        if tool["access"] == "write":
            assert "delete" not in name
    # The writes that touch the person are never applied without a decision.
    for name in (
        "create_user",
        "add_user_to_group",
        "disable_user",
        "enable_user",
        "logout_user",
        "send_setup_email",
    ):
        assert tools[name]["human_approval"] is True
    assert not tools["remove_user_from_group"].get("human_approval")


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_an_agent_follows_its_skills_and_a_prompt_injection_section(agent: str) -> None:
    spec = _spec(agent)
    for skill in spec["skills"]:
        assert (PLUGIN / "skills" / skill["slug"] / "SKILL.md").is_file()
    text = spec["instructions"].lower()
    assert "suspected prompt injection" in text
    assert f'"{OUTPUT_KEYS[agent]}"' in text
    assert "evidence" in text


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_the_evals_hold_against_the_instructions(agent: str) -> None:
    evals = _load(PLUGIN / "evals" / f"{agent}.yaml")
    text = _spec(agent)["instructions"].lower()
    assert evals["agent"] == agent
    for phrase in evals["must_contain"]:
        assert str(phrase).lower() in text, phrase
    for phrase in evals["must_not_contain"]:
        assert str(phrase).lower() not in text, phrase
    assert all(case["live"] for case in evals["cases"])


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_the_workflow_hands_the_output_to_a_person(agent: str) -> None:
    workflow = _load(PLUGIN / "templates" / AGENTS[agent] / "workflows" / f"{AGENTS[agent]}.yaml")
    assert all("action" not in transition for transition in workflow["transitions"])
    steps = {t["id"]: t for t in workflow["transitions"]}
    first = workflow["transitions"][0]
    assert first["from"] == workflow["initial"] and first["by"] == next(
        name for name, actor in workflow["actors"].items() if actor["type"] == "agent"
    )
    assert first["outputs"] == [OUTPUT_KEYS[agent]]
    assert first["to"] in workflow["states"] and "task" in workflow["transitions"][1]
    manifest = _load(PLUGIN / "templates" / AGENTS[agent] / "manifest.yaml")
    assert manifest["requires"]["connectors"] == {}
    assert "org_connectors" not in manifest["requires"]
    actors = [actor["agent"] for actor in workflow["actors"].values() if actor["type"] == "agent"]
    assert actors == [agent]
    assert steps  # every transition is reachable by id


def test_a_joiner_or_leaver_is_approved_by_the_manager_then_decided_then_read_back() -> None:
    workflow = _load(PLUGIN / "templates" / "joiner-leaver" / "workflows" / "joiner-leaver.yaml")
    chain = [(t["from"], t["to"], t["by"]) for t in workflow["transitions"] if t["id"] != "t-abandon"]
    assert chain == [
        ("received", "planned", "agent"),
        ("planned", "manager_ok", "manager"),
        ("manager_ok", "approved", "it_identity"),
        ("approved", "checked", "agent"),
        ("checked", "done", "it_identity"),
    ]
    steps = {t["id"]: t for t in workflow["transitions"]}
    assert steps["t-manager"]["on_reject"] == "declined"
    assert steps["t-readback"]["outputs"] == ["joiner_leaver_readback_markdown"]
    text = _spec("joiner-leaver")["instructions"]
    assert "state `received`" in text and "state `approved`" in text and "joiner_leaver_readback_markdown" in text
