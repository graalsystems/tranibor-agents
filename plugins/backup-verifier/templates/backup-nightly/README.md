# backup-nightly - nightly restore verification of Velero backups

One workflow, `backup-nightly`, for a platform team that wants proof every night that its Kubernetes backups
restore, and a person to read the result only when it is Red.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| queue | - | the project's `schedules` entry opens tonight's work item | - |
| verify | agent `backup-verifier` | checks freshness, errors and storage locations, restores one namespace into a prefixed throw-away namespace, compares, deletes it; writes `backup_verdict` (Green or Red) and `backup_report_markdown` | back to the queue, retried twice, then a person |
| done | - | **Green**: nothing to do | - |
| review | people of `platform-owner` | **Red**: read each Red's evidence and proposed fix, carry it out, settle the outcome in the form | - |
| done | - | - | - |

Things to know:

- **The cron is not in the template.** The core's template format cannot ship a time: `defaults.start_rules`
  matches a tracker item's labels, type and state, and has no cron. A nightly run is a **project schedule**
  (core docs, "Work items on a schedule"; `tracker: internal`), which the administrator adds to the project's
  configuration. Five fields, UTC:

  ```yaml
  schedules:
    - name: nightly
      cron: "30 3 * * *"
      title: "Backup verification {window}"
      fields: { velero_schedule: "", source_namespace: "" }
      workflow: backup-nightly
  ```

  Run it after the backups it checks, and when the cluster is quiet: a test restore uses capacity. The core
  keys the work item by its window, so two passes in the same window open one item.
- **The restore waits for approval by default.** `create_test_restore` is a write, and the descriptor leaves
  its `human_approval` unset: the platform governs it, and a person approves each run. An unapproved restore
  never ran, so its schedule is reported **Red - unproven**, never Green. An organisation that trusts the
  nightly run can lower the operation to automatic in its connector policy: the only namespaces it can touch
  are the ones inside the configured prefix, and the server refuses any other. `delete_test_restore_namespace`
  is already automatic (it deletes only a namespace this server created, inside the prefix).
- **Green is bounded.** The report ends with *Not verified*: one namespace restored, volumes not restored
  unless asked, an application not started. Read the skill `restore-testing` for what a restore test proves.
- **A Red is text.** The agent proposes a fix; it never changes a backup, a schedule or a source namespace.
  The platform owner carries the fix out and records what they did.

Validated by `tranibor plugin validate` (manifest) and the core's workflow parser (strict); the template's
checks are in `tests/test_template.py`.
