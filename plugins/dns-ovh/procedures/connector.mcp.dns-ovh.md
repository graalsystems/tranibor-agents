---
id: connector.mcp.dns-ovh
title: Connect OVHcloud DNS zones
applies_to: {connector: mcp/dns-ovh}
audience: org_admin
version: 1
reviewed: 2026-11-25
vendor_docs:
  - {title: "First steps with the OVHcloud API", url: "https://docs.ovhcloud.com/en/guides/manage-and-operate/api/first-steps"}
  - {title: "Managing OVHcloud API rights (right delegation)", url: "https://docs.ovhcloud.com/en/guides/manage-and-operate/api/api-right-delegation"}
  - {title: "OVHcloud API console", url: "https://eu.api.ovh.com/console"}
  - {title: "API schema: /domain/zone", url: "https://eu.api.ovh.com/1.0/domain.json"}
  - {title: "API schema: /auth", url: "https://eu.api.ovh.com/1.0/auth.json"}
  - {title: "API schema: /me/api/credential", url: "https://eu.api.ovh.com/1.0/me.json"}
egress: ["eu.api.ovh.com", "ca.api.ovh.com", "api.us.ovhcloud.com"]
fields:
  endpoint: "ovh-eu, ovh-ca or ovh-us: the API of the region of your OVHcloud account"
  application_key: "the AK of the API application created in step 1 (an identifier)"
  application_secret: "the AS of the same application, typed once"
  consumer_key: "the CK of step 2, limited to the zones below, typed once"
  zones: "the zones this connector may touch, e.g. example.com; any other zone is refused, even to read"
---

## Before you start

- An OVHcloud account that holds the zones, and the right to create an API application and to validate a
  consumer key for it (the account owner, or a user delegated that right).
- The `dns` and `dns-ovh` plugins installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- The list of zones the connector may touch. Start with one test zone.
- This procedure was written from OVHcloud's public documentation and API schemas
  (`vendor_docs` above). It has **not** been run against a real account: see "What is not confirmed".

## At OVHcloud

1. **An API application.** Create one on the page of your region (the "create an application" page linked from
   the first-steps guide, for example `https://eu.api.ovh.com/createApp/`). Name it for what it is, for
   example `tranibor-dns`. It gives an **application key (AK)** and an **application secret (AS)**.
2. **A consumer key restricted to your zones.** The consumer key is what grants rights, so give it only
   these: the four verbs on the listed zones' records and nothing else. Request it by `POST /auth/credential`
   (no authentication; the application key goes in the `X-Ovh-Application` header), here for the zones
   `example.com` and `example.net`:

   ```json
   {
     "accessRules": [
       {"method": "GET",    "path": "/domain/zone"},
       {"method": "GET",    "path": "/domain/zone/example.com"},
       {"method": "GET",    "path": "/domain/zone/example.com/*"},
       {"method": "POST",   "path": "/domain/zone/example.com/*"},
       {"method": "PUT",    "path": "/domain/zone/example.com/*"},
       {"method": "DELETE", "path": "/domain/zone/example.com/*"},
       {"method": "GET",    "path": "/domain/zone/example.net"},
       {"method": "GET",    "path": "/domain/zone/example.net/*"},
       {"method": "POST",   "path": "/domain/zone/example.net/*"},
       {"method": "PUT",    "path": "/domain/zone/example.net/*"},
       {"method": "DELETE", "path": "/domain/zone/example.net/*"}
     ],
     "redirection": "https://example.com/"
   }
   ```

   The answer holds `consumerKey` and a `validationUrl`. `GET /domain/zone` (the list of the account's
   zones) is the one rule that is not under a zone: the server uses it to tell which allowed zones exist.
   Alternatively, build the same rules on the OVHcloud "create token" page of your region (for example
   `https://api.eu.ovhcloud.com/createToken/`), which takes the same methods and paths as `GET`, `POST`,
   `PUT` and `DELETE` fields and gives the consumer key at the end.
3. **Validate the consumer key.** Open the `validationUrl` (or finish the create-token page), sign in with an
   account that owns the zones, choose the **validity** (an unlimited key is valid until revoked; prefer a
   fixed validity of a few months and renew it), and validate. Until it is validated, every call is refused.
4. **Check what it can do** with the OVHcloud API console: `GET /auth/currentCredential` shows the key's rules,
   its status and its expiry.

### Revoking and rotating

- To stop the connector at once: remove the plugin's connector in Tranibor, then revoke the consumer key at
  OVHcloud: `DELETE /me/api/credential/{credentialId}` (find the id with `GET /me/api/credential`; the entry
  shows the key's rules, last use, expiry and status), or from the account's API management page.
- To rotate: create and validate a new consumer key (steps 2 and 3), put it in `consumer_key`, then revoke the
  old one.
- To change the zones: create a new consumer key with the new rules and change `zones`; a consumer key's
  rules cannot be extended after it is validated.

## Permissions

- Reads: the zones, a zone's state, SOA and DNSSEC status, its records, one record, the zone as BIND text, its
  history, a **plan** (the diff between records you want and the live zone, with no write), and an audit.
- Writes, each a governed action: create, update and delete a record, and publish the zone. **Delete** and
  **refresh** always need a person's approval. OVHcloud holds record changes in the zone until it is
  refreshed, so nothing is public before `publish` is approved.
- Never: DNSSEC changes, zone restore from history, NS records of the apex, or any zone outside `zones`.

The consumer key's rules are the rights; the list above in step 2 is all it needs.

## In Tranibor

Install or configure the plugin and fill the connector:

| Field | Value |
|:--|:--|
| `endpoint` | `ovh-eu`, `ovh-ca` or `ovh-us`, as the account's region |
| `application_key` | the AK of step 1 |
| `application_secret` | the AS of step 1 (sealed, never shown again) |
| `consumer_key` | the CK of step 2 (sealed, never shown again) |
| `zones` | the same zones as the access rules, e.g. `example.com, example.net` |

The server holds two locks, and each is enough on its own to stop another zone: the consumer key's access
rules (OVHcloud refuses the call) and `zones` (the server refuses it before any call, on a read as on a
write). Keep both lists equal.

## Test it

The plugin's tests also run the `dns/dns@1` conformance suite against this server's fake; the same suite runs against a throwaway zone with `server/tests/live` (see its docstring).

1. In Tranibor, ask the connector for `get_provider` (it names the instance and its zones), then `list_zones`: it lists your allowed zones that exist in the account.
2. `get_zone` on one zone: name servers and whether changes are waiting to be published.
3. `list_records` on a name, then `plan_changes` with a record you do **not** want changed (no write): the plan says nothing to do.
4. Ask the DNS steward to point a test name (for example `test.example.com`) to `192.0.2.1` and approve the proposed create, then the publish; read the record back with `get_record`. Delete it afterwards (a person approves the delete and the publish).

Everything above is untested against a real account by the plugin's author: step 4 is the first real write.

### What is not confirmed

Not found in OVHcloud's documentation, so not confirmed; the server's code says the same where it applies:

- The exact error text OVHcloud returns for a clock that is too far off. The server reads the server's time
  from `/auth/time` and retries once when an error mentions the signature or the timestamp.
- The validity options of a consumer key: `POST /auth/credential` has no validity field in the schema, so the
  validity is chosen when the key is validated (or on the create-token page).
- Whether a `ttl` of 0 means the zone's default. The server never sends 0: it sends `null` on an update and
  leaves the TTL out on a create.
- Whether a host name written without a final dot is read as relative to the zone. The server warns about it;
  write absolute names (`lb.example.com.`).
- The quoting of TXT values in the API's answers, and how an empty `subDomain` filter is read (the server
  filters again itself).
- Whether an `isDeployed` of false means unpublished changes or a deployment in flight.
- Whether OVHcloud itself refuses a CNAME beside other records: the server checks it before writing.
- Whether `api.eu.ovhcloud.com` also serves the API for `ovh-eu`: the server only uses the three hosts of the
  egress list above.

## When it fails

| Symptom | Cause | What to do |
|:--|:--|:--|
| `OVHcloud answered 401: the credentials were refused` | the key is not validated, expired, revoked, or the AK/AS/CK do not belong together, or the endpoint is the wrong region | check `GET /auth/currentCredential`; re-validate or recreate the consumer key |
| `OVHcloud answered 403: the consumer key's access rules do not allow this call` | the key's rules do not cover this zone or verb | create a key with the zone's paths (step 2) |
| `refused: zone ... is not in this server's allow-list` | the zone is not in `zones` | add it to `zones` (and to a new consumer key) if it is meant |
| a listed zone is missing from `list_zones` | it is not in this OVHcloud account, or the key cannot list it | check the zone's name and the account |
| `OVHcloud answered 404` on a record | the record id belongs to another zone, or was deleted | list the zone's records again |
| a change is not visible | it is held until `publish` is approved, and resolvers keep the old TTL | approve the publish; the plan states the old TTL |
