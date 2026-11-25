# SPDX-License-Identifier: Apache-2.0
"""The `lan-incident` template, played against a core checkout (CONTRIBUTING.md, "Checks").

Run from the core's root, which holds the platform's packages and its own template conformance suite:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/unifi/tests

`tranibor plugin validate` reads a template's manifest only; these tests read what it ships the way the
platform does: the core's conformance checks, the strict workflow parser, the routing after the diagnosis,
and the two governed writes rendered as proposed, with the approval each waits for.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "lan-incident"
CORE = Path(os.environ.get("TRANIBOR_CORE", Path.cwd())).resolve()
SITE = "11111111-1111-4111-8111-111111111111"
SWITCH = "33333333-3333-4333-8333-333333333303"


def _conformance() -> ModuleType:
    path = CORE / "tests" / "conformance" / "templates" / "test_templates.py"
    if not path.is_file():
        raise RuntimeError(f"run from a core checkout (or set TRANIBOR_CORE): {path} not found")
    spec = importlib.util.spec_from_file_location("core_template_conformance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checks() -> list[str]:
    """Every check the core plays on its own templates (a `test_*` taking the manifest's path only)."""
    return sorted(
        name
        for name, function in vars(_conformance()).items()
        if name.startswith("test_")
        and callable(function)
        and list(inspect.signature(function).parameters) == ["path"]
    )


def _workflow() -> Any:
    from tranibor_core.dsl import parse_workflow

    workflow, report = parse_workflow((TEMPLATE / "workflows" / "lan-incident.yaml").read_text(), strict=True)
    assert report.valid and not report.warnings, report.as_dict()
    return workflow


def _transition(transition: str) -> Any:
    (found,) = [t for t in _workflow().transitions if t.id == transition]
    return found


#: The agent and the skill come with the plugin (installed in the organisation when it is), not with the
#: template: the core's check only knows agents a template ships or the core's catalogue holds. The agent is
#: checked for completeness below, and the plugin by `tranibor plugin validate`.
PLUGIN_SHIPS_AGENT = "test_the_template_agents_and_skills_are_installable"


@pytest.mark.parametrize("check", _checks())
def test_the_core_conformance_holds(check: str) -> None:
    if check == PLUGIN_SHIPS_AGENT:
        pytest.skip("the agent and the skill are the plugin's, not the template's")
    getattr(_conformance(), check)(TEMPLATE / "manifest.yaml")


def test_the_core_checks_that_matter_here_are_played() -> None:
    assert {
        "test_the_manifest_is_well_formed",
        "test_the_workflows_the_default_the_routing_and_the_policy_exist",
        "test_actions_call_only_what_the_template_announces",
    } <= set(_checks())


def test_the_template_and_the_plugin_agree_on_what_they_ship_and_need() -> None:
    manifest = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text())
    assert manifest["requires"]["connectors"] == {"tracker": "internal"}
    assert manifest["requires"]["org_connectors"] == {"unifi": "mcp"}
    plugin = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    assert plugin["contents"]["templates"] == ["templates/lan-incident"]
    workflow = _workflow()
    agents = [a for a in workflow.actors.values() if a.type == "agent"]
    assert [a.agent for a in agents] == ["unifi-netops"]


def test_the_agent_is_complete_and_reaches_only_its_connector() -> None:
    from tranibor_core.agent_completeness import missing_components

    agent = yaml.safe_load((PLUGIN / "agents" / "unifi-netops.yaml").read_text())["agent"]
    assert missing_components(agent["spec"]) == []
    (server,) = agent["spec"]["mcp_servers"]
    assert server["connector"] == "unifi"
    descriptor = yaml.safe_load((PLUGIN / "servers" / "unifi.yaml").read_text())
    declared = {o["name"] for o in descriptor["operations"]}
    assert set(server["tools"]) <= declared
    # no tool the agent holds can be a write the descriptor leaves ungoverned
    writes = {o["name"]: o for o in descriptor["operations"] if o["access"] == "write"}
    assert all(writes[t].get("human_approval") for t in server["tools"] if t in writes)


@pytest.mark.parametrize(
    ("outputs", "expected"),
    [
        ({"action_kind": "restart_device"}, "review_restart"),
        ({"action_kind": "power_cycle_port"}, "review_cycle"),
        ({"action_kind": "none"}, "review_none"),
        ({"action_kind": "something else"}, "review_none"),
        ({}, "review_none"),
    ],
)
def test_the_diagnosis_routes_a_proposal_to_its_review_and_the_rest_to_a_reading(
    outputs: dict[str, Any], expected: str
) -> None:
    from tranibor_core.dsl.engine import WorkflowEngine

    chosen = WorkflowEngine(_workflow()).select_transition("diagnosed", outputs=outputs)
    assert chosen is not None and chosen.to == expected


def test_the_diagnosis_must_name_a_known_action_kind() -> None:
    gates = {g.name: g for g in _transition("t-diagnose").gates}
    assert set(gates) == {"outputs_present", "outputs_in"}
    assert gates["outputs_in"].params["values"]["action_kind"] == ["none", "restart_device", "power_cycle_port"]


def _proposed(transition: str, fields: dict[str, Any]) -> dict[str, Any]:
    from tranibor_api.effects import render
    from tranibor_api.services.actions import rendered_approval

    action = _transition(transition).action
    context = {"fields": fields, "work_item": {"id": "w1", "key": "NET-4", "title": "t"}}
    return {
        "title": render(action.title, context),
        "params": render(action.params, context),
        "approval": rendered_approval(action.approval.model_dump(), context),
        "effects": action.effects,
    }


@pytest.mark.parametrize(
    ("transition", "operation", "extra", "arguments"),
    [
        ("t-apply-restart", "restart_device", {}, {"site_id": SITE, "device_id": SWITCH}),
        ("t-apply-cycle", "power_cycle_port", {"port_idx": "5"}, {"site_id": SITE, "device_id": SWITCH, "port_idx": "5"}),
    ],
)
def test_a_restart_and_a_power_cycle_wait_for_the_network_administrators(
    transition: str, operation: str, extra: dict[str, str], arguments: dict[str, str]
) -> None:
    from tranibor_api.effects import render

    proposed = _proposed(transition, {"site_id": SITE, "device_id": SWITCH, "reason": "frozen", **extra})
    assert proposed["approval"]["approvers"] == [{"role": "approver", "group": "network-admin", "min": 1}]
    assert proposed["approval"]["step_up_minutes"] <= 5
    (call,) = proposed["effects"]
    assert (call.with_["connector"], call.with_["operation"]) == ("unifi", operation)
    assert render(call.with_["arguments"], {"params": proposed["params"], "effects": []}) == arguments
    assert SWITCH in proposed["title"]


def test_the_review_forms_name_ids_the_server_accepts() -> None:
    import jsonschema

    server_id = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    for transition in ("t-review-restart", "t-review-cycle"):
        form = _transition(transition).task.form
        validator = jsonschema.Draft202012Validator(form)
        good = {"site_id": SITE, "device_id": SWITCH, "reason": "r", "port_idx": "5"}
        validator.validate(good)
        for field in ("site_id", "device_id"):
            assert form["properties"][field]["pattern"] == server_id
            assert not validator.is_valid({**good, field: "../../x"})
    cycle = jsonschema.Draft202012Validator(_transition("t-review-cycle").task.form)
    assert not cycle.is_valid({"site_id": SITE, "device_id": SWITCH, "reason": "r", "port_idx": "five"})
