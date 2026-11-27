# observability plugin

Triage firing alerts at any observability provider with a Tranibor agent, under human approval. This plugin
holds what does not depend on the provider: the **profile** every observability server must serve, the agent,
the skill, the template, the evals and the conformance suite. A provider plugin brings the server:
[`observability-grafana`](../observability-grafana/README.md).

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `observability` 0.1.0, `contents.profiles`; core `>=0.30.0,<1.0.0` is a placeholder (see below) |
| Profile | [`profiles/observability.yaml`](profiles/observability.yaml) | `observability/observability@1`: 13 tools, their schemas and the governance floor |
| Agent | [`agents/alert-triage.yaml`](agents/alert-triage.yaml) | names no provider; finds the instance that fires the alert, groups alerts by cause, finds the likely cause with queries as evidence, proposes a silence or an escalation |
| Skill | [`skills/alert-triage`](skills/alert-triage/SKILL.md) | grouping, USE and RED signals, flapping, silence hygiene |
| Template | [`templates/alert-triage`](templates/alert-triage/README.md) | alert, triage (agent), review (on call), done |
| Evals | [`evals/alert-triage.yaml`](evals/alert-triage.yaml) | instruction checks, and live cases on a provider's fake |
| Conformance | [`conformance/`](conformance) | pytest suite a server runs against itself |

Supersedes the Grafana-only `alert-triage` plugin of #61: its client, fake, agent, evals and procedure moved to
[`observability-grafana`](../observability-grafana/README.md) and to this plugin, renamed to the profile's
vocabulary.

## The profile

ADR 0224: a server that `implements: [observability/observability@1]` serves all of these tools, with a schema
compatible with the profile's and governance at least as strict. The platform names an organisation's instances
`<instance>__<tool>`; the agent does not know which provider is behind one.

| Tool | Access | Governance floor |
|:--|:--|:--|
| `get_provider` | read | none; says the instance, the provider and the sources it may query |
| `list_firing_alerts`, `get_alert_rule`, `list_silences` | read | none; results are marked untrusted |
| `list_sources`, `query_metrics`, `query_logs` | read | none; the server bounds every window, step and line count, and refuses a source outside its allow-list before any upstream call |
| `list_dashboards`, `get_dashboard`, `list_annotations` | read | none; bounded |
| `create_silence` | write | `human_approval` (a silence hides alerts from people); idempotent through the platform's `action_key`; **compensation: `expire_silence`** |
| `expire_silence` | write | only a silence the server created; a person's is refused |
| `add_annotation` | write | idempotent through the platform's `action_key` |

Vocabulary: an alert has `id`, `labels`, `annotations`, `state`, `starts_at`, `silenced_by`, `rule_id`, `url`;
a **source** is a metrics or logs backend with an opaque `id` and a `kind` (`metrics` or `logs`); an annotation
id is a string. Nothing in it is Grafana's: a Prometheus with Alertmanager or a Datadog server fits it.

Left out on purpose: creating or editing rules, contact points and dashboards, paging, traces.

## Conformance

[`conformance/`](conformance) is a package (`tranibor-observability-conformance`). A server's tests subclass
`Conformance`, give it an `observability_target` fixture and inherit the checks: every tool served, schemas,
governance floor, allow-list refusal before any upstream call, bounded windows, silence idempotency and the
rendered compensation, refusal to expire a silence the server did not create, annotation idempotency. Each
provider plugin declares it as a path dev-dependency of its `server/pyproject.toml`; the image does not install
it. See [`conformance/README.md`](conformance/README.md).

## Not proven yet

`validate` and `pack` need a core that knows `contents.profiles`, `implements: <profile>` and
`mcp_servers[].profile` (core stories S86-01/S86-02). The core floor in `plugin.yaml` is a placeholder until a
release carries them. Checked with core 0.29.2: it refuses `contents.profiles` and a plugin dependency written
as a name, nothing else. The evals' live cases and the template have never run on a deployment.
