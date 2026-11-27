# lan-incident — a LAN or Wi-Fi incident diagnosed by an agent, decided by a network administrator

One workflow, `lan-incident`, for a network team that wants the LAN steward to read a site and write the
diagnosis of each complaint, at any vendor whose server plugin is installed, and a person to decide what is
written to the network.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| request | — | a complaint becomes a work item (console, trigger, MCP client), optionally naming the site | — |
| diagnosis | agent `lan-steward` | asks every instance which serves the site, reads devices, uplinks, statistics, clients and firmware; writes `lan_diagnosis_markdown` and `action_kind`; any write it calls waits in Approvals | back to the queue, untouched |
| review | people of `network-admin` | read the diagnosis and its change plan; decide each proposed action in Approvals (restart, PoE power-cycle, block or unblock a client, firmware upgrade); confirm | **reject** → back to the queue |
| done | — | — | — |

Things to know:

- **Nothing is written before a person decides.** `restart_device`, `power_cycle_port`, `block_client` and
  `upgrade_firmware` are `human_approval: true` in the profile; a provider's server may only raise that,
  never lower it.
- **The template names no connector and no platform action.** The provider is whichever instance of
  `lan/lan@1` the organisation connected; the agent asks each one (`get_provider`) and refuses to guess when
  none or several serve the site. The writes are the agent's own governed calls, not a platform step.
- **A provider may lack a write.** `get_provider` lists `unsupported_tools`; the agent then says so and puts
  the change in its **Change plan** section for an administrator to make on the vendor's console.
- **No SSID, VLAN or channel tool exists**: such a change is the **Change plan** section of the report.
- **The workflow has no tracker rule**: create the work item by hand (or from your own trigger). It is checked
  against the core's schemas, not yet played on a deployment.
- The group `network-admin` is yours to create in your identity provider. The agent and the skill come with
  the plugin, not the template.
