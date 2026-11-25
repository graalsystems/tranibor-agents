# alert-triage — Grafana alerts triaged for the person on call

One workflow, `alert-triage`, for a team that wants an agent to prepare the triage of each firing alert, and
a person to decide what is done.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| alert | — | Grafana's webhook contact point calls the project's HTTP trigger; one work item per alert group | — |
| triage | agent `on-call-triager` | reads the rule, the metric, bounded logs, the deploy annotations and the silences; writes `alert_triage_markdown` and `decision` | back to the start, twice, then a person |
| review | people of `on-call` | `decision: silence` → settles the matchers, minutes and reason; any other decision → records the outcome (escalate, fix the rule, none) | **reject** → back to the start, nothing written |
| apply | the platform | one governed action, `create_silence`, that a **second person** approves | declined: ends, nothing written; failed: a person reads the cause |
| done | — | — | — |

Things to know:

- **Nothing is written to Grafana before a person decides.** The triager's own calls are proposals; the
  silence is created from what the person on call settled, after another person approved it.
- **The route follows the agent's `decision`.** Only `silence` reaches the silence form; a missing or other
  answer takes the text branch (ADR 0106: an output the agent did not write never matches).
- **Escalation and rule fixes are text.** The plugin sends no page and edits no rule.
- **The work item is opened by an HTTP trigger**, which a template cannot ship: the procedure
  (`procedures/connector.mcp.grafana.md`, step *In Tranibor*, 3) gives its definition and the contact point
  that calls it. The template requires the `internal` tracker for that reason.
- **The silence's undo.** The action's effect declares `expire_silence` as its compensation; the server only
  expires silences it created.

Checked by `tranibor plugin validate` (manifest, workflow) and by `../../tests/` (the apply step renders
against the fields the work item holds). Not run end to end on a live Tranibor with a live Grafana.
