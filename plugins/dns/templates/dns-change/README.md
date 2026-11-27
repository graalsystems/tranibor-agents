# dns-change — a DNS change planned by an agent, decided by the zone owner

One workflow, `dns-change`, for a team that wants the DNS steward to prepare each change to a zone, at any
provider whose server plugin is installed, and a person to decide what is written and what is published.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| request | — | a work item names the zone and says what to change ("point app.example.com to 192.0.2.50") | — |
| plan | agent `dns-steward` | asks every instance which serves the zone, reads it, calls `plan_changes`, writes `dns_steward_markdown`; the writes it calls wait in Approvals | back to the queue, untouched |
| review | people of `zone-owners` | read the plan; decide each proposed action in Approvals (create, update, delete, and publish when the provider stages changes); confirm | **reject** → back to the queue |
| done | — | — | — |

Things to know:

- **Nothing is written before a person decides.** `delete_record` and `publish` are `human_approval: true`
  in the profile; a provider's server may only raise that, never lower it.
- **The template names no connector.** The provider is whichever instance of `dns/dns@1` the organisation
  connected; the agent asks each one (`get_provider`) and refuses to guess when none or several serve the zone.
  For that reason the publish is the agent's last proposed action, not a platform step.
- **The workflow has no tracker**: create the work item by hand (or from your own trigger) with `zone`.
  It is checked against the core's schemas, not yet played on a deployment.
- The agent and the skill come with the plugin, not the template.
