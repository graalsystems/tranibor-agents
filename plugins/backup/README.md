# backup plugin

Prove that backups exist and restore, whatever the backup system, with a Tranibor agent and a person's
approval for anything that writes data. This plugin holds what does not depend on the product: the **profile**
every backup server must serve, the agent, the skill, the template, the evals and the conformance suite. A
provider plugin brings the server: [`backup-velero`](../backup-velero/README.md).

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `backup` 0.1.0, `contents.profiles`; core `>=0.30.0,<1.0.0` is a placeholder (see below) |
| Profile | [`profiles/backup.yaml`](profiles/backup.yaml) | `backup/backup@1`: 12 tools, their schemas and the governance floor |
| Agent | [`agents/backup-verifier.yaml`](agents/backup-verifier.yaml) | names no product; checks every schedule, flags gaps and failing locations, proposes a restore test |
| Skill | [`skills/restore-testing`](skills/restore-testing/SKILL.md) | RPO and RTO, what a test restore proves and does not |
| Template | [`templates/backup-nightly`](templates/backup-nightly/README.md) | verify (agent), approve the restore (person), report |
| Evals | [`evals/backup-verifier.yaml`](evals/backup-verifier.yaml) | instruction checks, and live cases on a provider's fake |
| Conformance | [`conformance/`](conformance) | pytest suite a server runs against itself |

## The profile

ADR 0224: a profile is a contract of tools. A server that `implements: [backup/backup@1]` serves all of them,
with a schema compatible with the profile's, and governance at least as strict. The platform names an
organisation's instances `<instance>__<tool>`; the agent does not know which product is behind one.

| Tool | Access | Governance floor |
|:--|:--|:--|
| `get_provider` | read | none; names the instance and the rule a restore target must follow |
| `list_schedules`, `list_backups`, `get_backup`, `list_locations`, `list_restores`, `get_restore` | read | none |
| `backup_freshness` | read | none; per schedule: fresh, stale, never or paused |
| `storage_inventory` | read | none; says `configured: false` when the system has no way to list its storage |
| `trigger_backup` | write | idempotent through the platform's `action_key`; no approval floor |
| `run_restore_test` | write | `human_approval`; idempotent through `action_key`; only into a target the server's configuration allows; compensated by `cleanup_restore_test` |
| `cleanup_restore_test` | write | none; only a target the server created for a test |

Statuses are normalised: `succeeded`, `partial`, `failed`, `running`, `pending`; the product's own word stays
in `provider_status`. A partial backup is restorable but never counts as a fresh one. Sizes are `null` when
the system does not report them.

Left out on purpose: deleting or editing a backup, schedule or location, restoring in place, and reading
backup contents.

## Conformance

[`conformance/`](conformance) is a package (`tranibor-backup-conformance`). A server's tests subclass
`Conformance`, give it a `backup_target` fixture and inherit the checks: every tool served, schemas, newest
first and truncation, a partial backup never fresh, trigger idempotent for one `action_key`, the restore test
only inside the allowed target and idempotent, compensation rendered and run, an unrestorable backup refused,
targets and label escapes refused before any upstream call. Each provider plugin declares it as a path
dev-dependency (`[tool.uv.sources]`, editable) of its `server/pyproject.toml`; the image does not install it.
See [`conformance/README.md`](conformance/README.md).

## Template cron

The `backup-nightly` template runs on a project `schedules` entry (its README gives it); the template itself
holds no cron. The restore always waits for a person.

## Not proven yet

`validate` and `pack` need a core that knows `contents.profiles`, `implements: <profile>` and
`mcp_servers[].profile` (core stories S86-01/S86-02). The core floor in `plugin.yaml` is a placeholder until a
release carries them, and the evals' live cases have never run.
