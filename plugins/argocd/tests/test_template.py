# SPDX-License-Identifier: Apache-2.0
"""The `gitops-drift` template, played against a core checkout (CONTRIBUTING.md, "Checks").

Run from the core's root, which holds the platform's packages and its own template conformance suite:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/argocd/tests

`tranibor plugin validate` reads a template's manifest only; these tests read what it ships the way a
project's birth and its interpreter do:

- the core's own template conformance checks, called on the template;
- the workflow parses strictly, and its path is alert, diagnosis, review, apply, verify, done;
- the apply is one governed action calling the plugin's `sync_application`, a FULL sync (no resource list),
  rendered the way the platform renders a proposed action, with an approval and no one excluded by accident;
- every operation the workflow calls is declared by the plugin's server, and its arguments are that
  operation's parameters;
- the agent and the skill the workflow names are the plugin's own.
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
from jinja2.sandbox import SandboxedEnvironment

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "gitops-drift"
CORE = Path(os.environ.get("TRANIBOR_CORE", Path.cwd())).resolve()
#: The core's own templates only take a tracker type its adapters register; `internal` is one.
FIELDS = {"application": "shop-api", "prune": False, "decision_note": "Reviewed: the drift is the hotfix."}


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


def _operations() -> dict[str, dict[str, Any]]:
    server = yaml.safe_load((PLUGIN / "servers" / "argocd.yaml").read_text())
    return {op["name"]: op for op in server["operations"]}


def _manifest() -> dict[str, Any]:
    return dict(yaml.safe_load((TEMPLATE / "manifest.yaml").read_text()))


def _workflow() -> Any:
    from tranibor_core.dsl import parse_workflow

    workflow, report = parse_workflow((TEMPLATE / "workflows" / "gitops-drift.yaml").read_text(), strict=True)
    assert report.valid and not report.warnings, report.as_dict()
    return workflow


def _transition(transition: str) -> Any:
    (found,) = [t for t in _workflow().transitions if t.id == transition]
    return found


def _render(value: Any, **context: Any) -> Any:
    env = SandboxedEnvironment()
    if isinstance(value, str):
        return env.from_string(value).render(**context)
    if isinstance(value, dict):
        return {k: _render(v, **context) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, **context) for v in value]
    return value


#: The core's own templates ship their agents; this template's agent and skill are the plugin's (installed
#: with it), which `plugin validate` and `test_the_template_names_the_plugin_s_own_agent...` check instead.
PLUGIN_AGENT_CHECK = "test_the_template_agents_and_skills_are_installable"


@pytest.mark.parametrize("check", _core_checks())
def test_the_core_conformance_holds(check: str) -> None:
    if check == PLUGIN_AGENT_CHECK:
        pytest.skip("the agent and the skill come with the plugin, not the template")
    getattr(_core_conformance(), check)(TEMPLATE / "manifest.yaml")


def test_the_path_is_alert_diagnosis_review_apply_verify_done() -> None:
    workflow = _workflow()
    by_id = {t.id: (t.from_, t.to) for t in workflow.transitions}
    assert by_id["t-diagnose"] == ("alert", "awaiting_review")
    assert by_id["t-review"] == ("awaiting_review", "approved")
    assert by_id["t-apply"] == ("approved", "applied")
    assert by_id["t-verify"] == ("applied", "done")


def test_the_review_rejects_to_a_close_without_any_sync() -> None:
    assert _transition("t-review").on_reject == "closed"


def test_the_template_names_the_plugin_s_own_agent_and_no_foreign_one() -> None:
    plugin = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    assert "agents/gitops-steward.yaml" in plugin["contents"]["agents"]
    raw = yaml.safe_load((TEMPLATE / "workflows" / "gitops-drift.yaml").read_text())
    agents = {a["agent"] for a in raw["actors"].values() if a.get("type") == "agent"}
    assert agents == {"gitops-steward"}
    assert _manifest()["requires"]["org_connectors"] == {"argocd": "mcp"}


def test_the_apply_is_one_full_sync_through_the_plugin_s_operation() -> None:
    action = _transition("t-apply").action
    (effect,) = action.effects
    assert effect.effect == "connector.call"
    assert effect.with_["connector"] == "argocd"
    assert effect.with_["operation"] == "sync_application"
    operation = _operations()["sync_application"]
    assert operation["human_approval"] is True
    # a FULL sync: no resource list, which is what leaves a stale operation state behind
    assert set(effect.with_["arguments"]) <= {"name", "prune"}
    assert "resources" not in effect.with_["arguments"]


def test_the_apply_renders_with_its_approval_and_prune_off_by_default() -> None:
    action = _transition("t-apply").action
    params = _render(dict(action.params), fields=FIELDS)
    assert params == {"application": "shop-api", "prune": "false"}
    title = _render(action.title, fields=FIELDS)
    assert "shop-api" in title and "prune off" in title
    assert _render(action.justification, fields=FIELDS) == FIELDS["decision_note"]
    assert action.approval is not None
    assert action.approval.approvers[0].group == "platform-owners"
    on = _render(action.title, fields={**FIELDS, "prune": True})
    assert "prune on" in on


def test_every_operation_the_workflow_calls_is_declared_by_the_server() -> None:
    declared = set(_operations())
    raw = (TEMPLATE / "workflows" / "gitops-drift.yaml").read_text()
    for line in raw.splitlines():
        if line.strip().startswith("operation:"):
            assert line.split(":", 1)[1].strip() in declared


def test_both_agent_steps_write_the_report_the_agent_declares() -> None:
    agent = (PLUGIN / "agents" / "gitops-steward.yaml").read_text()
    for transition in ("t-diagnose", "t-verify"):
        assert list(_transition(transition).outputs) == ["gitops_report_markdown"]
    assert '"gitops_report_markdown"' in agent
