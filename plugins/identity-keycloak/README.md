# identity-keycloak plugin

The Keycloak server of the [`identity`](../identity/README.md) profile: it serves `identity/identity@1` for
Keycloak realms (Admin REST API), under human approval. Install it with the `identity` plugin; the agents that
use it are `identity-steward` and `joiner-leaver`, which name no provider.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `identity-keycloak` 0.1.0, requires `identity` |
| Server | [`servers/identity-keycloak.yaml`](servers/identity-keycloak.yaml), [`server/`](server) | `implements: [identity/identity@1]`, over the Admin REST API, and its `fake` |
| Procedure | [`procedures/connector.mcp.identity-keycloak.md`](procedures/connector.mcp.identity-keycloak.md) | client, roles, events, mail, rotation |

```sh
tranibor plugin validate plugins/identity-keycloak
tranibor plugin pack plugins/identity-keycloak -o identity-keycloak-0.1.0.tar.gz   # same bytes every time
cd plugins/identity-keycloak/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Reaching the system

The platform calls Keycloak over HTTPS from where it runs.

- **A public endpoint works now.** Give the connector the realm's public base URL.
- **A private endpoint does not yet.** A Keycloak on a private network needs the platform's private-network
  support, ADR 0224 section 6 (private CIDRs, or an outpost next to the system), which is not available. Do
  not work around it: no tunnel, no public re-exposure for the purpose. Wait for it.

## Connector

`base_url`, `client_id`, `client_secret` (a secret field) and `realms`, an allow-list: any realm outside it is
refused, **even on read**, before any call to Keycloak (`token_realm` only when the client lives in another
realm). Egress is limited to the host in `base_url`. An instance names itself with `TRANIBOR_INSTANCE_NAME`
(default `identity-keycloak`): that is what `get_provider` answers. Each realm is a `directory` of the profile.

## Tools and governance

All 24 tools of the profile, with its governance floor kept (see the [profile table](../identity/README.md)),
plus two reads only Keycloak has:

| Tool | Access | Governance |
|:--|:--|:--|
| `list_clients` | read | none; clients without their secret, registration token or attributes |
| `get_realm_summary` | read | none; realm settings without SMTP, keys, components or identity providers |

No tool deletes a user, reads a credential's data, impersonates, or touches a client's secret.

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/identity/conformance`](../identity/conformance))
against this server over the fake. The suite is a path dev-dependency of `server/pyproject.toml`
(`tranibor-identity-conformance`, editable), installed by `uv sync --all-extras --dev`; the image installs no dev
group. `server/tests/live/` runs the same suite against a throwaway realm of a real Keycloak, skipped without
`TRANIBOR_LIVE_IDENTITY_KEYCLOAK_*`.

## The fake

`KEYCLOAK_FAKE=1` serves a seeded Keycloak: realm `demo` (49 users, groups, administrators, sign-in and admin
events, one user with no sign-in) and a second realm outside the default allow-list that holds a marker string
the tests prove never leaks. The unit tests and the evals run on it.

## Not confirmed

Not run against a real Keycloak. Points the public documentation did not settle (the credentials endpoint's
masking, the direct-holders-only reads of client roles, group role mappings without composites, the bare
integer of `/users/count`, subgroup inheritance of role mappings, the query parameters of users and events,
`subGroupCount`, whether a user update keeps attributes) are listed in the procedure; realm roles and
composite roles are not followed, and `list_privileged_users` says so in its `scope`.
