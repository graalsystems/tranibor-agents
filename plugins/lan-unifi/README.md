# lan-unifi plugin

The UniFi server of the [`lan`](../lan/README.md) profile: it serves `lan/lan@1` for a UniFi Network
console (official Integration API, optional Site Manager API), under human approval. Install it with the
`lan` plugin; the agent that uses it is `lan-steward`, which names no vendor. It supersedes the wave-1
`unifi` plugin (PR #58), whose client, fake and tests it reuses.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `lan-unifi` 0.1.0, requires `lan` |
| Server | [`servers/lan-unifi.yaml`](servers/lan-unifi.yaml), [`server/`](server) | `implements: [lan/lan@1]`, over the official UniFi APIs, and its `fake` |
| Procedure | [`procedures/connector.mcp.lan-unifi.md`](procedures/connector.mcp.lan-unifi.md) | least-rights API key, pinned CA, what the official API cannot do |

```sh
choregos plugin validate plugins/lan-unifi
cd plugins/lan-unifi/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Reaching the system

A console on a **public** address works today: the server calls `base_url` (and `api.ui.com` with a Site
Manager key), and egress is limited to exactly those hosts. A console on a **private** network, the usual
case, needs ADR 0224 section 6 (private CIDRs, or an outpost), which is not available yet. This plugin does
not work around it: do not publish the console to the internet to get through.

## Connector

Fields `base_url`, `api_key` (secret), `ca_certificate` (a pinned PEM; TLS verification is never turned off),
and optionally `site_manager_url` with `site_manager_api_key` (secret) for the cloud firmware view. An
instance names itself with `TRANIBOR_INSTANCE_NAME` (default `lan-unifi`): that is what `get_provider` answers.

## Tools and governance

The profile's fifteen tools with the floor kept, plus eight extras no agent sees:

| Tool | Access | Governance |
|:--|:--|:--|
| the ten profile reads | read | none |
| `restart_device`, `power_cycle_port` | write | `human_approval` |
| `block_client`, `unblock_client`, `upgrade_firmware` | write | declared as in the profile (`human_approval`, block and unblock undo each other) but **not served**: the official API has no such call, so they answer `not_supported`, send nothing, and `get_provider.unsupported_tools` lists them |
| extras: `get_info`, `list_vouchers`, `cloud_firmware_status` | read | none |
| extras: `authorize_guest`, `unauthorize_guest`, `create_vouchers` | write | `human_approval`; undone by `revoke_guest_access` and `revoke_vouchers` |

No site allow-list: the console's API key, through the account's role, is the bound (the DNS pilot's zone
allow-list has no equivalent here).

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/lan/conformance`](../lan/conformance))
against this server over the fake (path dev-dependency, editable; the image installs no dev group). The
block, unblock and firmware-upgrade effects are therefore **not** proven on UniFi: only that they refuse
cleanly. `server/tests/live/` runs read-only checks against a real console, skipped without
`TRANIBOR_LIVE_UNIFI_BASE_URL` and `TRANIBOR_LIVE_UNIFI_API_KEY`; they have never run.

## The fake

`LAN_UNIFI_FAKE=1` serves a seeded office ("Example Office": gateway, switch, three access points, three
clients), on documentation addresses. The unit tests, the conformance suite and the evals run on it.

## Not confirmed

Not run against a real console. Points the vendor documents did not settle (guest access type value, restart
and power-cycle response bodies, voucher creation status, Site Manager `updateAvailable` type) are listed in
the header of [`server/src/tranibor_lan_unifi/tools.py`](server/src/tranibor_lan_unifi/tools.py).
