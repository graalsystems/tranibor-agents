# certificates-ct plugin

The Certificate Transparency server of the [`certificates`](../certificates/README.md) profile: it serves
`certificates/certificates@1` by searching a public log's index (crt.sh) for the domains an administrator
lists. Install it with the `certificates` plugin; the agent that uses it is `cert-watch`, which names no
provider. **Read-only**: it asks a public service and writes nothing.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `certificates-ct` 0.1.0, requires `certificates` |
| Server | [`servers/certificates-ct.yaml`](servers/certificates-ct.yaml), [`server/`](server) | `implements: [certificates/certificates@1]`, over a CT search, and its `fake` |
| Procedure | [`procedures/connector.mcp.certificates-ct.md`](procedures/connector.mcp.certificates-ct.md) | which domains to list, what it reports, revoking |

```sh
tranibor plugin validate plugins/certificates-ct
tranibor plugin pack plugins/certificates-ct -o certificates-ct-0.1.0.tar.gz   # same bytes every time
cd plugins/certificates-ct/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Connector

`domains` (required): the registered domains to follow, subdomains included. It is an allow-list: any other
domain is refused before a request is made. `ct_url` defaults to `https://crt.sh`. There is no credential and
no secret. An instance names itself with `TRANIBOR_INSTANCE_NAME` (default `certificates-ct`).

## Reaching the system

- **The public service works now.** crt.sh is on the internet; the server asks it like any client would.
- **A private log or mirror does not work yet.** It needs the private-network decision of ADR 0224 section 6
  (private CIDRs an administrator declares, or an outpost inside the network). That is not available, and this
  plugin does not work around it: do not point `ct_url` at a private host.
- Egress is declared as `from_fields: [ct_url]`: the platform checks the typed service against the
  organisation's network bounds; a cluster NetworkPolicy must let the server reach it on 443.
- The domains are searched by name, not dialled: a domain never turns into a connection to that domain.

## Tools

All four are the profile's, `access: read`, no approval, no undo.

| Tool | What it answers here |
|:--|:--|
| `get_provider` | the instance, `provider: crt.sh`, `kind: log`, the domains in scope (`*.example.com`), and what a log cannot see |
| `list_certificates` | the certificates logged for the domains, soonest expiry first; a domain whose search failed is in `unavailable`, not dropped |
| `get_certificate` | one logged certificate, by its `ct-<number>` id; the note says when a later one replaced it |
| `list_findings` | `expired` (information only), `expires_soon`, `unreachable`, each with evidence |

`renewal` is always `managed: false, state: unknown`. Ids are `ct-` and crt.sh's entry number: unique within
the instance, and a search is needed to read one (crt.sh's JSON has no lookup by id), so `get_certificate`
searches the configured domains.

What the log is for: it lists what was issued, including by someone who is not you. The findings above are
the ones a single log can raise from its dates. The question "which names have a certificate nobody else
knows" is the agent's, by comparing this instance's names with those the probe and the certificate manager
report; this server does not try to answer it.

Because the log keeps every certificate ever issued, a renewed certificate leaves its predecessor in it. An
entry that another entry with a later expiry covers entirely (every name) raises no `expires_soon` and no
`expired` finding, so a renewal is not an alarm; `get_certificate` says what replaced it. An expired entry
nobody replaced is reported as `info`, never `critical`: the log cannot tell that anyone still uses it.

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/certificates/conformance`](../certificates/conformance))
against this server over the fake. The suite is a path dev-dependency of `server/pyproject.toml`
(`tranibor-certificates-conformance`, editable), installed by `uv sync --all-extras --dev`; the image installs no
dev group. `server/tests/live/` runs the same suite against the real service, skipped without
`TRANIBOR_LIVE_CERTIFICATES_CT_DOMAINS`.

## The fake

`CERTIFICATES_CT_FAKE=1` serves six invented log entries for example.com: healthy, 9 days left, 4 days left,
another authority's, expired, and one replaced by a later entry. Nothing leaves the process. The unit tests and
the evals run on it.

## Not confirmed

crt.sh publishes no API documentation: `?q=<identity>&output=json` is the de facto interface its own page
links to, and the fields read here (`id`, `issuer_name`, `common_name`, `name_value`, `not_before`, `not_after`,
`entry_timestamp`, `serial_number`) are what it returned when #57 was written, not what an official document
promises. It is rate limited and sometimes answers 5xx or HTML: those are reported as `unavailable`, never
retried in a loop. The live suite has never been run, and the plugin has never spoken to the real service. A
very large domain can exceed crt.sh's time limit or the 5000 entries read per search.
