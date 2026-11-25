# lan-incident — LAN and Wi-Fi incidents diagnosed under approval

One workflow, `lan-incident`, for a network team that wants an agent to read the console and write the
diagnosis of each complaint, and a network administrator to decide what is written to the network.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| request | — | a complaint becomes a work item (console, trigger, MCP client) | — |
| diagnosis | agent `unifi-netops` | reads the site, devices, uplinks, statistics, clients and firmware; writes `lan_diagnosis_markdown` and `action_kind`; any write it calls waits in Approvals | back to the request, untouched |
| routing | the platform | `restart_device`, `power_cycle_port`, or `none` | — |
| review | people of `network-admin` | read the diagnosis; for a restart or a power-cycle, settle the target in a form; for `none`, read it and its change plan | **reject** → left untouched |
| apply | the platform | one governed action, `restart_device` or `power_cycle_port`, that a second member of `network-admin` approves, re-authenticated | retried once, then a person |
| done | — | — | — |

Things to know:

- **Nothing is written before a person decides.** The agent's own calls are governed actions; the apply
  step writes only what the review form holds, and `restart_device` and `power_cycle_port` carry
  `human_approval` in the server's descriptor, a floor no policy lowers.
- **No SSID, VLAN, channel or firmware tool exists**: the official API serves none. A change of that kind is
  the **Change plan** section of the report, for an administrator to make on the console.
- **The tracker is the platform's own** (`tracker: internal`): work items are opened by a person, a trigger
  or an MCP client, so the template ships no start rule.
- **A restart is for a misbehaving device that is online.** An offline device has an upstream cause; the
  agent proposes a PoE power-cycle of its switch port only when it read that port with PoE.
- The group `network-admin` is yours to create in your identity provider.

Validated by `tranibor plugin validate` (manifest), by the core's strict workflow parser and conformance
checks (`tests/test_template.py`, which also renders both governed actions and their approval). Not run end
to end on a cluster against a real console.
