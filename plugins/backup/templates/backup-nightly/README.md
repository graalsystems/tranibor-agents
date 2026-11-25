# backup-nightly - nightly restore verification of backups

One workflow, `backup-nightly`, for a platform team that wants proof every night that its backups, at any backup
system whose server plugin is installed, restore, and a person to read the result only when it is Red.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| queue | - | the project's `schedules` entry opens tonight's work item | - |
| verify | agent `backup-verifier` | checks freshness, errors and storage locations, proposes a restore of one source into an isolated target, compares, cleans it up; writes `backup_verdict` (Green or Red) and `backup_report_markdown` | back to the queue, retried twice, then a person |
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
      fields: { schedule: "", source: "" }
      workflow: backup-nightly
  ```

  Run it after the backups it checks, and when the system is quiet: a test restore uses capacity. The core
  keys the work item by its window, so two passes in the same window open one item.
- **The restore always waits for a person.** `run_restore_test` is `human_approval: true` in the profile, and
  a server may only raise that, never lower it: a restore writes data. An unapproved restore never ran, so its
  schedule is reported **Red - unproven**, never Green. The target is isolated by the server's own rule
  (`get_provider.restore_target_rule`), which the agent cannot change; `cleanup_restore_test` is automatic (it
  deletes only a target this server created inside that rule).
- **The template names no connector.** The backup system is whichever instance of `backup/backup@1` the
  organisation connected; the agent asks each one and refuses to guess when a named schedule is served by none
  or by several.
- **Green is bounded.** The report ends with *Not verified*: one source restored, data volumes not restored
  unless asked, an application not started. Read the skill `restore-testing` for what a restore test proves.
- **A Red is text.** The agent proposes a fix; it never changes a backup, a schedule or a source.
  The platform owner carries the fix out and records what they did.

Validated by `tranibor plugin validate` (manifest) and the core's workflow parser (strict); the template's
checks are in `tests/test_template.py`.
