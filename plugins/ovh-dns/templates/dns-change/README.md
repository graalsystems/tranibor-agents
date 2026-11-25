# dns-change — a DNS change planned by an agent, applied after review, published by a second approval

One workflow, `dns-change`, for a team that wants the DNS steward to prepare each change to a zone hosted
at OVHcloud and a person to decide what is written and what is published.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| request | — | a work item names the zone and says what to change ("point app.example.com to 192.0.2.50") | — |
| plan | agent `dns-steward` | reads the zone, calls `plan_changes`, writes `dns_steward_markdown` (findings, change, TTL advice); the writes it calls wait in Approvals | back to the queue, untouched |
| review | people of `zone-owners` | read the plan; decide each proposed action in Approvals (an approved one is written to the zone, unpublished); confirm the zone | **reject** → back to the queue, nothing published |
| apply | the zone owner | the approved actions: `create_record`, `update_record`, `delete_record` | each can be undone by its compensation |
| refresh | the platform | one governed action, `refresh_zone`, which always needs a person | retried, then a person |
| done | — | — | — |

Things to know:

- **Nothing is written before a person decides, and nothing is public before a second decision.** OVHcloud
  holds record changes in the zone until it is refreshed; `refresh_zone` is `human_approval: true`.
- **Only the zones of the connector's allow-list** can be named; any other zone is refused, even to read.
- **The workflow has no tracker**: create the work item by hand (or from your own trigger) with `zone`.
  It is a template of the plugin's agent and server, checked against the core's schemas, not yet played on a
  deployment.
- The agent and the skill come with the plugin, not the template.
