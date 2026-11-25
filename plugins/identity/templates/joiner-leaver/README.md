# joiner-leaver — a person's access planned by an agent, approved by the manager, decided by IT identity

One workflow, `joiner-leaver`, for a team that wants the access side of an arrival or a departure prepared by
an agent, at any provider whose identity server plugin is installed, approved by the person's manager, and each
write decided by a person.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| request | — | a work item names the directory, the person, the date, the role, the manager and the approver | — |
| plan | agent `joiner-leaver` (state `received`) | finds the instance, reads the directory and the role catalogue, calls `plan_joiner` / `plan_leaver`, writes `joiner_leaver_markdown`; the writes it calls wait in Approvals | back to the queue, untouched |
| manager | people of `jml-managers` | approve the request and plan: only the person's own manager | **reject** → declined |
| decide | people of `it-identity` | decide each action in Approvals, on or after the date, in the plan's order; confirm | **reject** → declined |
| read-back | agent (state `approved`) | resolves the account (the id the directory gives the username) and reads the directory back against the plan; a refused write shows as not done; writes `joiner_leaver_readback_markdown` | back to `approved` |
| close | people of `it-identity` | do the manual tasks; say what was done | **reject** → back to the read-back |
| done | — | — | — |

A joiner: create the account (no password), add the role's groups, send the setup mail. A leaver: disable,
end the sessions, remove the groups. Nothing is deleted.

Wave-1's `joiner-leaver` ran a child workflow per write, each calling a named connector. A template cannot name a
connector here (the provider is whichever instance the organisation connected), so the same phases (plan,
manager, IT, resolve, read-back, close) are kept in one workflow, and each write is the agent's governed
proposal, as in `dns-change`. The agent's two phases are told apart by the work item's state, as in wave-1.

Things to know:

- **Nothing is written before a person decides.** Create, add to a group, disable, enable, sign out and setup mail
  are `human_approval: true` in the profile; removing from a group follows the project's policy.
- **A request without an approver is not planned**, nor one whose approver is the person.
- **The manager's identity is not checked by the platform**: the task says to approve only as the person's
  manager, and the plan names the manager. A stricter check (approvers read from the work item, ADR 0196) is not
  wired in this workflow.
- **The effective date is not enforced by the platform**: IT approves on or after it.
- **The workflow has no tracker**: create the work item by hand with the fields above.
  It is checked against the core's schemas, not yet played on a deployment.
