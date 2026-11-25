# cert-review - a periodic certificate review, read by a person

One workflow, `cert-review`, for a platform team that wants an agent to look at certificate hygiene every
week and a person to decide what to do about it.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| queue | a schedule | one work item per window is opened | - |
| review | agent `cert-watcher` | reads cert-manager, Certificate Transparency and live probes; writes `cert_review_markdown` | back to the queue, twice, then a person |
| read | people of `platform-team` | read the report, apply or decline each proposal by hand, record the outcome | **reject** sends it back to the queue |
| done | - | - | - |

Things to know:

- **Nothing is ever written.** The plugin's server declares no write operation and the agent has no write
  tool: every fix is text or YAML in the report, for a person to apply outside the platform.
- **The schedule is the project's, not the template's.** A template's `defaults` takes no cron. Add this to
  the project's configuration (it needs the internal tracker, which `requires.connectors.tracker` asks for;
  the cron is UTC, five fields; work items are keyed by window, so a retried firing opens one item):

  ```yaml
  schedules:
    - name: weekly-cert-review
      cron: "0 6 * * mon"
      title: Weekly certificate review
      workflow: cert-review
  ```

- **Configure the server first.** The review is only as good as what the server can read: the service
  account, the domains and the probe hosts are set when the plugin is installed (see the plugin README and
  `procedures/connector.mcp.cert-watch.md`).
