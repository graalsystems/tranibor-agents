# Argo CD plugin

Supervise GitOps drift and sync through the Argo CD API, under human approval.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `argocd` 0.1.0, core `>=0.29.2,<1.0.0` |
| Agent | [`agents/gitops-steward.yaml`](agents/gitops-steward.yaml) | explains OutOfSync and Degraded, tells drift from a pending commit, proposes the fix; every write waits for a person |
| Skill | [`skills/argocd-troubleshooting`](skills/argocd-troubleshooting/SKILL.md) | drift vs pending commit, lingering selective sync, ignored differences, hooks, sync windows |
| Template | [`templates/gitops-drift`](templates/gitops-drift/README.md) | alert → diagnosis → review (platform owner) → apply → verify → done |
| Evals | [`evals/gitops-steward.yaml`](evals/gitops-steward.yaml) | instruction checks, and seven live cases on the fake instance (not run) |
| Procedure | [`procedures/connector.mcp.argocd.md`](procedures/connector.mcp.argocd.md) | local account, RBAC policy lines, token generation and rotation |
| Server | [`servers/argocd.yaml`](servers/argocd.yaml), [`server/`](server) | the MCP server over Argo CD's REST API, and its seeded `fake` |

## What the agent declares (ADR 0127)

| Agent | Model | `catalogue` | `extensions` | `web` | `memories` | Why |
|:--|:--|:--|:--|:--|:--|:--|
| `gitops-steward` | `profile:by_size` | none | none | none | `project/shared` write | Everything it reads and proposes goes through its `argocd` connector (`mcp_servers`); it never browses |

```sh
tranibor plugin validate plugins/argocd
tranibor plugin pack plugins/argocd -o argocd-0.1.0.tar.gz   # same bytes every time
```

`validate` reads the whole directory: run it on a clean tree (no `server/.venv` nor caches), as CI does.

## Governance

| Tool | Access | Approval | Undo |
|:--|:--|:--|:--|
| `list_applications`, `get_application`, `get_resource_tree`, `get_managed_resources`, `list_events`, `get_sync_windows`, `get_history`, `get_revision_metadata` | read | no | n/a |
| `sync_dry_run` | read | no | n/a |
| `refresh_application` | write | no | n/a (changes nothing in the cluster) |
| `sync_application` | write | yes, `action_key` for idempotency | none (see rollback) |
| `terminate_operation` | write | yes | none |
| `rollback_application` | write | yes | a rollback to the previous history id |

- **Never served**: deleting an Application, editing an Application's spec, changing a project or RBAC.
- **`projects` allow-list**: every tool checks the Application's project, and the list is filtered again.
- **`sync_dry_run` is a read, built from the diff.** Argo CD's own `dryRun` starts an operation that overwrites
  `status.operationState`, which would hide a lingering selective sync.
- **A sync is full by default**; a resource list needs a written justification of 20 characters or more, and
  `prune` is false unless asked. A running operation and a deny sync window are refused before the call.
- **`refresh_application` has no approval**, as a refresh changes nothing in the cluster. It is still a write
  and goes through the organisation's write policy.
- **Secret values and credential-looking fields never come out** of a diff; credentials in a repository URL
  are stripped; third-party text is truncated and treated as data.

## The agent: `gitops-steward`

It reads the Application, its managed resources' diff, its tree and events, the revision metadata and the
sync windows, and writes `{"gitops_report_markdown": "…"}` with four sections: **Findings**, **Diagnosis**,
**Proposed fix**, **Confidence**. A git change is text; a sync is a governed action. Text from Argo CD, git or
Kubernetes is data: an instruction in it is reported as a suspected prompt injection and not obeyed.
30 turns, 15 minutes, 1 USD.

## The server

Python 3.12, `mcp`, `httpx`, `pydantic`, `structlog`, `uvicorn`; package `tranibor_argocd`. Configuration:
`ARGOCD_BASE_URL`, `ARGOCD_TOKEN`, `ARGOCD_PROJECTS`, `ARGOCD_CA_CERT`; `ARGOCD_FAKE=1` runs it on a seeded
fake of the API (nine Applications, one per situation the agent must tell apart). The platform's token for the
server is `TRANIBOR_SERVER_TOKEN` (`CHOREGOS_SERVER_TOKEN` during the rename's window).

```sh
cd plugins/argocd/server
uv sync --all-extras --dev
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && uv run pytest -q
```

- **Proven on the fake**: every tool, the governance flags, the project allow-list, the HTTP guard, the
  descriptor against the server's tools, the template's path.
- **Not proven**: any call against a real Argo CD. `server/tests/live/` runs only with
  `TRANIBOR_LIVE_ARGOCD_URL`, `TRANIBOR_LIVE_ARGOCD_TOKEN` and `TRANIBOR_LIVE_ARGOCD_PROJECTS` set, and was
  not run. The points the documentation did not confirm are in the procedure and in `client.py`'s docstring.
