# cert-watch plugin

Review TLS certificate hygiene with a Tranibor agent: cert-manager state, Certificate Transparency, and what
the endpoints really serve. **Read-only: the server has no write tool at all.**

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `cert-watch` 0.1.0, core `>=0.29.2,<1.0.0` |
| Agent | [`agents/cert-watcher.yaml`](agents/cert-watcher.yaml) | reads, cites, diagnoses, proposes fixes as text for a person to apply |
| Skill | [`skills/cert-manager-troubleshooting`](skills/cert-manager-troubleshooting/SKILL.md) | the Certificate to Challenge chain, HTTP-01 / DNS-01 failures, rate limits |
| Template | [`templates/cert-review`](templates/cert-review/README.md) | queue, agent review, platform-team decision, done; weekly schedule in the project config |
| Evals | [`evals/cert-watcher.yaml`](evals/cert-watcher.yaml) | instruction checks, and four live cases on the fake |
| Procedure | [`procedures/connector.mcp.cert-watch.md`](procedures/connector.mcp.cert-watch.md) | read-only service account, minimal RBAC, token, CA |
| Server | [`servers/cert-watch.yaml`](servers/cert-watch.yaml), [`server/`](server) | the MCP server and its `fake` |

## Sources and tools

| Source | Used by | Notes |
|:--|:--|:--|
| Kubernetes API (`cert-manager.io/v1`, `acme.cert-manager.io/v1`), GET and LIST only | `list_certificates`, `get_certificate`, `list_failing_challenges`, `expiry_report` | bearer token, optional private CA, optional namespace allow-list |
| crt.sh (Certificate Transparency search) | `ct_search` | only the configured `domains`; the JSON output is de facto, not documented |
| Live TLS handshake | `probe_tls`, `expiry_report` | only the configured `probe_hosts` |

| Tool | Access | Approval |
|:--|:--|:--|
| `list_certificates`, `get_certificate`, `list_failing_challenges`, `ct_search`, `probe_tls`, `expiry_report` | read | none needed |

There is no write operation, no compensation and nothing to approve. The agent proposes a fix as text or YAML
for a person; the template's last step is a human decision.

## Network note

`egress` can name URL-valued fields only (`kube_api_url`, `ct_url`). The `probe_hosts` (`host:port`) cannot be
expressed there: the server refuses any host not in the list, but the platform's network policy does not open
those hosts by itself. An administrator must allow them.

## Validate

```sh
tranibor plugin validate plugins/cert-watch
tranibor plugin pack plugins/cert-watch -o cert-watch-0.1.0.tar.gz   # same bytes every time
cd plugins/cert-watch/server && uv run pytest -q                     # all on the fake and respx, no network
```

## Not verified

- Nothing was run against a real cluster, crt.sh or a real TLS host. `server/tests/live/` is skipped without
  `TRANIBOR_LIVE_CERT_WATCH_*` variables and has not been run.
- crt.sh's JSON fields are taken from common use, not from official documentation.
- On Python 3.12 the served chain is read through `ssl`'s private `_sslobj.get_unverified_chain()` (public from
  3.13), falling back to the leaf alone.
- Core Kubernetes Events are not read.
- The template cannot carry a cron: the schedule is a project `schedules:` block, shown in the template README.
