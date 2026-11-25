---
id: connector.mcp.unifi
title: Connect a UniFi console
applies_to: {connector: mcp/unifi}
audience: org_admin
version: 1
reviewed: 2026-11-25
vendor_docs:
  - {title: "UniFi Network API (Integration API)", url: "https://developer.ui.com/network/"}
  - {title: "UniFi Site Manager API", url: "https://developer.ui.com/site-manager/"}
egress: ["<console host>:443", "api.ui.com:443 (only with the Site Manager key)"]
fields:
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
- The `unifi` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- **Network reachability**: the platform must reach the console over HTTPS (443, or the port in `base_url`).
  A console on a private network is reached through your platform's network bounds (the egress the
  organisation allows) or an outpost; the server never reaches anything but the host in `base_url` and,
  when set, `api.ui.com`. Allow exactly those, from the platform only. Do not publish the console to the
  internet for this.
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
3. **Optional, the cloud firmware tool.** On unifi.ui.com, **API**, create a Site Manager API key (it lists
   hosts, sites and devices of your account, read only) and copy it. Leave it out to keep every call local.
4. **TLS.** A console presents a certificate its own CA signed, which no public store trusts. **Do not turn
   verification off** (the server has no such option). Export the certificate the console presents (or its
   CA's) as PEM and paste it in `ca_certificate`; the server then trusts that certificate and nothing else
   for this console. Better, install a certificate from a CA you already trust on the console, and leave
   the field empty.

## Permissions

| Tool | Reads or writes | Needs on the console | Approval |
|:--|:--|:--|:--|
| `get_info`, `list_sites`, `list_devices`, `get_device`, `get_device_statistics`, `list_clients`, `get_client`, `list_networks`, `firmware_drift`, `list_vouchers` | reads | read-only on the site | none |
| `cloud_firmware_status` | reads (api.ui.com) | the Site Manager key | none |
| `restart_device`, `power_cycle_port` | writes | manage devices | `human_approval` |
| `authorize_guest`, `unauthorize_guest`, `create_vouchers` | writes | manage guests and hotspot | `human_approval` |

Vouchers are created under a name that carries the action's key, so that a retried action never creates
them twice. Voucher codes and Wi-Fi passphrases are never returned by a read tool.

## In Tranibor

1. Install the plugin, then open its connector `unifi` and fill in the fields:
   `base_url`, `api_key`, optionally `ca_certificate`, and optionally both `site_manager_url`
   (`https://api.ui.com`) and `site_manager_api_key` (the two go together or neither).
2. The platform checks `base_url` and `site_manager_url` against the organisation's network bounds: add
   the console's host (and `api.ui.com` if used) if it refuses.
3. Keep the policy of the writes as the plugin ships it. `restart_device`, `power_cycle_port`,
   `authorize_guest`, `unauthorize_guest` and `create_vouchers` carry `human_approval`, a floor that no
   policy lowers: each is a governed action a person approves. `revoke_guest_access` and `revoke_vouchers`
   are the undo of the two that have one.
4. Make a project from the template `lan-incident` and create the group `network-admin` in your identity
   provider: its members review the agent's diagnosis and approve a restart or a power-cycle.

## Test it

1. Ask the agent: "List the devices of the site and say which are offline". It should name the console's
   devices, their state and firmware.
2. A `401` is the key; a `403` is the account's role; a TLS error is the certificate (`ca_certificate`);
   a timeout is the network path (egress, firewall) between the platform and the console.
3. The first write is yours: approve one restart of an access point in a quiet hour, and watch it come back.

## When it fails

- `invalid_arguments`: an id that is not a UUID, a port without PoE, a limit out of range. Nothing was sent
  to the console.
- `not_configured`: the connector lacks `site_manager_*`, for `cloud_firmware_status`.
- `rate_limited`: the console throttles the key; the agent reads again later.
- Endpoints, field values and responses this plugin could not confirm in the vendor's documents are listed
  in the server's `tools.py` header; the live suite (`server/tests/live`) is how to confirm them on yours:

```sh
cd plugins/unifi/server
TRANIBOR_LIVE_UNIFI_BASE_URL=https://unifi.example.com TRANIBOR_LIVE_UNIFI_API_KEY=... \
  uv run pytest tests/live -q
```
