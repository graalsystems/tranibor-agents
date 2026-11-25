# identity-review — an access review written by an agent, decided by the directory owner

One workflow, `identity-review`, for a team that wants the identity steward to audit a directory at any
provider whose server plugin is installed, and a person to decide what is changed.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| request | — | a work item names the directory and, optionally, what to look at | — |
| report | agent `identity-steward` | asks every instance which serves the directory, reads it, writes `identity_steward_markdown`; the writes it calls wait in Approvals | back to the queue, untouched |
| decide | people of `directory-owners` | read the report; decide each proposed action in Approvals; confirm | **reject** → back to the queue |
| done | — | — | — |

Things to know:

- **Nothing is written before a person decides.** `add_user_to_group`, `disable_user`, `enable_user`, `logout_user`
  and `send_setup_email` are `human_approval: true` in the profile; a server may only raise that, never lower it.
- **Findings are candidates.** The agent says what the directory shows and what it cannot know (a service
  account, a second factor enforced elsewhere); the owner asks the person's manager.
- **The template names no connector.** The provider is whichever instance of `identity/identity@1` the
  organisation connected.
- **The workflow has no tracker**: create the work item by hand (or from your own trigger) with `directory`.
  It is checked against the core's schemas, not yet played on a deployment.
- The agent and the skill come with the plugin, not the template.
