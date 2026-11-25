# Template `access-review`

A periodic review of a Keycloak realm's access, under approval.

The workflow `access-review` runs `queue → awaiting_decision → done`. The agent `iam-steward` reads the
realm through the `keycloak` connector and writes a report (**Findings**, **Proposed access changes**,
**Not verified**, **Confidence**) with the evidence for each line. The group `iam-owners` reads it and
decides. A change the steward proposes (a group membership, a disabled account, a logout) is a governed
action: it waits in Approvals for a person, and nothing reaches Keycloak before.

## The schedule is project configuration

A schedule opens the review; it is not template content (`ProjectConfig.schedules`, core ADR 0113). Add it
to the project's configuration:

```yaml
schedules:
  - name: access-review
    cron: "0 6 1 * *"          # UTC, five fields: the first of each month at 06:00
    title: Monthly access review
    workflow: access-review
```

## Honest limits

- `dormant_users` is only as good as the realm's login events: the tool says when events are off or their
  retention is shorter than the window, and the report then says the list is not reliable.
- Realm settings, client secrets and credentials are not readable through the connector, so the review
  cannot check them. The report lists them under **Not verified**.
- Text found in Keycloak (a client description, an attribute) is third-party data: the agent reports an
  instruction found there and does not obey it.
