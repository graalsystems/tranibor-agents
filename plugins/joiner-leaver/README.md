# Joiner-leaver plugin

Onboarding, role change and offboarding of a person across systems: an agent turns a request into a
checklist and a plan, the manager and IT approve it, each write is its own governed action, a read-back
checks the result. **This plugin ships no server**: it orchestrates the connectors of other plugins.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `joiner-leaver` 0.1.0, core `>=0.28.0,<1.0.0` (`spawn:` and `approval.excluded`, as `access-requests` 0.2.0) |
| Agent | [`agents/people-ops-it.yaml`](agents/people-ops-it.yaml) | reads Keycloak, plans, verifies; no write tool; refuses a request that names no approver |
| Skill | [`skills/jml-checklist`](skills/jml-checklist/SKILL.md) | the joiner / mover / leaver method, segregation of duties, the four-eyes rule, the manual tasks |
| Skill | [`skills/role-catalogue`](skills/role-catalogue/SKILL.md) | how an organisation describes role → groups as a knowledge base; a sample table to replace |
| Template | [`templates/joiner-leaver`](templates/joiner-leaver/README.md) | request → plan → manager and IT approve → one governed write each → read-back → closed |
| Evals | [`evals/people-ops-it.yaml`](evals/people-ops-it.yaml) | instruction checks (level 1, deterministic) |
| Tests | [`tests/test_template.py`](tests/test_template.py) | the template against a core checkout (CI's `templates` job) |

## Connectors

| Connector | From | Used for | Required |
|:--|:--|:--|:--|
| `keycloak` | the `keycloak-admin` plugin's server (an organisation connector named `keycloak`, capability `mcp`) | reads by the agent: `find_users`, `get_user`, `list_user_groups`, `list_groups`, `get_group_members`, `list_user_sessions`; writes by the template, each governed: `create_user`, `add_user_to_group`, `remove_user_from_group`, `disable_user`, `logout_user`, `send_required_actions_email` (and `enable_user`, only as the undo of a disabling) | yes |
| `microsoft-365` | the `microsoft-365` plugin | **not used.** Its server is delegated read access to one person's own calendar, mail and chats (`list_events_with`, `search_mail_with`, `search_chats_with`): it can neither notify nor administer, and no admin tool is invented | no |

The plugin manifest has no way to mark an organisation connector optional: every one listed is required,
which is why only `keycloak` is listed. Install and configure `keycloak-admin` first.

## Not covered yet

| System | Status |
|:--|:--|
| GitHub organisation (membership, teams) | a later plugin; today a **manual task** in every plan |
| Entra / Microsoft 365 licences and mailboxes | a later plugin; manual task |
| Hardware (laptop, phone, badge: hand-over, return, wipe) | a later plugin; manual task |
| Shared mailboxes, ownership transfer, other SaaS tools | manual tasks |

The agent lists these in every plan (`manual_tasks`) so that nobody believes a person is offboarded
because Keycloak is: a person closes the work item with an attestation that each was done or why not.

## Governance

| Step | Who decides | How |
|:--|:--|:--|
| The request | names an **approver** | no approver (or the person themselves), no plan: the agent refuses and a person reads it |
| The plan | the person's manager, then IT | two human steps before the first write; rejected by either, the request is declined |
| Each write | the approvers of the group the catalogue names (`jml-approvers-<suffix>`) | one governed action per account, per group, per invitation, per disabling, per sign-out; re-authenticated within 5 minutes; **never** the person concerned, who asked, nor the person's manager |
| When | no earlier than `effective_date` | the start date, the move date or the last day |
| Undo | `remove_user_from_group` / `add_user_to_group` / `enable_user` | a group write and a disabling are compensated; account creation, invitation and sign-out are not (nothing to undo) |
| Deleting | nobody | no workflow calls a delete, and Keycloak's server has no such tool: a leaver is **disabled and signed out**, never deleted |
| Closing | a person (IT) | only a read-back that confirms the plan reaches the closing task; the task lists the manual tasks |

The target of every write is the request's own `username`, taken from the work item, never from what the
agent wrote: a plan cannot aim a write at another account. A refused or abandoned write does not stop the
others, and the read-back then reports `verified: false` and sends the request to a person.

## How to validate

```sh
tranibor plugin validate plugins/joiner-leaver
tranibor plugin pack plugins/joiner-leaver -o joiner-leaver-0.1.0.tar.gz   # same bytes every time
# from a core checkout, as CI's `templates` job does:
TRANIBOR_CORE=$PWD uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/joiner-leaver/tests
```

## What is and is not proven

Proven here: the manifests validate and pack; the seven workflows parse strictly; the plan, the fan-out
and each governed write are rendered as the platform renders them; the agent's eval case holds on its
rendered instructions. **Not proven**: a run against a live Keycloak or the real orchestrator (the
`keycloak-admin` server is built in parallel; the tool and argument names used were checked against its
`servers/keycloak-admin.yaml`), the agent's behaviour on a real model, an empty list opening no child
in the orchestrator beyond what its source says, and the agent reading the work item's `state` through
`get_ticket` to know its phase.
