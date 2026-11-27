---
id: connector.mcp.identity-keycloak
title: Connect Keycloak realms
applies_to: {connector: mcp/identity-keycloak}
audience: org_admin
version: 1
reviewed: 2026-11-26
vendor_docs:
  - {title: "Keycloak Server Administration Guide: service accounts and client credentials", url: "https://www.keycloak.org/docs/latest/server_admin/#_service_accounts"}
  - {title: "Keycloak Server Administration Guide: managing clients and their secret", url: "https://www.keycloak.org/docs/latest/server_admin/#assembly-managing-clients_server_administration_guide"}
  - {title: "Keycloak Admin REST API reference", url: "https://www.keycloak.org/docs-api/latest/rest-api/index.html"}
  - {title: "Keycloak Server Administration Guide: auditing and events", url: "https://www.keycloak.org/docs/latest/server_admin/#auditing-and-events"}
  - {title: "Keycloak Server Administration Guide: required actions", url: "https://www.keycloak.org/docs/latest/server_admin/#_required-actions"}
egress: ["<your Keycloak host>"]
fields:
  base_url: "https://<your Keycloak host>, without a trailing slash and without /admin (a server that still serves /auth writes it here)"
  realms: "the names of the realms this connector may touch, not their display names; any other realm is refused, even to read"
  client_id: "the clientId of the confidential client created below"
  client_secret: "its secret (Credentials tab), typed once"
  token_realm: "only when the client lives in another realm than the first one listed (typically master); empty otherwise"
---

## Before you start
- Keycloak 26.x (the connector is written against its Admin REST API; 17 and later serve no `/auth`
  prefix). You administer the realms, or are an administrator of the `master` realm.
- The `identity` and `identity-keycloak` plugins installed in your organisation (Catalogue, **Plugins**). The
  server runs in the platform; there is nothing to host. A plugin that brings a server needs a second person's
  approval.
- Keycloak reachable over HTTPS **from the platform**. A public address works now. A Keycloak on a private
  network does not yet: it needs the platform's private-network support (ADR 0224 section 6: private CIDRs or
  an outpost), which is not available. Do not work around it (no tunnel, no public re-exposure for the
  purpose): wait for it.
- Not run against a real server: this procedure and the connector were written from Keycloak's
  documentation and exercised on a seeded fake only. Run the server's live suite (below) once on a
  non-production realm before relying on it, and tell the plugin's maintainers what differs.

## At Keycloak

### Create the client
In the admin console, in the first realm of your list (or in `master` for a client that administers several
realms, then fill `token_realm`):

1. **Clients**, **Create client**. Client type **OpenID Connect**, a Client ID such as `tranibor-iam`.
2. **Capability config**: **Client authentication** on (confidential), **Service accounts roles** on.
   Turn **Standard flow**, **Direct access grants** and **Implicit flow** off: this client never logs a
   person in.
3. Save. On the **Credentials** tab, keep **Client Authenticator** at *Client Id and Secret* and copy the
   secret. You will type it once in Tranibor.

### Turn events on
Dormant-account reviews read sign-in events; Keycloak records them only when asked. In **Realm settings**,
**Events**, in every realm listed:

- **User events settings**: *Save events* on, and *Saved types* including at least `LOGIN`, `LOGIN_ERROR`
  and `LOGOUT`. Set an expiration that covers the dormancy window you review (default 90 days: keep 180).
- **Admin events settings**: *Save events* on, and *Include representation* **off** unless you need it.
  (The connector never returns the representation either way, because it can hold a secret.)

Without sign-in events, `dormant_users` says it is not reliable and proposes nothing from them.

### Turn mail on, to onboard people
`send_setup_email` needs an SMTP server in each realm (**Realm settings**, **Email**) and an address on the
user. Without it the connector says so in `get_directory` (`mail_configured: false`) and the plan for an
arrival says the person must be given their first sign-in another way.

## Permissions

On the client's **Service accounts roles** tab, **Assign role**, filter by clients, and pick the client
`realm-management` (in `master`, the client is named after the realm, `<realm>-realm`), **in each realm
listed**. Give the least that works, in one of two variants:

| Variant | Roles of `realm-management` | What the connector can then do |
|:--|:--|:--|
| Read-only (start here) | `view-users`, `view-clients`, `view-realm`, `view-events` | every read: users, groups, members, sessions, second factors, administrators, clients, realm summary, events, dormant accounts, plans. Every write fails with `forbidden`. |
| Read-write | the read-only roles, plus `manage-users` | also: create a user, change group membership, disable and enable, end sessions, send setup emails. |

Do **not** give `manage-realm`, `manage-clients`, `realm-admin`, `impersonation` or `manage-events`:
the connector has no tool that needs them, and it is built never to change realm settings, touch a client's
secret, impersonate, or delete a user. A missing role shows up as `forbidden` naming the role family, not a
silent partial result.

Notes:
- Sessions are read per user (`GET /users/{id}/sessions`), which needs `view-users`. The group tools use
  `view-users` as well.
- Second factors are read from `GET /users/{id}/credentials` (`view-users`), keeping the credential **type**
  only; the credential's data is dropped in the server and never returned.
- Administrators are read from the holders of the `realm-management` client roles. Realm roles and
  composite roles are not followed: the answer says so (`scope`), and an agent must not conclude that nobody
  else is an administrator.

### What the connector will never do

It has no tool to delete a user, read a credential, impersonate, change the realm's settings, or read or
change a client's secret. The service account you created does not hold the roles that would allow most
of them either, so a misconfigured agent could not reach them through it.

## In Tranibor

| Field | Value |
|:--|:--|
| `base_url` | `https://sso.example.com` |
| `realms` | the realms' names, e.g. `staff`, `contractors` |
| `client_id` | `tranibor-iam` (the Client ID you chose) |
| `client_secret` | the secret copied when you created the client |
| `token_realm` | empty, unless the client is in another realm than the first one listed |

The connector gets a token with `POST {base_url}/realms/{token_realm or first realm}/protocol/openid-connect/token`
(`grant_type=client_credentials`) and renews it before it expires; it calls
`{base_url}/admin/realms/{realm}/...` for the realms listed, and for no other.

### One connector per Keycloak, or several
Each realm you list is a "directory" in the `identity` profile; the agents read `get_provider` on every
connected instance to pick the one that serves the realm a request names. Two Keycloak servers are two
connectors.

### Rotate the secret
1. In Keycloak, on the client's **Credentials** tab, **Regenerate** the secret. The old one stops
   working at once.
2. In Tranibor, edit the connector and type the new `client_secret`.
3. Open the connector's test. An `auth_failed` means the secret is wrong or the client was disabled.

Keycloak's client-secret rotation policies are configured in the realm's client policies and are not touched
by this connector.

## Test it

`server/tests/live/` runs the read tools against a real realm, skipped unless these are set:
`TRANIBOR_LIVE_KEYCLOAK_BASE_URL`, `TRANIBOR_LIVE_KEYCLOAK_REALM`, `TRANIBOR_LIVE_KEYCLOAK_CLIENT_ID`,
`TRANIBOR_LIVE_KEYCLOAK_CLIENT_SECRET` (and optionally `TRANIBOR_LIVE_KEYCLOAK_TOKEN_REALM`). It performs no
write unless `TRANIBOR_LIVE_KEYCLOAK_WRITE=1`, and then only on a user it creates itself, named
`conf-live-<random>`, which it disables and leaves in place (no tool deletes a user: remove it by hand).
`TRANIBOR_LIVE_REQUIRED=keycloak` turns a missing variable into a failure.

## When it fails

- `auth_failed`: the secret is wrong, was regenerated, or the client is disabled; check `token_realm`.
- `forbidden`: the service account lacks a role from the Permissions table, in that realm.
- `not_found` on every call: `base_url` carries a stale `/auth` prefix, or a realm name is wrong.
- `invalid_arguments` naming the allow-list: the realm asked for is not in `realms`.

### What is not confirmed in the documentation

Nothing here was run against a real server.

- The exact query parameters of `GET /users` (`search`, `exact`, `enabled`, `briefRepresentation`),
  `GET /events` (`type`, `user`, `dateFrom`) and `GET /admin-events` (`operationTypes`, `resourceTypes`,
  `authUser`, `resourcePath`) are modelled on the REST API reference as of 26.x and on the admin console's
  own calls.
- `GET /groups/{id}/children` with `subGroupsCount`, and the `subGroupCount` field, depend on the release.
- Whether a `PUT /users/{id}` with the full representation leaves attributes untouched on every release.
- `GET /users/{id}/credentials`: that it answers `type` for each credential and that `credentialData` and
  `secretData` are masked or present is not confirmed; the server keeps the type only either way.
- `GET /clients/{id}/roles/{role}/users` lists the **direct** holders of a role only (no group member, no
  composite). That is why group role mappings are read too.
- `GET /groups/{id}/role-mappings/clients/{clientId}` does not include composite roles; whether a sub-group
  inherits its parent's role mappings is assumed (the server applies it by path prefix) and not confirmed.
- `GET /users/count` answering a bare integer.
- The roles listed under Permissions were not verified by running each tool with only those roles.
