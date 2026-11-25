# Template `joiner-leaver`

```
received ─ agent: plan ─▶ planned ─ manager ─▶ manager_ok ─ IT ─▶ approved
approved ─ one child per write ─▶ create account → [agent: resolve id] → remove groups → add groups
         → invitation → disable → end sessions ─▶ applied
applied ─ agent: read-back ─▶ checked ─ IT closes the manual tasks ─▶ done
```

| Workflow | What it holds |
|:--|:--|
| `joiner-leaver` | the request (kind, name, username, date, role, team, manager, **approver**), the agent's plan, the two plan approvals, the fan-out, the read-back, the closing task |
| `jml-create-account`, `jml-group-add`, `jml-group-remove`, `jml-invite`, `jml-disable`, `jml-logout` | one governed Keycloak write each, approved by `jml-approvers-<suffix>`, never by the person, the requester or the manager, applied no earlier than the date |

An organisation edits its role catalogue (the skill `role-catalogue`) and, if it wants other required
actions in the invitation, publishes its own version of `jml-invite`. The groups `jml-managers`,
`it-desk` and `jml-approvers-*` (including `jml-approvers-it-identity` for the account's own lifecycle,
as the catalogue names it) must exist in the identity provider.
