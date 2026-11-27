# observability-grafana plugin

The Grafana server of the [`observability`](../observability/README.md) profile: it serves
`observability/observability@1` for a Grafana stack (Grafana-managed alerting, Prometheus-type and Loki
datasources through Grafana's proxy, dashboards, annotations), under human approval. Install it with the
`observability` plugin; the agent that uses it is `alert-triage`, which names no provider.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `observability-grafana` 0.1.0, requires `observability` |
| Server | [`servers/observability-grafana.yaml`](servers/observability-grafana.yaml), [`server/`](server) | `implements: [observability/observability@1]`, over Grafana's HTTP API, and its `fake` |
| Procedure | [`procedures/connector.mcp.observability-grafana.md`](procedures/connector.mcp.observability-grafana.md) | service account, token, webhook and trigger, what is not confirmed |

```sh
tranibor plugin validate plugins/observability-grafana
tranibor plugin pack plugins/observability-grafana -o observability-grafana-0.1.0.tar.gz   # same bytes every time
cd plugins/observability-grafana/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Reaching the system

- **A public Grafana works now.** `base_url` is the only host the server may reach (`egress.from_fields`).
- **A private Grafana does not work yet.** A plugin server cannot reach a private address (ADR 0096 section 8);
  that needs ADR 0224 section 6 (private CIDRs or an outpost), which is not available. This plugin does not
  work around it.

## Connector

One service-account token (Viewer reads everything; Editor, or a narrower custom role, only if silences and
annotations are wanted). Prometheus, Mimir and Loki are reached **through Grafana** (`/api/ds/query`): no second
credential, one egress host. `datasources` is an allow-list of datasource uids: any other source is refused
before any call to Grafana; empty means every Prometheus-type and Loki datasource the token sees. An instance
names itself with `TRANIBOR_INSTANCE_NAME` (default `observability-grafana`): that is what `get_provider` answers.

## Tools and governance

The profile's tools, with its governance floor kept:

| Tool | Access | Governance |
|:--|:--|:--|
| `get_provider`, `list_firing_alerts`, `get_alert_rule`, `list_silences`, `list_sources`, `list_dashboards`, `get_dashboard`, `list_annotations` | read | none; results bounded and marked `untrusted` |
| `query_metrics`, `query_logs` | read | window capped (default 6 h, at most 24 h), series, points and lines bounded (lines default 200, at most 1000) |
| `create_silence` | write | **`human_approval: true`**; exact matchers (no regex), including `alertname`, each matching a firing alert, at most 25 alerts; duration capped (default 120 min, at most 1440); idempotent through `action_key`; **compensation: `expire_silence`** |
| `expire_silence` | write | only a silence this plugin created (`createdBy` `tranibor-observability-grafana`) |
| `add_annotation` | write | always tagged `tranibor`; idempotent through `action_key` |

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/observability/conformance`](../observability/conformance))
against this server over the fake. `server/tests/live/` runs the same suite, and a few reads, against a test
stack, skipped without `TRANIBOR_LIVE_GRAFANA_*`.

## The fake

`OBSERVABILITY_GRAFANA_FAKE=1` serves a seeded stack: a real incident with a deploy marker (HighErrorRate on
checkout), a flapping alert, an alert already silenced by a person, a prompt injection in a log line, and a
datasource outside the test allow-list. Silences hide the alerts they match. The unit tests and the evals run on it.

## Not confirmed

Not run against a real Grafana. The `/api/ds/query` bodies and frames, the Alertmanager-compatible routes, which
of the three places carries an alert's rule id, and the narrower custom role are modelled on the documentation
and Grafana's source; the procedure lists them. The live suite has never been run.
