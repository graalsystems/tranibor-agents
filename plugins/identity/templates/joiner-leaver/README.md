# joiner-leaver — a person's access planned by an agent, decided by IT identity

One workflow, `joiner-leaver`, for a team that wants the access side of an arrival or a departure prepared by
an agent, at any provider whose identity server plugin is installed, and a person to decide each write.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| request | — | a work item names the directory, the person, the date, the role, the manager and the approver | — |
| plan | agent `joiner-leaver` | finds the instance, reads the directory and the role catalogue, calls `plan_joiner` / `plan_leaver`, writes `joiner_leaver_markdown`; the writes it calls wait in Approvals | back to the queue, untouched |
| review | people of `it-identity` | read the plan; decide each action in Approvals, on or after the date; do the manual tasks; say what was done | **reject** → back to the queue |
| done | — | — | — |

A joiner: create the account (no password), add the role's groups, send the setup mail. A leaver: disable,
end the sessions, remove the groups. Nothing is deleted.

Simplified from the wave-1 `joiner-leaver` plugin, which ran a child workflow per write, an agent phase to
resolve the account id and a read-back; the manager's separate approval is gone too. Here the agent proposes the
writes through the profile and the platform governs each one, as in `dns-change`. **Decision for the
maintainers**: if a separate manager approval or a read-back is wanted, they come back as workflow steps.

Things to know:

- **Nothing is written before a person decides.** Create, disable, enable, sign out and setup mail are
  `human_approval: true` in the profile; group changes follow the project's policy: set it to require approval
  for the role catalogue's sensitive groups.
- **A request without an approver is not planned**, nor one whose approver is the person.
- **The effective date is not enforced by the platform**: the reviewer approves on or after it.
- **The workflow has no tracker**: create the work item by hand with the fields above.
  It is checked against the core's schemas, not yet played on a deployment.
