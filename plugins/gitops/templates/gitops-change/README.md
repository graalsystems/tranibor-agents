# gitops-change: an application diagnosed by an agent, its fix decided by the platform owner

One workflow, `gitops-change`, for a team that wants the GitOps steward to diagnose an application that is
OutOfSync or Degraded, at any controller whose server plugin is installed, and a person to decide what is applied.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| alert | n/a | a work item names the application ("shop-api is OutOfSync") | n/a |
| diagnosis | agent `gitops-steward` | asks every instance which serves the application, reads it, writes `gitops_report_markdown`; the writes it calls wait in Approvals | back to the start, untouched |
| review | people of `platform-owners` | read the diagnosis; decide each proposed action in Approvals (sync, rollback, pause or resume auto-sync); submit | **reject** closes the item |
| verification | agent `gitops-steward` | refreshes and re-reads the application, says whether it is Synced and Healthy | back to the previous step |
| done | n/a | n/a | n/a |

Things to know:

- **Nothing is applied before a person decides.** `sync_application`, `rollback_application` and
  `set_auto_sync` are `human_approval: true` in the profile; a server may only raise that, never lower it.
- **The template names no connector.** The controller is whichever instance of `gitops/gitops@1` the
  organisation connected; the agent asks each one (`get_provider`) and refuses to guess when none or several
  serve the application. For that reason the sync is the agent's proposed action, not a platform step.
- **A git change is text.** No tool edits git or an application's definition.
- **The workflow has no tracker**: create the work item by hand (or from your own trigger) with `application`.
  It is checked against the core's schemas, not yet played on a deployment.
- The agent and the skill come with the plugin, not the template.
