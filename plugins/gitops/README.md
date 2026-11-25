# gitops plugin

Diagnose and fix applications that a GitOps controller reconciles, with a Tranibor agent, under human approval.
This plugin holds what does not depend on the controller: the **profile** every gitops server must serve, the
agent, the skill, the template, the evals and the conformance suite. A provider plugin brings the server:
[`gitops-argocd`](../gitops-argocd/README.md); a Flux one fits the same profile.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `gitops` 0.1.0, `contents.profiles`; core `>=0.30.0,<1.0.0` is a placeholder (see below) |
| Profile | [`profiles/gitops.yaml`](profiles/gitops.yaml) | `gitops/gitops@1`: 14 tools, their schemas and the governance floor |
| Agent | [`agents/gitops-steward.yaml`](agents/gitops-steward.yaml) | names no controller; finds the instance that serves the application, refuses ambiguity |
| Skill | [`skills/gitops-troubleshooting`](skills/gitops-troubleshooting/SKILL.md) | drift or pending commit, partial sync, noise, hooks, what blocks a sync |
| Template | [`templates/gitops-change`](templates/gitops-change/README.md) | alert, diagnosis (agent), review (platform owner), verification, done |
| Evals | [`evals/gitops-steward.yaml`](evals/gitops-steward.yaml) | instruction checks, and live cases on a provider's fake |
| Conformance | [`conformance/`](conformance) | pytest suite a server runs against itself |

## The profile

ADR 0224: a profile is a contract of tools. A server that `implements: [gitops/gitops@1]` serves all of them,
with a schema compatible with the profile's, and governance at least as strict. The platform names an
organisation's instances `<instance>__<tool>`; the agent does not know which controller is behind one.

| Tool | Access | Governance floor |
|:--|:--|:--|
| `get_provider`, `list_applications`, `get_application`, `list_resources`, `get_diff`, `list_events`, `get_sync_windows`, `get_history`, `get_revision_metadata` | read | none |
| `plan_sync` | read | none; says what a sync would do and what blocks it, writes nothing |
| `refresh_application` | write | none: asks the controller to re-read git, changes nothing that runs |
| `sync_application` | write | `human_approval`; idempotent through the platform's `action_key` |
| `rollback_application` | write | `human_approval`; compensable: a sync back to the revision that was in force |
| `set_auto_sync` | write | `human_approval`; compensable: the result carries `previous_enabled` |

Vocabulary: an **application** has a **scope** (the controller's project, namespace or tenant), a **sync status**
(`Synced`, `OutOfSync`, `Unknown`) and a **health**. A server serves the scopes of its allow-list and refuses
the rest.

Left out on purpose: stopping a running operation (controller-specific; it is a blocker the agent reports),
creating, deleting or editing an application, projects and RBAC, and a git change (the agent writes it as text).

## Conformance

[`conformance/`](conformance) is a package (`tranibor-gitops-conformance`). A server's tests subclass
`Conformance`, give it a `gitops_target` fixture and inherit the checks: every tool served, schemas, governance
floor, allow-list refusal before any write, plan writes nothing, sync idempotent for one `action_key`,
rollback and pause with their compensations rendered from the descriptor and run. Each provider plugin declares
it as a path dev-dependency (`[tool.uv.sources]`, editable) of its `server/pyproject.toml`; the image does not
install it. See [`conformance/README.md`](conformance/README.md).

## Not proven yet

`validate` and `pack` need a core that knows `contents.profiles`, `implements: <profile>` and
`mcp_servers[].profile` (core stories S86-01/S86-02). The core floor in `plugin.yaml` is a placeholder until a
release carries them, and the evals' live cases have never run.
