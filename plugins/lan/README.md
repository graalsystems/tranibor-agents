# lan plugin

Diagnose a site's local network (devices, health, clients, ports, networks, Wi-Fi, firmware) with a Tranibor
agent, and propose governed changes, under human approval. This plugin holds what does not depend on the
vendor: the **profile** every LAN server must serve, the agent, the skill, the template, the evals and the
conformance suite. A provider plugin brings the server: [`lan-unifi`](../lan-unifi/README.md).

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `lan` 0.1.0, `contents.profiles`; core `>=0.30.0,<1.0.0` is a placeholder (see below) |
| Profile | [`profiles/lan.yaml`](profiles/lan.yaml) | `lan/lan@1`: 15 tools, their schemas and the governance floor |
| Agent | [`agents/lan-steward.yaml`](agents/lan-steward.yaml) | names no vendor; finds the instance that serves the site, refuses ambiguity |
| Skill | [`skills/lan-troubleshooting`](skills/lan-troubleshooting/SKILL.md) | Wi-Fi retries and channels, uplinks and PoE, a client that cannot connect, firmware hygiene |
| Template | [`templates/lan-incident`](templates/lan-incident/README.md) | request, diagnosis (agent), review (network administrator), done |
| Evals | [`evals/lan-steward.yaml`](evals/lan-steward.yaml) | instruction checks, and live cases on a provider's fake |
| Conformance | [`conformance/`](conformance) | pytest suite a server runs against itself |
| Tests | [`tests/`](tests) | the template, the agent and the profile agree with each other and with the core's schemas |

## The agent and the core's `netops`

`lan-steward` answers "what is wrong on site X": a slow Wi-Fi room, an offline device, firmware drift, a
client that cannot connect. It writes `{"lan_diagnosis_markdown", "action_kind"}` with five sections (Findings,
Likely cause, Proposed actions, Change plan, Confidence), cites the device and the field behind every claim,
treats text taken from the network as data, never as instructions, and never proposes a restart of several
devices at once.

The core catalogue's `netops` agent (not changed here) is a generic network operator. An organisation that
installs this plugin can let `netops` read its LAN too: in a new version of its `netops` agent, add the
profile to `mcp_servers` with the reads only, so that every write stays with `lan-steward`:

```yaml
mcp_servers:
  - profile: lan/lan@1
    tools: [get_provider, list_sites, list_devices, get_device, get_device_statistics, list_clients,
            get_client, list_networks, list_wlans, firmware_status]
```

A write added the same way is still a governed action: `human_approval` is a floor no agent version lowers.

## The profile

ADR 0224: a server that `implements: [lan/lan@1]` serves all of these tools, with schemas compatible with the
profile's, and governance at least as strict. The platform names an organisation's instances
`<instance>__<tool>`; the agent does not know which vendor is behind one.

| Tool | Access | Governance floor |
|:--|:--|:--|
| `get_provider`, `list_sites`, `list_devices`, `get_device`, `get_device_statistics`, `list_clients`, `get_client`, `list_networks`, `list_wlans`, `firmware_status` | read | none; `get_provider` also lists `unsupported_tools` |
| `restart_device` | write | `human_approval` |
| `power_cycle_port` | write | `human_approval`; PoE ports only |
| `block_client` | write | `human_approval`; compensated by `unblock_client` |
| `unblock_client` | write | compensated by `block_client` |
| `upgrade_firmware` | write | `human_approval`; no undo |

A provider whose API lacks a write still serves the tool: it answers `not_supported`, sends nothing, and says so
in `get_provider.unsupported_tools`. The agent then proposes nothing for it and writes a change plan.

Left out on purpose: SSID, VLAN, channel and firewall edits (a change plan for a person), alarms and events,
anything a single vendor offers (kept as extra tools of that vendor's plugin, which no agent sees).

## Conformance

[`conformance/`](conformance) is a package (`tranibor-lan-conformance`). A server's tests subclass
`Conformance`, give it a `lan_target` fixture and inherit the checks: every tool served, descriptor and
governance floor, compensations rendered as the platform renders them, reads and writes answering the
profile's schemas, `not_supported` with zero upstream calls. See [`conformance/README.md`](conformance/README.md).

## Not proven yet

`validate` and `pack` need a core that knows `contents.profiles`, `implements: <profile>` and
`mcp_servers[].profile` (core stories S86-01/S86-02). The core floor in `plugin.yaml` is a placeholder until a
release carries them, and the evals' live cases have never run.
