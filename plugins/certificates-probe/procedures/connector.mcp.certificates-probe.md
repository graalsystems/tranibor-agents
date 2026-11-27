---
id: connector.mcp.certificates-probe
title: Install the certificates-probe connector
applies_to:
  connector: mcp/certificates-probe
audience: org_admin
version: 1
reviewed: 2026-11-26
vendor_docs:
  - {title: "Python ssl: TLS support", url: "https://docs.python.org/3/library/ssl.html"}
egress:
  - the hosts listed in `hosts`, on their ports (TLS)
fields:
  hosts: The public hosts whose certificate matters, as host or host:port.
---

## Before you start

- The `certificates-probe` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in
  the platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- The server opens a TLS connection to each host you list, reads the certificate it serves and closes. It
  sends no application data, holds no credential and writes nothing. There is no secret to create.
- **A public endpoint works as soon as the connector is installed. A private endpoint** (an internal address,
  a name only your resolver knows) **is not reachable yet**: it needs the private-network decision of ADR 0224
  section 6 (private CIDRs, or an outpost), which is not available. Do not point the connector at a private
  host and do not open a tunnel around the platform.

## At the endpoints

Nothing to create. List the hosts: every public name whose certificate matters, with its port when it is not
443 (a mail server on 993, a gateway on 8443). One entry per service: a certificate is served per host and
port.

## Permissions

| What | Where | Why |
|:--|:--|:--|
| an outbound TLS connection to each listed host and port | the listed hosts only | read the certificate each serves |
| nothing else | | no credential, no write, no other host |

## In Tranibor

Open **Connectors**, add the plugin's `certificates-probe` connector and fill `hosts`. The server refuses to
probe any host that is not in that list, whatever an agent asks. Then install the `cert-review` template of
the `certificates` plugin in a project and add the schedule its README gives.

## Test it

1. Press **Test** on the connector, then ask the agent for `list_certificates`: one entry per host you
   listed, with its days remaining.
2. A host that cannot be reached is named under `unavailable` and raises an `unreachable` finding; it is not
   an error of the connector.
3. `get_certificate` on an id that is not in the list is refused, without a connection being opened.

For each host the server reports the leaf certificate, its names, issuer and validity dates, whether the
chain served is complete and whether the system roots trust it. It does not know what renews the certificate:
ask the connector of the system that manages it (a certificate manager) for that. Nothing was run against a
real endpoint by the author of this page.

## When it fails

| You see | What it means | What to do |
|:--|:--|:--|
| the host is under `unavailable` | the platform could not open a TLS connection | check the name, the port and that the endpoint is public |
| `is not one of the configured hosts` | the request names a host not in `hosts` | add it if you own it |
| the chain shows as incomplete for a host that works in a browser | the server sends only its leaf and a browser fetches the intermediate | serve the full chain: that is the finding |

To revoke, delete the connector. Nothing was created on your side.
