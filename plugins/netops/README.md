# Network operations plugin

The `netops` agent of the core's built-in catalogue (ADR 0040, ADR 0052). It reads through the organisation's MCP connectors named `pfsense`, `cilium` and `nginx` (`requires.org_connectors`), and proposes every change as code; it applies none.

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`netops`](agents/netops.yaml) | 4 | Diagnoses network behaviour from flows, policies and firewall rules; proposes every change as code, never applies one. |

Version 1.2.3, core `>=0.25.1,<1.0.0`. The instructions are the core's, byte for byte: the plugin's files
do not hold the work-item context, which the core prepends when it installs an internal agent. See
[MIGRATION-FROM-CORE.md](../../MIGRATION-FROM-CORE.md).

## Connectors

The plugin brings no server. The agent calls three organisation connectors, by name
(`requires.org_connectors` in [`plugin.yaml`](plugin.yaml)), each of capability `mcp`; the tools it may
call on each are the ones its `mcp_servers` allow ([`agents/netops.yaml`](agents/netops.yaml)), and the
ones its instructions name:

| Connector name | Capability | Tools allowed (`mcp_servers`) | Tools the instructions call |
|:--|:--|:--|:--|
| `pfsense` | `mcp` | `list_*`, `gateway_status`, `create_firewall_rule`, `update_firewall_rule`, `delete_firewall_rule` | `list_firewall_rules`, `list_aliases`, `list_port_forwards`; to propose a change, `create_firewall_rule`, `update_firewall_rule`, `delete_firewall_rule` |
| `cilium` | `mcp` | `list_*`, `get_flows`, `propose_network_policy` | `get_flows`, `list_network_policies`, `list_clusterwide_policies`; to propose a change, `propose_network_policy` |
| `nginx` | `mcp` | `list_*`, `read_*`, `propose_nginx_config` | `list_nginx_files`, `read_nginx_file`; to propose a change, `propose_nginx_config` |

The agent sees each tool as `<connector>__<tool>` (`cilium__get_flows`). `validate_nginx_config`, which
the instructions call before proposing an nginx change, is a platform tool, not one of these connectors'.

The agent reaches each connector **by its name** (`pfsense`, `cilium`, `nginx`). The core's own
connector types expose exactly these operations: `firewall/pfsense` (`list_firewall_rules`,
`create_firewall_rule`, …), `network_policy/cilium` (`get_flows`, `propose_network_policy`, …) and
`web_server/nginx` (`read_nginx_file`, `propose_nginx_config`, …). Create each one under its name and
set it up with the core's procedure, shown in the console beside the connector's form:
`connector.firewall.pfsense`, `connector.network_policy.cilium` and `connector.web_server.nginx`. An MCP
connector of the same name whose server exposes the same tool names works too; the plugin ships no
server of its own.

**Tool scripts by default** (1.1.0, catalogue version 3). The agent's spec says `tools: {scripts: offer}`
(the core's ADR 0122 §9): a run may call its tools from a script it writes, through the runner's tool
bridge, under the same governance as the model's own calls. The core's tool-script matrix measured it on
`netops-ssh-exposure` and `netops-dropped-flows-top` (matrix `evals-live-tools-s57-11-20261107`, opencode,
`platform/claude-opus`, ten repetitions): cost per successful run −38.0 % [−49.3 %, −23.3 %] with scripts,
success rate not lower. It is a default, never a right: the deployment's brake, the organisation's switch
and ceiling, and the project's policy when it says something for the stage's role (`tools.scripts` or
`tools.per_role.<role>.scripts`) all decide before it. A core older than 0.24.1 does not know the field
and keeps 1.0.0, which is the same agent without the default.


## Declared components (1.2.1)

Every agent declares the five components of ADR 0127 (the core's S60-04), so that a version no longer
inherits any of them from its project. Each agent's catalogue version is one higher than in 1.1.0, and no
instruction changed. `tools.web` and `tools.extensions` are declared even when empty: a list that is present
closes its source. The project and the deployment still only tighten what is named here.

| Agent | Model | `catalogue` | `extensions` | `web` | `memories` | Why |
|:--|:--|:--|:--|:--|:--|:--|
| `netops` | `profile:by_size` | none | none | none | `project/shared` write | Reaches the network estate through its three connectors (`mcp_servers`, unchanged) and `validate_nginx_config`, a platform tool; `tools.scripts: offer` is kept (ADR 0122 §9). |

`profile:by_size` is what these agents ran with unless a workflow actor overrode it; it is an explicit choice
here, not a new one. The memory is what the legacy `memory: project` meant (`effective_bindings`): the run's
lessons go to the project's shared memory. A later version may narrow it.

```sh
tranibor plugin validate plugins/netops
tranibor plugin pack plugins/netops -o netops-1.2.3.tar.gz   # same bytes every time
```
