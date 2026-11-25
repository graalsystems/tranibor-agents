---
id: connector.mcp.certificates-ct
title: Install the certificates-ct connector
applies_to:
  connector: mcp/certificates-ct
audience: org_admin
version: 1
reviewed: 2026-11-26
vendor_docs:
  - {title: "Certificate Transparency (RFC 9162)", url: "https://www.rfc-editor.org/rfc/rfc9162"}
  - {title: "crt.sh", url: "https://crt.sh"}
egress:
  - the search service in `ct_url` (https://crt.sh by default), over HTTPS
fields:
  domains: The domains whose logged certificates to list (subdomains included).
  ct_url: The search service to ask; keep the default.
---

## Before you start

- The `certificates-ct` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- The server asks a public search service which certificates were logged for your domains. The log is public
  and answers anyone: it holds no credential, and there is no secret to create.
- **The public service works as soon as the connector is installed.** A private log, or a mirror that only your
  network reaches, **is not reachable yet**: it needs the private-network decision of ADR 0224 section 6, which
  is not available. Do not point `ct_url` at a private host and do not open a tunnel around the platform.

## At the source

Nothing to create. Decide which domains to follow: the registered domains your organisation owns, such as
`example.com`. Subdomains come with them. A certificate for a name nobody told you about is exactly what this
connector is for; do not limit the list to the domains you think are in use.

## Permissions

| What | Where | Why |
|:--|:--|:--|
| an outbound HTTPS request to the search service | `ct_url` only | search the log for each listed domain |
| nothing else | | no credential, no write, no other host |

## In Tranibor

Open **Connectors**, add the plugin's `certificates-ct` connector and fill `domains`. Leave `ct_url` as it is.
The server refuses to search any domain that is not under that list, whatever an agent asks. Then install the
`cert-review` template of the `certificates` plugin in a project and add the schedule its README gives.

## Test it

1. Press **Test** on the connector, then ask the agent for `list_certificates`: the certificates logged for
   your domains, soonest expiry first.
2. A search that fails (the service is busy or rate limited) is named under `unavailable` and raises an
   `unreachable` finding; it is not an error of the connector. Ask again later.
3. `get_certificate` on an id that is not in the list is refused.

The log shows what a CA issued, not what is served or what renews it. It also keeps every certificate ever
issued: a renewed certificate leaves its predecessor in the log, which the server does not report as expiring
when a later one covers the same names. Names with no certificate in any other connector are reported by the
agent, not by the server. Nothing was run against the real service by the author of this page.

## When it fails

| You see | What it means | What to do |
|:--|:--|:--|
| the domain is under `unavailable` | the search service did not answer, or answered something that is not JSON | try again later: it is rate limited and sometimes slow |
| `names no certificate logged` | the id is not in the log for your domains | list again; check the domain is in `domains` |
| `is not a certificate of the configured domains` | the id is not one of this connector's (`ct-` and digits) | use an id from `list_certificates` |
| a certificate you do not recognise | a CA issued it for a name under your domain | the agent reports it; check with the team that owns the name |

To revoke, delete the connector. Nothing was created on your side.
