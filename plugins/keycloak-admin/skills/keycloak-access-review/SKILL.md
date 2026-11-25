---
name: keycloak-access-review
description: Least-privilege and pre-upgrade review checklist for a Keycloak realm — who should hold access, dormant accounts, client hygiene, events, and what to check before a major Keycloak upgrade (custom themes, deprecated features). Use when reviewing access in a realm or preparing an upgrade.
---

# Reviewing a Keycloak realm

This is a checklist, not a procedure: for each line, read what answers it, and write down what you
**could not** see. The connector reads users, groups, clients, sessions, events and a minimised realm
summary. It does not read role mappings, client roles, credentials, identity providers, authentication
flows, themes or keys: those lines are marked "console" below, a person checks them in the admin console
or the realm export, and a review must not claim them done.

## 1. Least privilege: who holds access

| Check | Where | Finding when |
|:--|:--|:--|
| Access is granted through groups, not user by user | `list_user_groups` | users with no group (`users_without_group`) |
| Groups map to a job, not to "everyone" | `list_groups` with member counts | one group holds most of the realm; a team group above the threshold |
| Administrative access is a short list | group named for admins, `get_group_members` | more people than the role needs; people who left |
| Role mappings of each group are minimal | console | `realm-admin`, `manage-users`, `impersonation` on a broad group |
| Accounts of people who left are disabled | `dormant_users`, the HR list | enabled and unused; disabled but sessions open (`list_user_sessions`) |

Never propose removing a person from the group that holds the realm's administration without saying who
else keeps access: do not leave the realm with no administrator.

## 2. Dormant accounts: what the evidence is

`dormant_users` relies on LOGIN events of the window and on sessions' last access. It is evidence of
non-use, not proof.

- Events must be enabled and kept longer than the window; otherwise `reliable` is false. Say so.
- Accounts created inside the window are not judged.
- Service accounts, accounts that log in only through a federated identity provider without events, and
  accounts used only through refresh tokens can look dormant. Confirm with `list_login_events` and
  `list_user_sessions` for the user before proposing a disable.
- Disable, never delete. Pair a disable with `logout_user` when sessions are open.

## 3. Clients (applications)

Look in `list_clients` for, and report with the clientId:
- public clients with the implicit flow enabled (deprecated; standard flow with PKCE replaces it);
- redirect URIs that are `*` or a bare scheme, or an `http://` URI outside localhost;
- enabled clients with no sign of use in recent LOGIN events (`list_login_events` with `client`);
- confidential clients with direct access grants (password grant) enabled that do not need it;
- service-account clients: their roles are a console check, and their secret is never read here.

## 4. Realm settings (from `get_realm_summary`)

- brute-force protection on; a password policy set (length, not the username);
- `sslRequired` is `external` or `all`; open self-registration is a decision, not a default;
- token and SSO session lifespans are what the organisation intends (very long idle or max lifespans are a finding);
- login events and admin events on, with a retention that covers the review window;
- `smtp_configured` true if the organisation relies on email for password reset and required actions.

## 5. Admin events worth a lead

Role or group changes made by someone who does not usually administer; a client secret created or changed
(`CLIENT_SECRET`) outside a rotation window; a user created then quickly disabled or given a privileged
group; changes from an unfamiliar IP address or at odd hours. A lead is reported with who, what, when,
where, and what is not verified; the event's representation is never available through this connector.

## 6. Before a major upgrade (for example to 26.x or later)

Read the upgrade guide and release notes of the target version first
(https://www.keycloak.org/docs/latest/upgrading/index.html), then check what this realm depends on.

| Check | How | Notes |
|:--|:--|:--|
| Custom themes (login, account, email, admin) | `get_realm_summary` names the theme in use (`loginTheme`, `accountTheme`, `emailTheme`, `adminTheme`) | A theme that is not a `keycloak*` built-in is custom: it is a file deployed on the server, and it has to be rebuilt and tried against the new version; the account console and admin console themes changed between major versions. The connector cannot read the theme's files: that is a console and filesystem check. |
| Deprecated features and flows | realm export / console; `list_clients` for implicit flow and direct access grants | Features announced as deprecated or removed in the upgrade guide; legacy flows a client still uses. |
| Custom providers (SPIs), extensions | server filesystem | Not visible through the Admin API. |
| Database and cache configuration | server configuration | Not visible through the Admin API. |
| Clients depending on removed behaviour | `list_clients` | Redirect URI rules, token lifespans, and flows each client uses. |
| A rollback exists | operations | A database backup taken before the upgrade, and a staging realm export imported and tried. |

State plainly which of these the review verified and which are left to a person. Never state that an
upgrade is safe: the review can only say what it found and what it could not see.

## 7. Writing a proposal

One finding, one proposed change, with the evidence next to it. Prefer: remove a membership over disable;
disable over anything else; never delete. A proposal names the user (username and id), the group or
action, and the evidence, so that the person approving can decide without opening Keycloak.
