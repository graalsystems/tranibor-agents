# certificates-probe plugin

The live-endpoint server of the [`certificates`](../certificates/README.md) profile: it serves
`certificates/certificates@1` by opening a TLS connection to the hosts an administrator lists and reading what a
client is served. Install it with the `certificates` plugin; the agent that uses it is `cert-watch`, which names
no provider. **Read-only**: it sends no application data and writes nothing.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `certificates-probe` 0.1.0, requires `certificates` |
| Server | [`servers/certificates-probe.yaml`](servers/certificates-probe.yaml), [`server/`](server) | `implements: [certificates/certificates@1]`, over a TLS handshake, and its `fake` |
| Procedure | [`procedures/connector.mcp.certificates-probe.md`](procedures/connector.mcp.certificates-probe.md) | what to list, what it reports, revoking |

```sh
tranibor plugin validate plugins/certificates-probe
tranibor plugin pack plugins/certificates-probe -o certificates-probe-0.1.0.tar.gz   # same bytes every time
cd plugins/certificates-probe/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Connector

One field, `hosts`: host or host:port (443 by default). `hosts` is an allow-list: any other host is refused
before a connection is made. There is no credential and no secret. An instance names itself with
`TRANIBOR_INSTANCE_NAME` (default `certificates-probe`): that is what `get_provider` answers.

## Reaching the system

- **A public endpoint works now.** Install the connector, list the host, done: the server connects from the
  platform's network like any client on the internet would.
- **A private endpoint does not work yet.** An internal address, or a name only your resolver answers, needs the
  private-network decision of ADR 0224 section 6 (private CIDRs an administrator declares, or an outpost inside
  the network). That is not available, and this plugin does not work around it: do not list a private host.
- Egress is declared as `from_fields: [hosts]`. The platform checks the typed hosts against the organisation's
  network bounds; a cluster NetworkPolicy must let the server reach their ports (443 is not the only one: mail
  is 993 or 465, an API gateway often 8443).

## Tools

All four are the profile's, `access: read`, no approval, no undo.

| Tool | What it answers here |
|:--|:--|
| `get_provider` | the instance, `provider: tls-probe`, `kind: endpoint`, the hosts in scope, and what a probe cannot see |
| `list_certificates` | the certificate each configured host serves, not-issued and soonest-expiring first; a host that did not answer is in `unavailable`, not dropped |
| `get_certificate` | one host's certificate with the TLS version, chain length and fingerprint |
| `list_findings` | `expired`, `expires_soon`, `not_trusted`, `chain_incomplete`, `hostname_mismatch`, `unreachable`, each with evidence |

`renewal` is always `managed: false, state: unknown`: a handshake cannot tell what renews a certificate. The
agent asks the instances that manage (`certificates-cert-manager`).

## How it reads a certificate

A first handshake with verification off (so an expired or mis-chained certificate can still be read), then a
second one that verifies against the system roots, which gives `trusted`. The served chain comes from a private
CPython 3.12 attribute (`_sslobj.get_unverified_chain`, public from 3.13) and falls back to the leaf alone, in
which case the chain is reported incomplete.

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/certificates/conformance`](../certificates/conformance))
against this server over the fake. The suite is a path dev-dependency of `server/pyproject.toml`
(`tranibor-certificates-conformance`, editable), installed by `uv sync --all-extras --dev`; the image installs no
dev group. `server/tests/live/` runs the same suite against real hosts, skipped without
`TRANIBOR_LIVE_CERTIFICATES_PROBE_HOSTS`.

## The fake

`CERTIFICATES_PROBE_FAKE=1` serves six invented endpoints (example.com): healthy, 9 days left, 4 days left with
an incomplete chain and no trust, expired, valid for another name, and one that does not answer. Nothing is
dialled. The unit tests and the evals run on it.

## Not confirmed

The handshake was exercised against a local TLS server (self-signed, 12 days left), not against a public host: the live suite has never been run. Not confirmed: the `_sslobj` chain access
on the Python the image ships, and that the platform reads a bare `host:port` in an egress `from_fields`.
