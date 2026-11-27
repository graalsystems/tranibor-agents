---
id: connector.mcp.gitops-argocd
title: Connect an Argo CD instance
applies_to: {connector: mcp/gitops-argocd}
audience: org_admin
version: 1
reviewed: 2026-11-26
vendor_docs:
  - {title: "Argo CD user management (local accounts, API keys)", url: "https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/"}
  - {title: "Argo CD RBAC configuration", url: "https://argo-cd.readthedocs.io/en/stable/operator-manual/rbac/"}
  - {title: "Argo CD sync windows", url: "https://argo-cd.readthedocs.io/en/stable/user-guide/sync_windows/"}
  - {title: "Argo CD selective sync", url: "https://argo-cd.readthedocs.io/en/stable/user-guide/selective_sync/"}
  - {title: "Argo CD automated sync policy", url: "https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/"}
  - {title: "Argo CD diffing customization (ignoreDifferences)", url: "https://argo-cd.readthedocs.io/en/stable/user-guide/diffing/"}
  - {title: "argocd account generate-token", url: "https://argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_account_generate-token/"}
  - {title: "Argo CD API", url: "https://argo-cd.readthedocs.io/en/stable/developer-guide/api-docs/"}
egress: ["<argocd host>"]
fields:
  base_url: "https://argocd.example.com, the Argo CD API server, without a trailing slash"
  token: "the API key of the local account `tranibor`, typed once"
  projects: "the Argo CD projects whose Applications Tranibor may see and act on, for instance `shop`; they are the scopes of the gitops profile"
  ca_cert: "optional: the PEM of the CA that signs the server's certificate, when it is not a public one"
---

## Before you start

- An Argo CD you administer, reachable over HTTPS from Tranibor's server. The `gitops` and `gitops-argocd` plugins installed in
  your organisation (Catalogue, **Plugins**); its server runs in the platform, there is nothing to host. A
  plugin that brings a server needs a second person's approval.
- The AppProjects whose Applications Tranibor should supervise. The `projects` field is an allow-list the
  server enforces on every call; the Argo CD role below is the second fence.
- Tranibor never deletes or creates an Application, edits an Application's source or destination, or changes a
  project or RBAC. The one edit it makes is the auto-sync setting of an Application (`set_auto_sync`), which
  needs the `update` right: that right is **broader** than the tool, since Argo CD cannot grant "update the sync
  policy only". Leave `update` out to keep Tranibor from pausing auto-sync; the other tools keep working.
- Not proven: this procedure and the server were written from Argo CD's documentation and tested against a
  seeded fake of its API. Nothing was run against a real Argo CD. The points the documentation did not let
  us confirm are listed at the end of this page.

## At Argo CD

1. **A local account with an API key.** In the `argocd-cm` ConfigMap add the account, API key only, no
   login (user management doc):

   ```yaml
   data:
     accounts.tranibor: apiKey
     accounts.tranibor.enabled: "true"
   ```

2. **Its rights**, in `argocd-rbac-cm`, `policy.csv` (RBAC doc). Read on the project, the right to sync it, and to pause its auto-sync. Replace `shop` by your project. The syntax is
   `p, <subject>, <resource>, <action>, <project>/<application>, allow`:

   ```csv
   p, role:tranibor-read, applications, get, shop/*, allow
   p, role:tranibor-read, projects, get, shop, allow
   p, role:tranibor-sync, applications, sync, shop/*, allow
   p, role:tranibor-sync, applications, update, shop/*, allow
   p, role:tranibor-sync, applications, action/*, shop/*, deny
   g, tranibor, role:tranibor-read
   g, tranibor, role:tranibor-sync
   ```

   - `get` on applications covers every read the server makes: the application, its resource tree, managed
     resources, events, history and revision metadata, and the sync windows of the project.
   - `sync` covers `sync_application` and `rollback_application` (both are operations the sync right
     starts). A refresh is a `get` with a refresh parameter.
   - `update` covers `set_auto_sync`, which patches the Application's sync policy and one annotation
     (`tranibor.io/paused-automated`, where a pause keeps the settings it removed, so a resume restores them).
     Not confirmed: that `update` is the right Argo CD checks for that patch (see the end of this page).
   - Do **not** add `delete`, `create`, `override`, `projects` write rights, or `*`. To supervise only, leave
     out `role:tranibor-sync`: the writes then fail with `403` and the reads work.
   - Check it: `argocd admin settings rbac can tranibor sync applications 'shop/my-app' --policy-file policy.csv`
     answers `Yes`, and the same with `delete` answers `No`.
3. **The token** (`argocd account generate-token`):

   ```sh
   argocd account generate-token --account tranibor --expires-in 90d
   ```

   Shown once. Put it in your secret store. **Rotation**: generate a new token before the old one expires,
   set it in Tranibor (below), check the connector, then delete the old one with
   `argocd account delete-token --account tranibor <id>` (the id is in `argocd account get --account tranibor`).
   A calendar reminder 7 days before expiry is cheaper than an incident.

## Permissions

What the server calls, and the Argo CD right behind each:

| Tool | Argo CD right | Approval in Tranibor |
|:--|:--|:--|
| get_provider | none (answers from the configuration) | none (read) |
| list_applications, get_application, list_resources, get_diff, list_events, get_sync_windows, get_history, get_revision_metadata, plan_sync | `applications, get` | none (read) |
| refresh_application | `applications, get` (a refresh query parameter) | none: a write that changes nothing in the cluster |
| sync_application | `applications, sync` | **required**; idempotent through the platform's `action_key` |
| rollback_application | `applications, sync` | **required**; compensated by a sync back to the revision that was in force |
| set_auto_sync | `applications, update` | **required**; compensated by `set_auto_sync` with the previous value |

`plan_sync` is a read: it compares the live state to git and lists what a sync would change. Argo CD's
own `dryRun` option starts an operation that overwrites the Application's `operationState`, which would
hide a lingering partial sync, so the server does not use it.

The server's descriptor is the floor; an organisation can only make the approvals stricter.

## In Tranibor

1. Catalogue, **Plugins**, `gitops-argocd`, **Configure**.
2. `base_url`: `https://argocd.example.com`. `token`: the token of step 3, sealed, shown once.
3. `projects`: the same project names as in the role.
4. `ca_cert`, if Argo CD's certificate is signed by a private CA.
5. Create a project from the template **GitOps · drift under review**. Put the people who
   decide on a sync in the `platform-owners` group.

## Test it

1. Press **Test** on the connector: `get_provider` names the instance and its projects, `list_applications` answers with the allowed projects' Applications.
2. Open a work item from the template naming one Application. The steward runs and posts its report.
3. Approve the sync: the Application's history gains an entry, and the steward's verification says Synced
   and Healthy.

## When it fails

- `401`: the token is wrong, expired or its account is disabled (`accounts.tranibor.enabled`).
- `403` on a read: the role lacks `applications, get` on that project; on a sync: `applications, sync`.
- An Application is not listed: its project is not in `projects`, or the role does not cover it. The server
  also refuses it by name, saying so.
- `TLS` errors: Argo CD's certificate is signed by a private CA; set `ca_cert`.
- A sync is refused as "another operation is running": wait for it, or ask an Argo CD administrator to terminate it (this plugin has no tool for that).
- A sync is refused for a deny window: the project's sync window denies manual syncs until it ends.
- A rollback, or a sync to a given revision, is refused: Argo CD refuses it while auto-sync is enabled; pause it with `set_auto_sync` (approval), or revert in git.
- A pause does not stick, or the Application flips back: another Application manages it (app-of-apps) and reverts the patch. The tool warns when it sees the tracking annotation or label.

**Not confirmed in the documentation:**

- The exact fields of a resource-tree node for every Kubernetes kind; the server reads only the common ones.
- That a managed resource's `targetState`, `liveState`, `normalizedLiveState` and `predictedLiveState` are
  JSON texts (as the API's swagger shows), and what they hold for a resource missing on one side.
- Whether the application events endpoint answers without a resource filter on every release.
- That the `get` right is enough for the project's sync windows endpoint on every release.
- That `applications, update` is the right the PATCH endpoint checks, and that it suffices (no `override`, no `sync`).
- Whether Argo CD refuses a sync to a `revision` other than the target while auto-sync is on (the server refuses it first), and how a multi-source Application takes a single `revision`.
- That a merge patch with `automated: null` removes the setting and that an empty `automated: {}` re-enables it with the defaults, on every release.
- Whether a parent Application's self-heal reverts the patch (the server only warns).
All of these are handled defensively, and the live suite (`server/tests/live`) checks them against a real
instance when its environment variables are set.
