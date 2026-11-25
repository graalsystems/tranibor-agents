# Argo CD · drift and sync under approval

An alert or a request about an Argo CD Application becomes a work item; the GitOps steward diagnoses it; the
platform owner decides; a sync, if decided, is one governed action; the steward verifies.

```
alert ──▶ diagnosis (agent) ──▶ review (platform owner) ──▶ apply (platform) ──▶ verify (agent) ──▶ done
                                       └─ reject ──▶ closed, no sync
```

- **Input**: the work item names the Application in its `application` field (a trigger, a chat message or a
  person opens it; the template needs the project's `internal` tracker).
- **Diagnosis**: `gitops-steward` reads only. A git change it proposes (an `ignoreDifferences` entry, a revert)
  is text in the report; nothing edits git or an Application's spec.
- **Review**: the `platform-owners` group reads the report, ticks `prune` only if it wants git-absent
  resources deleted, and writes a note. Rejecting closes the item without any sync.
- **Apply**: `sync_application`, a FULL sync (no resource list, so no stale selective operation is left
  behind), `prune` off unless ticked, idempotent by the platform's `action_key`. It waits for the owners'
  approval, re-authenticated within ten minutes.
- **Verify**: the steward refreshes and re-reads the Application and reports whether it is Synced and Healthy.

The organisation needs an `argocd` connector (the plugin's server) and a `platform-owners` group. See
`procedures/connector.mcp.argocd.md`.
