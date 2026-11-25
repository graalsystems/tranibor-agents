# Keycloak admin plugin

Review and tidy a Keycloak realm's access with a Tranibor agent, under human approval.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `keycloak-admin` 0.1.0, core `>=0.29.2,<1.0.0` |
| Agent | [`agents/iam-steward.yaml`](agents/iam-steward.yaml) | reads the realm, cites evidence, proposes; every write waits for a person |
| Skill | [`skills/keycloak-access-review`](skills/keycloak-access-review/SKILL.md) | least privilege, dormant-account evidence, pre-upgrade checks |
| Template | [`templates/access-review`](templates/access-review/README.md) | queue → awaiting decision (realm owners) → done; schedule is project config |
| Evals | [`evals/iam-steward.yaml`](evals/iam-steward.yaml) | instruction checks, and five live cases on the fake realm |
| Procedure | [`procedures/connector.mcp.keycloak-admin.md`](procedures/connector.mcp.keycloak-admin.md) | create the client, its service-account roles (read-only and read-write), rotate the secret |
| Server | [`servers/keycloak-admin.yaml`](servers/keycloak-admin.yaml), [`server/`](server) | the MCP server over the Admin REST API (Keycloak 26.x), with a seeded `fake` |

## Connector

Server name `keycloak`. Configuration: `base_url` (https), `realm`, `client_id`, `client_secret` (secret),
optional `token_realm` (the realm that issues the token, if not `realm`). Auth is the client-credentials
grant with a cached, renewed token. Egress is limited to `base_url`.

## Governance

| Tool | Access | Approval | Compensation |
|:--|:--|:--|:--|
| `find_users`, `get_user`, `list_user_groups`, `list_groups`, `get_group_members`, `list_user_sessions`, `list_clients`, `get_realm_summary`, `list_admin_events`, `list_login_events`, `dormant_users`, `users_without_group` | read | none | n/a |
| `create_user` | write | yes, `action_key`, idempotent on username | none (no delete exists) |
| `add_user_to_group` | write | none | `remove_user_from_group` |
| `remove_user_from_group` | write | none | `add_user_to_group` |
| `disable_user` | write | yes | `enable_user` |
| `enable_user` | write | none | n/a |
| `logout_user` | write | yes | none |
| `send_required_actions_email` | write | yes, `action_key` | none |

The connector never deletes a user, reads a credential, impersonates, changes realm settings or manages
client secrets. Tool results are whitelisted views: secrets, credentials, user attributes and admin-event
representations never leave the server.

## Validate

```sh
tranibor plugin validate plugins/keycloak-admin
tranibor plugin pack plugins/keycloak-admin -o keycloak-admin-0.1.0.tar.gz   # same bytes every time
cd plugins/keycloak-admin/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv` nor caches), as CI does.

## Not verified

The suite runs on the fake realm and mocked HTTP only. No real Keycloak was reached. The live suite
(`server/tests/live/`) is skipped without `TRANIBOR_LIVE_KEYCLOAK_*` variables. Endpoint details that could not
be confirmed in the official documentation are listed in the procedure's "What is not confirmed" section.
