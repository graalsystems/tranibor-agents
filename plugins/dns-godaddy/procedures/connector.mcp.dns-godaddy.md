---
id: connector.mcp.dns-godaddy
title: Connect GoDaddy DNS zones
applies_to: {connector: mcp/dns-godaddy}
audience: org_admin
version: 1
reviewed: 2026-11-25
vendor_docs:
  - {title: "GoDaddy developer portal: getting started", url: "https://developer.godaddy.com/getstarted"}
  - {title: "GoDaddy developer portal: access tokens", url: "https://developer.godaddy.com/keys"}
  - {title: "GoDaddy Domains API v3: DNS records", url: "https://developer.godaddy.com/doc/endpoint/domains"}
egress: ["api.godaddy.com", "api.ote-godaddy.com"]
fields:
  environment: "production for real domains; ote for GoDaddy's separate test environment"
  token: "the personal access token of step 1, typed once"
  zones: "the zones this connector may touch, e.g. example.com; any other zone is refused, even to read"
---

## Before you start

- A GoDaddy account that holds the domains, and access to the GoDaddy developer portal with it.
- The `dns` and `dns-godaddy` plugins installed in your organisation (Catalogue, **Plugins**). Its server runs
  in the platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- The list of zones the connector may touch. Start with one test zone.
- This server uses the **Domains API v3** with a personal access token (PAT), the scheme GoDaddy's documentation
  now describes. The older v1 `sso-key` scheme is documented as deprecated for Domains and does not work with v3.
- Written from GoDaddy's public documentation (fetched 2026-11-25), **not run against a real account**: see
  "What is not confirmed".

### Account restrictions on API access

What the documentation says, and no more:

- Domain and DNS management through the API needs an account that holds at least one domain, or a plan that
  grants management access. An account that does not qualify is refused with a 403 that carries its own error
  code, distinct from a missing scope.
- A token carries scopes (`domains.domain:read`, `domains.dns:update`) but is **not** restricted to chosen
  domains: it reaches every domain of the account. The `zones` list of this connector is therefore the
  restriction, and the only one. Keep it short.
- A rate limit applies per credential: the v3 overview states 60 requests per minute, the rate-limits page does
  not fix a number. The server waits for `Retry-After` and retries twice.
- Not in the documentation, so not stated here as fact: the "minimum number of domains" folklore of the older
  v1 API. If your account is refused with a 403 that is not a scope problem, ask GoDaddy support whether the
  account qualifies for API access.

## At GoDaddy

1. **Create a personal access token** in the developer portal (`https://developer.godaddy.com/keys`). Name it
   for what it is, for example `tranibor-dns`. Tick only the scopes `domains.domain:read` and
   `domains.dns:update`. The token is **shown once**: copy it then.
2. **Choose its expiry** (7, 30, 60, 90 days or one year) and write the date down: an expired token is refused
   with a 401. Renew before.
3. For a first test, use GoDaddy's OTE (test) environment with its own token and set `environment` to `ote`.

### Revoking and rotating

- To stop the connector at once: remove it in Tranibor, then revoke the token in the developer portal.
- To rotate: create a new token, put it in `token`, then revoke the old one.

## Permissions

- Reads: zones, a zone's records, one record, a zone-file rendering built from the records, a **plan** (a diff
  with no write).
- Writes, each a governed action: create, update (a full replacement of the record) and delete a record.
  **Delete** always needs a person's approval. GoDaddy applies a change at once; `publish` is a no-op that says so.
- Never: SOA and NS records (GoDaddy refuses them with 409 `dns_record_not_mutable`), SRV writes, or any zone
  outside `zones`.

## In Tranibor

| Field | Value |
|:--|:--|
| `environment` | `production`, or `ote` for the test environment |
| `token` | the token of step 1 (sealed, never shown again) |
| `zones` | the zones the agent may manage, e.g. `example.com, example.net` |

## Test it

1. Ask the connector for `get_provider`, then `list_zones`: it lists your allowed zones that exist.
2. `list_records` on a name, then `plan_changes` with a record you do **not** want changed: nothing to do.
3. Ask the DNS steward to point `test.example.com` to `192.0.2.1`, approve the create, read it back with
   `get_record`, then delete it (a person approves).

The plugin's tests run the `dns/dns@1` conformance suite on the server's fake; `server/tests/live` runs it on a
throwaway zone (see its docstring). Step 3 is the first real write.

### What is not confirmed

- The OTE host `api.ote-godaddy.com`: derived from the OpenAPI document's server variable, not from a call.
- The error body and 403 code of an account that is not eligible.
- The answer of a POST (the server re-reads the zone to find the new record's id), and the id format.
- The default TTL (the server sends 3600 when none is given) and the field mapping of MX and CAA.
- SRV records: listed, never written (the service and protocol mapping is not done).
- The real rate limit.

## When it fails

| Symptom | Cause | What to do |
|:--|:--|:--|
| `GoDaddy answered 401` | the token is expired, revoked, or for the other environment | create a new token (step 1) |
| `GoDaddy answered 403` | a missing scope, or an account not eligible for API access | check the two scopes; then the account (section above) |
| `refused: zone ... is not in this server's allow-list` | the zone is not in `zones` | add it if it is meant |
| `GoDaddy answered 409 dns_record_not_mutable` | SOA or NS record | not changeable through the API |
| `GoDaddy answered 422` | TTL outside 600-86400, CNAME at the apex or beside another record | fix the record |
| `GoDaddy answered 429` | rate limit | retry later |
