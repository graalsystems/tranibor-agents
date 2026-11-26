---
id: connector.mcp.lan-unifi
title: Connect a UniFi console to the lan profile
applies_to: {connector: mcp/lan-unifi}
audience: org_admin
version: 1
reviewed: 2026-11-25
vendor_docs:
  - {title: "UniFi Network API (Integration API)", url: "https://developer.ui.com/network/"}
  - {title: "UniFi Site Manager API", url: "https://developer.ui.com/site-manager/"}
egress: ["<console host>:443 (local connection)", "api.ui.com:443 (cloud connection, or a local one with the Site Manager key)"]
fields:
  connection: "local (the console's own address, the default) or cloud (Ubiquiti's connector through api.ui.com)"
  console_id: "cloud only: the console's id in the Site Manager (GET https://api.ui.com/v1/hosts)"
  base_url: "https://<console host>, the UniFi OS console, without a path (for example https://unifi.example.com)"
  api_key: "the Network Integration API key, typed once"
  ca_certificate: "PEM of the console's certificate, or of its CA, when it is not publicly trusted (optional)"
  site_manager_url: "https://api.ui.com, only to enable the cloud firmware tool (optional)"
  site_manager_api_key: "a Site Manager API key, typed once (optional, with site_manager_url)"
---

## Before you start

- A UniFi console (UniFi OS) with the Network application, which you administer. The plugin's server speaks
  the **Network Integration API** of that console, whose version this plugin was written against is 10.1.x
  (`GET /proxy/network/integration/v1/info` shows yours). It serves no alarm and no event, no per-client
  signal or SSID, and it edits no SSID, VLAN or channel: the agent writes those as a change plan for a
  person to apply on the console.
- The `lan` and `lan-unifi` plugins installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- **Network reachability**, three cases. (1) A console on a **public** address: `connection: local`, the
  platform reaches `base_url` over HTTPS. (2) A console with **no public address and no private route**
  (the usual case): `connection: cloud`, Ubiquiti's official cloud connector, which relays every call
  through `api.ui.com:443`; the console needs firmware 5.0.3 or later and to be adopted at unifi.ui.com.
  (3) A console on a **private** network the platform is given a route to: ADR 0224 section 6a (now
  accepted, being built as S86-06), not available yet. Never publish the console to the internet to get
  around it. The server reaches only the host in `base_url` (local) and `api.ui.com` (cloud, or a local
  connection with a Site Manager key).
- **Cloud limits** (the connector's, Site Manager API v1.0.0): 100 requests per minute per console, 25 s a
  request, 10 MB an answer. The server keeps to 80 a minute and says clearly when a limit is hit.
- **What the official API cannot do.** Blocking a client, unblocking one and upgrading a firmware are in
  the `lan/lan@1` profile but not in this API: the server answers `not_supported` (nothing is sent) and
  `get_provider` lists them in `unsupported_tools`. The agent then writes them as a change plan.
- This plugin was tested on its fake console and with mocked HTTP only. It was **not** run against a real
  console: the first run is yours, with the read-only key below, before any write is approved.

## At UniFi

1. **A dedicated account.** Create an account for the integration (Admins and Users) rather than using a
   personal one, so that the key can be revoked without breaking anyone else.
2. **The API key.** In UniFi Network, **Settings, Control Plane, Integrations**, create an API key named
   `tranibor`, and copy it: it is shown once. The Integration API acts with the rights of the account that
   created it, and the key has no scope of its own that the documentation states: **the account's role is the
   least-rights control**. (The documentation does not say which roles an Integration API call needs: the
   split below is the expected one, to confirm with the live suite.)
   - For diagnosis only (the first weeks), give the account the **read-only** role on the site: every read
     tool works, and every write tool fails at the console with `403`, which the server reports as
     `forbidden`.
   - To let administrators approve a restart, a PoE power-cycle or a guest authorisation, the account needs
     the right to manage devices and guests on that site, and nothing more (not Super Admin, not Control
     Plane administration). Prefer to keep read-only until a person has seen the proposals the agent makes.
3. **Cloud connection: the Site Manager key and the console id.** On unifi.ui.com, **API**, create a Site
   Manager API key and copy it (shown once). An **organization** key reaches every console of the
   organization; a **personal** key only the consoles its owner administers: prefer the narrowest that
   covers this console. Then read the console's id: `GET https://api.ui.com/v1/hosts` with the header
   `X-API-Key`, the `id` of the entry for your console. On a cloud connection this key is the only
   credential: **no Integration API key is typed**, and the rights are the key owner's role on the console.
   On a local connection the key is optional and only enables the cloud firmware tool.
4. **TLS.** A console presents a certificate its own CA signed, which no public store trusts. **Do not turn
   verification off** (the server has no such option). Export the certificate the console presents (or its
   CA's) as PEM and paste it in `ca_certificate`; the server then trusts that certificate and nothing else
   for this console. Better, install a certificate from a CA you already trust on the console, and leave
   the field empty.

## Permissions

| Tool | Reads or writes | Needs on the console | Approval |
|:--|:--|:--|:--|
| `get_info`, `list_sites`, `list_devices`, `get_device`, `get_device_statistics`, `list_clients`, `get_client`, `list_networks`, `list_wlans`, `get_provider`, `firmware_status`, `list_vouchers` | reads | read-only on the site | none |
| `cloud_firmware_status` | reads (api.ui.com) | the Site Manager key | none |
| `restart_device`, `power_cycle_port` | writes | manage devices | `human_approval` |
| `block_client`, `unblock_client`, `upgrade_firmware` | writes | not served by the official API | `human_approval` (unblock: none); answer `not_supported` |
| `authorize_guest`, `unauthorize_guest`, `create_vouchers` | writes | manage guests and hotspot | `human_approval` |

Vouchers are created under a name that carries the action's key, so that a retried action never creates
them twice. Voucher codes and Wi-Fi passphrases are never returned by a read tool.

## In Tranibor

1. Install the plugin, then open its connector `lan-unifi`, pick **How to reach the console** and fill in:
   - **local**: `base_url`, `api_key`, optionally `ca_certificate`, and optionally both `site_manager_url`
     (`https://api.ui.com`) and `site_manager_api_key` (the two go together or neither);
   - **cloud**: `console_id`, `site_manager_url` (`https://api.ui.com`, required: the platform opens the
     egress from it) and `site_manager_api_key`. Leave `base_url`, `api_key` and `ca_certificate` empty.
2. The platform checks `base_url` and `site_manager_url` against the organisation's network bounds: add
   the console's host (local) or `api.ui.com` (cloud) if it refuses.
3. Keep the policy of the writes as the plugin ships it. `restart_device`, `power_cycle_port`, `block_client`, `upgrade_firmware`,
   `authorize_guest`, `unauthorize_guest` and `create_vouchers` carry `human_approval`, a floor that no
   policy lowers: each is a governed action a person approves. `revoke_guest_access` and `revoke_vouchers`
   are the undo of the two that have one.
4. Make a project from the template `lan-incident` and create the group `network-admin` in your identity
   provider: its members review the agent's diagnosis and approve a restart or a power-cycle.

## Test it

1. Ask the agent: "List the devices of the site and say which are offline". It should name the console's
   devices, their state and firmware.
2. A `401` is the key; a `403` is the account's role; a TLS error is the certificate (`ca_certificate`);
   a timeout is the network path (egress, firewall) between the platform and the console. On the cloud
   connection a `404` is also a wrong `console_id`, a console that is offline or on firmware older than 5.0.3,
   or a key (personal) that is not its owner's; `timeout` is the connector's 25 s; `rate_limited` is the
   100 requests a minute per console (wait a minute); `response_too_large` is the 10 MB limit.
3. The first write is yours: approve one restart of an access point in a quiet hour, and watch it come back.

## When it fails

- `invalid_arguments`: an id that is not a UUID, a port without PoE, a limit out of range. Nothing was sent
  to the console.
- `not_configured`: the connector lacks `site_manager_*`, for `cloud_firmware_status`.
- `rate_limited`: the console (or the cloud connector, 100 requests a minute per console) throttles the key,
  or the server's own throttle would exceed it (nothing was sent); the agent reads again later.
- `timeout`: the console, or the connector's 25 s, did not answer in time. `response_too_large`: the
  connector's 10 MB limit; the agent asks for fewer items.
- Endpoints, field values and responses this plugin could not confirm in the vendor's documents are listed
  in the server's `tools.py` header; the live suite (`server/tests/live`) is how to confirm them on yours:

```sh
cd plugins/lan-unifi/server
TRANIBOR_LIVE_UNIFI_BASE_URL=https://unifi.example.com TRANIBOR_LIVE_UNIFI_API_KEY=... \
  uv run pytest tests/live -q
# through the cloud connector instead:
TRANIBOR_LIVE_UNIFI_CONSOLE_ID=... TRANIBOR_LIVE_UNIFI_SITE_MANAGER_API_KEY=... uv run pytest tests/live -q
```
