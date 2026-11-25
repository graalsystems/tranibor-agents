# alert-triage - firing alerts triaged for the person on call

One workflow, `alert-triage`, for a team that wants an agent to prepare the triage of what is firing, at any
provider whose server plugin is installed, and a person to decide what is done.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| alert | - | a work item names the alert (a provider's webhook can open it, or by hand; empty means everything firing) | - |
| triage | agent `alert-triage` | asks every instance which fires the alert, groups the alerts by cause, reads the rule, the metric, bounded logs, the deploy annotations and the silences; writes `alert_triage_markdown` and `decision`; a silence it proposes waits in Approvals | back to the start, twice, then a person |
| review | people of `on-call` | read the report, decide a proposed silence in Approvals, record the outcome (escalate, fix the rule, none) | **reject** -> back to the start |
| done | - | - | - |

Things to know:

- **Nothing is written to the provider before a person decides.** `create_silence` is `human_approval: true`
  in the profile (a silence hides alerts from people); a provider's server may only raise that, never lower it.
  Its compensation is `expire_silence`.
- **The template names no connector.** The provider is whichever instance of `observability/observability@1`
  the organisation connected; the agent asks each one (`get_provider`) and refuses to guess when several fire
  the same alert. For that reason the silence is the agent's governed proposal, not a platform step.
- **Escalation and rule fixes are text.** The plugin sends no page and edits no rule.
- **The work item is opened by an HTTP trigger** (or by hand), which a template cannot ship: the provider
  plugin's procedure gives the trigger and the webhook that calls it. The template requires the `internal` tracker.
- The agent and the skill come with the plugin, not the template. Checked against the core's schemas, not yet
  played on a deployment.
