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

Three cases, one per way the platform can get to the console. The same fifteen profile tools and the same
extras run in each; only the base URL and the key differ (`connection`, below).

| The console is | Use | The platform reaches | Needs |
|:--|:--|:--|:--|
| on a **public** address | `connection: local` | `base_url` (and `api.ui.com` with a Site Manager key) | the console's Integration API key |
| **without a public address and without a private route** | `connection: cloud`, **available now** | `api.ui.com:443` only | the console id and a Site Manager API key; firmware 5.0.3 or later |
| on a **private** network the platform can be given a route to | `connection: local` over the private route | the private address | ADR 0224 section 6a (now accepted, being built as S86-06): private CIDRs or an outpost |

The **cloud connector** is Ubiquiti's official one (Site Manager API v1.0.0, `/v1/connector/consoles/{id}/*path`):
it relays the call to the console's `http://127.0.0.1/proxy/<path>`, so the server calls
`https://api.ui.com/v1/connector/consoles/<console id>/network/integration/v1/...` with `X-API-Key`. Nothing
else is opened and nothing is published to the internet. An **organization** key reaches every console of the
organization; a **personal** key only its owner's. Do not publish a console to the internet to get through.

The connector's limits are kept under on the client side (a call that breaks one fails with a typed error and
a clear reason): 100 requests per minute per console (the server keeps to 80 and waits for a free slot up to
20 s, else answers `rate_limited` with nothing sent; a 429 is `rate_limited` too), 25 s per request (the
server gives up at 20 s: `timeout`), 10 MB per response (`response_too_large`: ask for a smaller page).
The Site Manager's own listing (`cloud_firmware_status`) is a separate budget on the same key.

Not verified against a real console: the two official documents disagree on the path. The Site Manager API
(the connector) says the path after the console id is proxied to `/proxy/<path>` and gives
`.../consoles/{id}/network/integration/v1/sites` as its example, which is what is implemented; the Network API
11.0.97 document names its cloud server `.../consoles/{consoleId}/proxy/network/integration`. The prefix is the
one constant `Controller` builds in `client.py`; the live suite (`tests/live`, `TRANIBOR_LIVE_UNIFI_*`) settles it.

## Connector

Fields `connection` (`local`, default, or `cloud`; drawn as a choice), then: local, `base_url`, `api_key`
(secret), `ca_certificate` (a pinned PEM; TLS verification is never turned off); cloud, `console_id` (the id of
`GET https://api.ui.com/v1/hosts`), `site_manager_url` (`https://api.ui.com`: the platform opens the egress from
it) and `site_manager_api_key` (secret). On a local connection `site_manager_url` with `site_manager_api_key`
stay optional, for the cloud firmware view. An instance names itself with `TRANIBOR_INSTANCE_NAME` (default
`lan-unifi`): that is what `get_provider` answers.

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
