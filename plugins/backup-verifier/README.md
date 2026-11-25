# backup-verifier plugin

Prove that Kubernetes backups restore: a Tranibor agent checks every Velero schedule each night, restores one
namespace into a throw-away namespace, and reports Green or Red with the evidence and a proposed fix.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `backup-verifier` 0.1.0, core `>=0.25.1,<1.0.0` (the template ships no `start_rules`) |
| Agent | [`agents/backup-verifier.yaml`](agents/backup-verifier.yaml) | reads, restores into a prefixed namespace, compares, cleans up; a Red is explained, its fix is text |
| Skill | [`skills/restore-testing`](skills/restore-testing/SKILL.md) | RPO/RTO, what a restore test proves and does not, reading Velero's objects, cleanup |
| Template | [`templates/backup-nightly`](templates/backup-nightly/README.md) | queue, verify, done when Green, platform-owner review when Red |
| Evals | [`evals/backup-verifier.yaml`](evals/backup-verifier.yaml) | instruction checks, and three live cases on the fake cluster |
| Procedure | [`procedures/connector.mcp.velero.md`](procedures/connector.mcp.velero.md) | service account, RBAC, admission policy, optional read-only bucket key |
| Server | [`servers/velero.yaml`](servers/velero.yaml), [`server/`](server) | the MCP server over the Kubernetes API (`velero.io/v1`) and an optional S3 listing, with its `fake` |

```sh
tranibor plugin validate plugins/backup-verifier
tranibor plugin pack plugins/backup-verifier -o backup-verifier-0.1.0.tar.gz   # same bytes every time
```

`validate` reads the whole directory: run it on a clean tree (no `server/.venv` nor caches), as CI does.

## What the agent declares (ADR 0127)

| Agent | Model | `catalogue` | `extensions` | `web` | `memories` | Why |
|:--|:--|:--|:--|:--|:--|:--|
| `backup-verifier` | `profile:by_size` | none | none | none | `project/shared` write | Everything it reads and restores goes through its `velero` connector; it never browses, and names or messages from the cluster are data, never instructions. |

## The connector: `velero`

The server talks to the Kubernetes API with a service-account bearer token (Schedule, Backup, Restore and
BackupStorageLocation of `velero.io/v1`, paged with `limit` and `continue`), and optionally lists the backup
bucket with a Signature Version 4 `ListObjectsV2`. 30 minutes and 1.5 USD cap an agent run.

| Tool | Access | Approval | What it does |
|:--|:--|:--|:--|
| `list_schedules` | read | none | schedules, cron, paused, newest backup |
| `list_backups` | read | none | backups, optionally of one schedule |
| `get_backup` | read | none | phase, errors, warnings, failure reason, included namespaces |
| `storage_locations` | read | none | each location's phase and message |
| `list_restores` | read | none | restores and their phase |
| `get_restore` | read | none | phase, errors, items restored, comparison with the backup |
| `backup_freshness` | read | none | per schedule: fresh, stale, never or paused |
| `bucket_listing` | read | none | objects under the backup prefix (when a bucket is configured) |
| `create_test_restore` | write | the platform's default (a person), an admin may lower it | restores ONE namespace of a backup into `<prefix><name>-<date>` |
| `delete_test_restore_namespace` | write | automatic | deletes a namespace this server created, inside the prefix |

## Governance

| Question | Answer |
|:--|:--|
| What can it change? | create a Restore, create and delete namespaces named with the configured prefix; nothing else |
| Who enforces the prefix? | **The server.** Kubernetes RBAC cannot restrict namespace names by prefix, so the service account must hold `create`/`delete` on namespaces. The server refuses a target outside the prefix, a reserved namespace, and a delete of a namespace without the label `tranibor.io/test-restore=true`. The procedure adds an admission policy (ValidatingAdmissionPolicy and Kyverno examples) as a second guard in the cluster |
| Is a restore approved? | by default, yes: `create_test_restore` leaves `human_approval` unset (the platform's floor for a write), because a restore uses capacity. A restore that did not run makes its schedule Red, never Green |
| Is it idempotent? | yes: the Restore's name derives from the platform's `action_key`; a retry finds the first one |
| Can it be undone? | `create_test_restore` has a compensation: `delete_test_restore_namespace` on the namespace it returned |
| Does it touch a live namespace? | never: it restores into a new namespace, with `includeClusterResources: false` and a `namespaceMapping` |
| Who reads a Red? | the `platform-owner` group, in the template's review; the fix is text, carried out by a person |

## What is proven, and what is not

Proven on the fake cluster (`VELERO_FAKE=1`): the 10 tools, the namespace guard, freshness and cron logic,
idempotence, pagination, the HTTP server and its token, the descriptor against the server, and the SigV4
signature against the AWS test vector (respx for the Kubernetes and S3 requests).

Not proven: any live cluster or bucket. The live suite (`server/tests/live`) is skipped without
`TRANIBOR_LIVE_VELERO_*` variables. Not confirmed in the Velero documentation and read defensively: Restore
`status.progress`, BackupStorageLocation `status.*`, the `velero.io/schedule-name` and `velero.io/backup-name`
labels. The admission-policy examples were not run. The live eval cases were not run.
