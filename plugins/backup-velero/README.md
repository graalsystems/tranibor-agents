# backup-velero plugin

The Velero server of the [`backup`](../backup/README.md) profile: it serves `backup/backup@1` for a Kubernetes
cluster that runs Velero, read through the Kubernetes API. Install it with the `backup` plugin; the agent that
uses it is `backup-verifier`, which names no product.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `backup-velero` 0.1.0, requires `backup` |
| Server | [`servers/backup-velero.yaml`](servers/backup-velero.yaml), [`server/`](server) | `implements: [backup/backup@1]`, over the `velero.io/v1` resources, and its `fake` |
| Procedure | [`procedures/connector.mcp.backup-velero.md`](procedures/connector.mcp.backup-velero.md) | service account, RBAC, admission policy, optional read-only bucket key |

```sh
choregos plugin validate plugins/backup-velero
choregos plugin pack plugins/backup-velero -o backup-velero-0.1.0.tar.gz   # same bytes every time
cd plugins/backup-velero/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Reaching the system

- **A public endpoint works now.** An API server (and, optionally, an S3 endpoint) with a public address and a
  valid certificate is reached from the platform once its host is in the connector's egress.
- **A private endpoint does not.** A cluster API on a private address cannot be reached by a plugin server
  today (ADR 0096 section 8). It needs ADR 0224 section 6 (private CIDRs or an outpost), which is not
  available yet. This plugin does not work around it: do not expose an API server to the internet for it.

## Connector

`kube_api_url` and a service account `kube_token` (the procedure gives its RBAC), `velero_namespace`
(default `velero`), `restore_namespace_prefix` (required), and optionally a read-only S3 key for
`storage_inventory`. Egress is limited to the API server and the bucket. An instance names itself with
`TRANIBOR_INSTANCE_NAME` (default `backup-velero`): that is what `get_provider` answers.

Kubernetes RBAC cannot restrict a verb to namespaces by prefix, so **the server is the guard** for the restore
target (prefix, reserved namespaces, a label on what it created); the procedure adds an admission policy as a
second, in-cluster control.

## Tools and governance

The profile's tools, with its governance floor kept:

| Tool | Access | Governance |
|:--|:--|:--|
| `get_provider`, `list_schedules`, `list_backups`, `get_backup`, `list_locations`, `list_restores`, `get_restore`, `backup_freshness`, `storage_inventory` | read | none |
| `trigger_backup` | write | idempotent through `action_key` (the Backup is named from it); creates one Backup from the Schedule's template; not undone |
| `run_restore_test` | write | human approval; one namespace into `<prefix><name>-<yyyymmdd>`, no cluster-scoped resources, volumes only on request; compensated by `cleanup_restore_test` |
| `cleanup_restore_test` | write | only a namespace in the prefix that carries the label this server set |

Velero phases map to the profile's statuses: Completed is `succeeded`, PartiallyFailed `partial`, Failed and
FailedValidation `failed`, InProgress and Finalizing `running`, New or unknown `pending`. Velero does not report a
backup's size in its resources, so `size_bytes` is `null`; the bucket (`storage_inventory`) holds it.

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/backup/conformance`](../backup/conformance))
against this server over the fake. The suite is a path dev-dependency of `server/pyproject.toml`
(`tranibor-backup-conformance`, editable), installed by `uv sync --all-extras --dev`; the image installs no dev
group. `server/tests/live/` runs reads and one restore round trip against a real cluster, skipped without
`TRANIBOR_LIVE_VELERO_*`.

## The fake

`VELERO_FAKE=1` serves a seeded cluster (three schedules, a failed backup, an unavailable location, test
namespaces) and a bucket listing. The unit tests and the evals run on it.

## Not confirmed

Not run against a real cluster. The procedure lists what the public documentation did not settle:
`status.progress` fields, a storage location's `status.*`, the `velero.io/schedule-name` and
`velero.io/backup-name` labels, the phases `Finalizing` and `WaitingForPluginOperations`, and in particular
**`trigger_backup`**: it creates a Backup from a Schedule's `spec.template`, as `velero backup create
--from-schedule` does, but only the CLI's behaviour was relied on, not a documented API contract.
