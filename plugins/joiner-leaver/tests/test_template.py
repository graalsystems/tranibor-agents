# SPDX-License-Identifier: Apache-2.0
"""The `joiner-leaver` template and its agent, played against a core checkout (CONTRIBUTING.md, "Checks").

Run from the core's root, which holds the platform's packages and its own template conformance suite:

    cd <core checkout> && uv sync --frozen
    TRANIBOR_CORE=$PWD uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/joiner-leaver/tests

`tranibor plugin validate` reads manifests; these tests read what the plugin ships the way the platform
does: the core's template conformance checks, the agent's instructions against its eval case, the plan as
the `output_schema` guarantee judges it, the fan-out as `spawn_children` renders it, and each governed
write as it is proposed, with who may not decide it.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "joiner-leaver"
CORE = Path(os.environ.get("TRANIBOR_CORE", Path.cwd())).resolve()

#: What the `keycloak-admin` plugin's server serves (its tool names are the contract this plugin calls).
READS = {"find_users", "get_user", "list_user_groups", "list_groups", "get_group_members", "list_user_sessions"}
WRITES = {
    "create_user",
    "add_user_to_group",
    "remove_user_from_group",
    "disable_user",
    "enable_user",
    "logout_user",
    "send_required_actions_email",
}
CHILDREN = {
    "jml-create-account": "create_user",
    "jml-group-add": "add_user_to_group",
    "jml-group-remove": "remove_user_from_group",
    "jml-invite": "send_required_actions_email",
    "jml-disable": "disable_user",
    "jml-logout": "logout_user",
}
COMPENSATION = {
    "jml-group-add": "remove_user_from_group",
    "jml-group-remove": "add_user_to_group",
    "jml-disable": "enable_user",
}
UUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
GROUP = {
    "group_id": "00000000-0000-0000-0000-0000000000a1",
    "group_path": "/engineering/developers",
    "sensitive": False,
    "approver_group": "engineering",
    "reason": "Group of the Software engineer role",
}
SENSITIVE = {**GROUP, "group_id": "00000000-0000-0000-0000-0000000000a3", "sensitive": True,
             "group_path": "/engineering/platform", "approver_group": "platform-leads"}  # fmt: skip
PLAIN = {"approver_group": "it-identity", "reason": "Account lifecycle"}
USER_ID = "00000000-0000-0000-0000-0000000000f1"
CHANGES = {
    "accounts": [{"email": "lea@example.com", "first_name": "Lea", "last_name": "Martin",
                  "approver_group": "it-identity", "reason": "Joins as a Platform engineer"}],
    "group_removals": [],
    "group_additions": [GROUP, SENSITIVE],
    "invites": [PLAIN],
    "disables": [],
    "logouts": [],
    "manual_tasks": ["Hand over a laptop", "Add Lea to the GitHub organisation (not connected)"],
}  # fmt: skip
REQUEST = {
    "kind": "joiner",
    "full_name": "Lea Martin",
    "username": "lea.martin",
    "effective_date": "2026-12-01",
    "approver": "Paul Durand <paul@example.com>",
    "requester": "paul@example.com",
    "manager": "paul@example.com",
}


def _core_conformance() -> ModuleType:
    path = CORE / "tests" / "conformance" / "templates" / "test_templates.py"
    if not path.is_file():
        raise RuntimeError(f"run from a core checkout (or set TRANIBOR_CORE): {path} not found")
    spec = importlib.util.spec_from_file_location("core_template_conformance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _core_checks() -> list[str]:
    module = _core_conformance()
    return sorted(
        name
        for name, function in vars(module).items()
        if name.startswith("test_")
        and callable(function)
        and list(inspect.signature(function).parameters) == ["path"]
    )


def _workflow(name: str) -> Any:
    from tranibor_core.dsl import parse_workflow

    workflow, report = parse_workflow((TEMPLATE / "workflows" / f"{name}.yaml").read_text(), strict=True)
    assert report.valid and not report.warnings, report.as_dict()
    return workflow


def _transition(workflow: str, transition: str) -> Any:
    (found,) = [t for t in _workflow(workflow).transitions if t.id == transition]
    return found


def _schemas(transition: Any) -> dict[str, Any]:
    for gate in transition.gates:
        if gate.name == "output_schema":
            return dict(gate.params["schemas"])
    raise AssertionError(f"{transition.id}: no `output_schema` guarantee")


def _agent() -> dict[str, Any]:
    return dict(yaml.safe_load((PLUGIN / "agents" / "people-ops-it.yaml").read_text()))


# ── The core's own checks, the manifests ───────────────────────────────────────────────────────────


#: The core's own templates ship their agents in the template folder; this plugin's agent and skills come
#: with the plugin itself (as `servicenow`'s do), checked by `test_the_plugin_ships_the_agent_and_skills`.
PLUGIN_AGENT_CHECK = "test_the_template_agents_and_skills_are_installable"


@pytest.mark.parametrize("check", _core_checks())
def test_the_core_conformance_holds(check: str) -> None:
    if check == PLUGIN_AGENT_CHECK:
        pytest.skip("the agent and the skills are the plugin's: see test_the_plugin_ships_the_agent_and_skills")
    getattr(_core_conformance(), check)(TEMPLATE / "manifest.yaml")


def test_the_plugin_ships_no_server_and_names_only_the_keycloak_connector() -> None:
    plugin = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    template = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text())
    assert "servers" not in plugin["contents"] and not (PLUGIN / "server").exists()
    assert plugin["requires"]["org_connectors"] == {"keycloak": "mcp"}
    assert template["requires"]["org_connectors"] == {"keycloak": "mcp"}
    assert template["requires"]["connectors"] == {"tracker": "internal"}


def test_the_plugin_ships_the_agent_and_skills_the_workflow_names() -> None:
    plugin = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    assert plugin["contents"]["agents"] == ["agents/people-ops-it.yaml"]
    assert plugin["contents"]["skills"] == ["skills/jml-checklist", "skills/role-catalogue"]
    actors = _workflow("joiner-leaver").actors
    assert {a.agent for a in actors.values() if getattr(a, "agent", None)} == {"people-ops-it"}
    assert [s["slug"] for s in _agent()["agent"]["spec"]["skills"]] == ["jml-checklist", "role-catalogue"]
    for skill in ("jml-checklist", "role-catalogue"):
        assert (PLUGIN / "skills" / skill / "SKILL.md").is_file()


def test_every_workflow_is_declared_and_valid() -> None:
    template = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text())
    declared = {Path(p).stem for p in template["defaults"]["workflows"]}
    assert declared == {"joiner-leaver", *CHILDREN} == {p.stem for p in (TEMPLATE / "workflows").glob("*.yaml")}
    for name in declared:
        _workflow(name)


# ── The agent: complete, reads only, and its eval case ─────────────────────────────────────────────


def test_the_agent_reads_keycloak_and_writes_nothing() -> None:
    spec = _agent()["agent"]["spec"]
    (server,) = spec["mcp_servers"]
    assert server["connector"] == "keycloak"
    assert set(server["tools"]) == READS, "the agent has the six reads and no write"
    assert not WRITES & set(server["tools"])
    assert spec["tools"] == {"catalogue": [], "extensions": [], "web": []}
    assert spec["model"] == "profile:by_size" and spec["memories"]


def test_the_agent_is_complete() -> None:
    from tranibor_core.agent_completeness import missing_components

    assert missing_components(_agent()["agent"]["spec"]) == []


def test_the_eval_case_holds_on_the_rendered_instructions() -> None:
    from tranibor_core.agent_catalogue.evals import AgentEvalResult  # noqa: F401
    from tranibor_core.instructions import render_instructions

    case = yaml.safe_load((PLUGIN / "evals" / "people-ops-it.yaml").read_text())
    rendered = render_instructions(_agent()["agent"]["spec"]["instructions"], inputs={}).lower()
    assert case["agent"] == "people-ops-it"
    assert [n for n in case["must_contain"] if str(n).lower() not in rendered] == []
    assert [n for n in case["must_not_contain"] if str(n).lower() in rendered] == []


def test_the_instructions_never_name_a_keycloak_write() -> None:
    text = _agent()["agent"]["spec"]["instructions"]
    assert not [w for w in WRITES if f"keycloak__{w}" in text]
    assert "delete" not in "".join(re.findall(r"keycloak__\w+", text))


# ── The workflow: a request without an approver, the plan, the routing ─────────────────────────────


def test_the_request_must_name_an_approver_and_a_date() -> None:
    inputs = _workflow("joiner-leaver").metadata.inputs
    validator = jsonschema.Draft202012Validator(inputs)
    validator.validate(REQUEST)
    for missing in ("approver", "effective_date", "username", "kind"):
        assert not validator.is_valid({k: v for k, v in REQUEST.items() if k != missing}), missing
    assert not validator.is_valid({**REQUEST, "kind": "contractor"})
    assert not validator.is_valid({**REQUEST, "username": "lea/../admin"})


def test_a_refused_request_goes_to_a_person_and_a_plan_to_the_manager() -> None:
    from tranibor_core.dsl.engine import WorkflowEngine

    engine = WorkflowEngine(_workflow("joiner-leaver"))
    refused = engine.select_transition("planned", outputs={"decision": "refused"})
    assert refused is not None and refused.to == "to_review"


def test_two_people_approve_the_plan_before_any_write_and_only_a_person_closes_it() -> None:
    workflow = _workflow("joiner-leaver")
    by = {t.id: t for t in workflow.transitions}
    assert (by["t-manager"].from_, by["t-manager"].to) == ("planned", "manager_ok")
    assert (by["t-it"].from_, by["t-it"].to) == ("manager_ok", "approved")
    assert by["t-manager"].by != by["t-it"].by
    first_write = by["t-accounts"]
    assert first_write.from_ == "approved", "no write before both approvals"
    assert by["t-close"].task is not None and by["t-close"].by == "it"
    assert by["t-close"].when == {"verified": True}
    assert by["t-not-verified"].to == "to_review", "a read-back that disagrees never reaches done"


def test_no_workflow_deletes_anything() -> None:
    for name in ("joiner-leaver", *CHILDREN):
        text = (TEMPLATE / "workflows" / f"{name}.yaml").read_text()
        assert not re.findall(r"operation:\s*\S*delete\S*", text), name


# ── The plan, as the guarantee judges it ───────────────────────────────────────────────────────────


def _plan_schemas() -> dict[str, Any]:
    return _schemas(_transition("joiner-leaver", "t-plan"))


def test_a_joiner_plan_passes_the_guarantee() -> None:
    schemas = _plan_schemas()
    jsonschema.Draft202012Validator(schemas["changes"]).validate(CHANGES)
    jsonschema.Draft202012Validator(schemas["decision"]).validate("plan")
    jsonschema.Draft202012Validator(schemas["summary"]).validate("The checklist.")


@pytest.mark.parametrize(
    "broken",
    [
        {k: v for k, v in CHANGES.items() if k != "manual_tasks"},
        {**CHANGES, "extra": []},
        {**CHANGES, "accounts": [CHANGES["accounts"][0]] * 2},
        {**CHANGES, "group_additions": [{**GROUP, "group_id": "developers"}]},
        {**CHANGES, "group_additions": [{**GROUP, "sensitive": "no"}]},
        {**CHANGES, "group_additions": [{k: v for k, v in GROUP.items() if k != "approver_group"}]},
        {**CHANGES, "group_additions": [{**GROUP, "approver_group": "{{ fields.x }}"}]},
        {**CHANGES, "group_additions": [{**GROUP, "username": "someone.else"}]},
        {**CHANGES, "group_additions": [GROUP] * 31},
        {**CHANGES, "disables": [PLAIN, PLAIN]},
        {**CHANGES, "invites": [{"approver_group": "it-identity"}]},
        {**CHANGES, "manual_tasks": [""]},
    ],
    ids=[
        "no-manual-tasks", "extra-list", "two-accounts", "group-id-not-a-uuid", "sensitive-not-boolean",
        "no-approvers", "approvers-a-template", "a-target-of-its-own", "too-many-groups", "two-disables",
        "no-reason", "empty-task",
    ],
)  # fmt: skip
def test_a_plan_the_next_step_cannot_read_is_refused(broken: dict[str, Any]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(_plan_schemas()["changes"]).validate(broken)


def test_the_resolved_user_id_is_one_id_for_one_account() -> None:
    schema = _schemas(_transition("joiner-leaver", "t-resolve"))["resolved"]
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate({"lea.martin": USER_ID})
    for broken in ({}, {"lea.martin": "lea"}, {"a": USER_ID, "b": USER_ID}):
        assert not validator.is_valid(broken), broken


# ── The fan-out: what each child is told ───────────────────────────────────────────────────────────


def _children(transition: str, outputs: dict[str, Any], fields: dict[str, Any] | None = None) -> Any:
    from tranibor_orchestrator.activities.children import items_of, outputs_of, render

    spawning = _transition("joiner-leaver", transition)
    parent_fields = dict(fields or REQUEST)
    rendered = []
    for rank, element in enumerate(items_of(spawning.spawn.for_each, parent_fields, dict(outputs)), 1):
        context = {
            "item": element,
            "rank": rank,
            "fields": parent_fields,
            "outputs": outputs_of(dict(outputs)),
            "parent": {"key": "JML-7", "title": "Lea joins"},
        }
        rendered.append(
            (element, render(spawning.spawn.fields, context), render(spawning.spawn.title, context))
        )
    return rendered


OUTPUTS = {"changes": CHANGES, "resolved": {"lea.martin": USER_ID}}
SPAWNS = {
    "t-accounts": ("jml-create-account", "accounts"),
    "t-removals": ("jml-group-remove", "group_removals"),
    "t-additions": ("jml-group-add", "group_additions"),
    "t-invites": ("jml-invite", "invites"),
    "t-disables": ("jml-disable", "disables"),
    "t-logouts": ("jml-logout", "logouts"),
}


def test_the_writes_are_spawned_in_a_safe_order() -> None:
    workflow = _workflow("joiner-leaver")
    chain, state = [], "approved"
    while True:
        nxt = [t for t in workflow.transitions if t.from_ == state and t.spawn]
        if not nxt:
            if state == "accounts_done":  # the agent resolves the id, then the next spawn
                state = "resolved"
                continue
            break
        (t,) = nxt
        chain.append(t.spawn.workflow)
        state = t.to
    assert chain == [
        "jml-create-account", "jml-group-remove", "jml-group-add", "jml-invite", "jml-disable", "jml-logout",
    ], "removals before additions; disabling before ending sessions"  # fmt: skip


@pytest.mark.parametrize("transition", sorted(SPAWNS))
def test_each_child_gets_fields_its_workflow_accepts_and_aims_at_the_requests_account(transition: str) -> None:
    workflow, key = SPAWNS[transition]
    outputs = {**OUTPUTS, "changes": {**CHANGES, key: CHANGES[key] or [PLAIN]}}
    if key in ("group_removals",):
        outputs["changes"]["group_removals"] = [GROUP]
    if key in ("accounts",):
        outputs["changes"]["accounts"] = CHANGES["accounts"]
    children = _children(transition, outputs)
    assert children, transition
    child_inputs = _workflow(workflow).metadata.inputs
    for _element, fields, title in children:
        jsonschema.Draft202012Validator(child_inputs).validate(fields)
        assert fields["username"] == "lea.martin" and "lea.martin" in title
        assert fields["effective_date"] == "2026-12-01"
        assert fields["approver"] == REQUEST["approver"] and fields["request"] == "JML-7"
        if transition != "t-accounts":
            assert fields["user_id"] == USER_ID


def test_an_empty_list_opens_no_child_and_a_missing_user_id_is_refused() -> None:
    from tranibor_orchestrator.activities.children import SpawnRefusedError

    assert _children("t-logouts", OUTPUTS) == []
    other = {**OUTPUTS, "resolved": {"someone.else": USER_ID}}
    with pytest.raises(SpawnRefusedError):
        _children("t-additions", other)


def test_a_plan_cannot_aim_a_write_at_another_account() -> None:
    sneaky = {**OUTPUTS, "changes": {**CHANGES, "group_additions": [{**GROUP, "username": "ceo"}]}}
    ((_, fields, _),) = _children("t-additions", {**sneaky, "changes": {**sneaky["changes"], "group_additions": [sneaky["changes"]["group_additions"][0]]}})  # fmt: skip
    assert fields["username"] == "lea.martin" and "ceo" not in fields.values()


# ── The governed writes ────────────────────────────────────────────────────────────────────────────


def _proposed(workflow: str, fields: dict[str, Any]) -> dict[str, Any]:
    from tranibor_api.effects import render
    from tranibor_api.services.actions import rendered_approval

    action = _transition(workflow, "t-apply").action
    context = {"fields": fields, "work_item": {"id": "w1", "key": "JML-7.1", "title": "t"}}
    params = render(action.params, context)
    return {
        "title": render(action.title, context),
        "justification": render(action.justification, context),
        "params": params,
        "approval": rendered_approval(action.approval.model_dump(), context),
        "effects": action.effects,
        "action": action,
    }


def _fields(workflow: str) -> dict[str, Any]:
    base = {
        "username": "lea.martin", "user_id": USER_ID, "effective_date": "2026-12-01",
        "approver_group": "engineering", "reason": "Role group", "approver": "Paul Durand",
        "requester": "paul@example.com", "manager": "manager@example.com", "request": "JML-7",
        "group_id": GROUP["group_id"], "group_path": GROUP["group_path"], "sensitive": False,
        "email": "lea@example.com", "first_name": "Lea", "last_name": "Martin",
    }  # fmt: skip
    return base


@pytest.mark.parametrize("workflow", sorted(CHILDREN))
def test_each_write_is_one_call_approved_by_its_approvers_never_by_those_concerned(workflow: str) -> None:
    proposed = _proposed(workflow, _fields(workflow))
    (effect,) = proposed["effects"]
    assert effect.with_["connector"] == "keycloak" and effect.with_["operation"] == CHILDREN[workflow]
    assert effect.with_["operation"] in WRITES
    compensation = effect.compensate
    if workflow in COMPENSATION:
        assert compensation and compensation["with"]["operation"] == COMPENSATION[workflow]
    else:
        assert not compensation, "no undo is invented where none exists"
    (approver,) = proposed["approval"]["approvers"]
    assert approver["group"] == "jml-approvers-engineering" and approver["min"] == 1
    assert set(proposed["approval"]["excluded"]) == {"lea.martin", "paul@example.com", "manager@example.com"}
    assert proposed["approval"]["step_up_minutes"] == 5
    assert str(proposed["action"].not_before) == "fields.effective_date", "never before the date"
    assert "Paul Durand" in proposed["justification"]


def test_the_group_writes_show_the_ids_and_the_sensitivity_to_the_approver() -> None:
    for workflow in ("jml-group-add", "jml-group-remove"):
        plain = _proposed(workflow, _fields(workflow))
        sensitive = _proposed(workflow, {**_fields(workflow), "sensitive": True})
        assert not plain["title"].startswith("[sensitive]") and sensitive["title"].startswith("[sensitive] ")
        assert USER_ID in plain["title"] and GROUP["group_id"] in plain["title"]
        assert plain["params"]["sensitive"] == "no" and sensitive["params"]["sensitive"] == "yes"
        args = plain["effects"][0].with_["arguments"]
        assert set(args) == {"user_id", "group_id"}


def test_a_child_refuses_an_id_that_is_not_one() -> None:
    inputs = _workflow("jml-group-add").metadata.inputs
    validator = jsonschema.Draft202012Validator(inputs)
    assert validator.is_valid(_fields("jml-group-add"))
    assert not validator.is_valid({**_fields("jml-group-add"), "group_id": "/engineering/developers"})
    assert not validator.is_valid({**_fields("jml-group-add"), "user_id": "../admin"})
    assert not validator.is_valid({k: v for k, v in _fields("jml-group-add").items() if k != "approver"})
