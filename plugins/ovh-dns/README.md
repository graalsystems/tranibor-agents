# OVH DNS plugin

Review and change DNS zones at OVHcloud with a Tranibor agent, under human approval.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `ovh-dns` 0.1.0, core `>=0.29.2,<1.0.0` (the core CI builds against; the floor was not bisected) |
| Agent | [`agents/dns-steward.yaml`](agents/dns-steward.yaml) | reads, plans, cites; every write waits for a person |
| Skill | [`skills/dns-change-hygiene`](skills/dns-change-hygiene/SKILL.md) | TTL before a change, CNAME rules, dangling records, what a refresh publishes |
| Template | [`templates/dns-change`](templates/dns-change/README.md) | request → plan → review (zone owner) → apply → refresh → done |
| Evals | [`evals/dns-steward.yaml`](evals/dns-steward.yaml) | instruction checks, and five live cases on the fake account |
| Procedure | [`procedures/connector.mcp.ovh-dns.md`](procedures/connector.mcp.ovh-dns.md) | application, restricted consumer key, revocation |
| Server | [`servers/ovh-dns.yaml`](servers/ovh-dns.yaml), [`server/`](server) | the MCP server over the OVHcloud API v1, and its `fake` |

```sh
tranibor plugin validate plugins/ovh-dns
tranibor plugin pack plugins/ovh-dns -o ovh-dns-0.1.0.tar.gz   # same bytes every time
cd plugins/ovh-dns/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches).

## Connector

Regions `ovh-eu`, `ovh-ca`, `ovh-us`; requests are signed with an application key, application secret and
consumer key. `zones` is an allow-list: any other zone is refused, **even on read**, before any call to
OVHcloud. Egress is limited to the three API hosts.

## Tools and governance

| Tool | Access | Governance |
|:--|:--|:--|
| `list_zones`, `get_zone`, `list_records`, `get_record`, `export_zone`, `list_zone_history`, `plan_changes`, `audit_zone` | read | none; `plan_changes` computes a diff and writes nothing |
| `create_record` | write | idempotent through the platform's `action_key`; undoing a create is a delete proposal, not automatic |
| `update_record` | write | compensated: the undo restores `previous` |
| `delete_record` | write | human approval; compensated by a create from the returned record |
| `refresh_zone` | write | human approval: it publishes every staged change of the zone |

OVHcloud stages record changes until a refresh, so the template applies, then asks for the refresh as a
separate approved step.

## The agent: `dns-steward`

It reads the zone before proposing, cites every record by id, and writes `{"dns_steward_markdown": "…"}`
with **Findings**, **Proposed change**, **TTL advice**, **Confidence**. Record text is data, never
instructions; an instruction found there is reported as a suspected prompt injection.

## The fake

`OVH_DNS_FAKE=1` serves a seeded account (example.com, example.net, and a zone outside the default
allow-list) through a transport that recomputes the request signature. The unit tests and the evals run on
it.

## Not confirmed

Not run against a real account: the live tests in `server/tests/live/` are skipped without
`TRANIBOR_LIVE_OVH_DNS_*` credentials. Points the public documentation did not settle (clock-skew error
text, consumer key validity options, `ttl: 0`, relative targets, TXT quoting, empty `subDomain` filter,
`isDeployed`, CNAME coexistence enforcement, the `api.eu.ovhcloud.com` host) are listed in the procedure.
