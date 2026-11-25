# identity plugin

Review and change who has access, at any directory, with a Tranibor agent, under human approval. This plugin
holds what does not depend on the provider: the **profile** every identity server must serve, the two agents,
their skills, the templates, the evals and the conformance suite. A provider plugin brings the server:
[`identity-keycloak`](../identity-keycloak/README.md).

A **directory** is a set of people and their access: users, groups, roles, sessions, credential state and
required actions. At Keycloak it is a realm; the profile calls it a `directory` and an instance lists its own
in `get_provider`.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `identity` 0.1.0, `contents.profiles`; core `>=0.30.0,<1.0.0` is a placeholder (see below) |
| Profile | [`profiles/identity.yaml`](profiles/identity.yaml) | `identity/identity@1`: 24 tools, their schemas and the governance floor |
| Agent | [`agents/identity-steward.yaml`](agents/identity-steward.yaml) | audits dormant accounts, accounts without a second factor, administrators and group drift; proposes governed changes |
| Agent | [`agents/joiner-leaver.yaml`](agents/joiner-leaver.yaml) | turns a joiner or leaver request into the ordered writes; refuses a request without an approver |
| Skills | [`skills/`](skills) | `identity-review`, `jml-checklist`, `role-catalogue` (a sample table to replace) |
| Templates | [`templates/identity-review`](templates/identity-review/README.md), [`templates/joiner-leaver`](templates/joiner-leaver/README.md) | request, agent, review by a person, done |
| Evals | [`evals/`](evals) | instruction checks, and live cases on a provider's fake |
| Conformance | [`conformance/`](conformance) | pytest suite a server runs against itself |

Neither agent names a provider: they work with whichever identity server plugins the organisation installed,
and find the instance that serves a directory with `get_provider`.

## The profile

ADR 0224: a profile is a contract of tools. A server that `implements: [identity/identity@1]` serves all of
them, with a schema compatible with the profile's, and governance at least as strict. The platform names an
organisation's instances `<instance>__<tool>`.

| Tool | Access | Governance floor |
|:--|:--|:--|
| `get_provider`, `get_directory` | read | none |
| `find_users`, `get_user`, `list_user_groups`, `list_groups`, `get_group_members`, `list_user_sessions` | read | none |
| `list_privileged_users`, `dormant_users`, `users_without_mfa`, `users_without_group`, `compare_group_members` | read | none; each says what it did not read |
| `list_sign_in_events`, `list_admin_events` | read | none; never returns a secret or a representation |
| `plan_joiner`, `plan_leaver` | read | none; compute the ordered calls and their warnings, write nothing |
| `create_user` | write | `human_approval`; idempotent on the username and through the platform's `action_key`; sets no password |
| `add_user_to_group`, `remove_user_from_group` | write | idempotent; compensated by the opposite call |
| `disable_user`, `enable_user` | write | `human_approval`; idempotent; compensated by each other |
| `logout_user` | write | `human_approval`; ends the open sessions |
| `send_setup_email` | write | `human_approval`; through the platform's `action_key` |

Writes address a person by `user_id` or by `username` (exactly one). Left out on purpose: deleting a user,
passwords and credentials, impersonation, roles and realm or client settings.

## Conformance

[`conformance/`](conformance) is a package (`tranibor-identity-conformance`). A server's tests subclass
`Conformance`, give it an `identity_target` fixture and inherit 23 checks: every tool served, schemas, create
idempotent, compensation of memberships and of disable, allow-list refusal before any upstream call (and
look-alike names), plans write nothing, no secret in any answer. See
[`conformance/README.md`](conformance/README.md).

## Not proven yet

`validate` and `pack` need a core that knows `contents.profiles`, `implements: <profile>` and
`mcp_servers[].profile` (core stories S86-01/S86-02). The core floor in `plugin.yaml` is a placeholder until a
release carries them, and the evals' live cases have never run.
