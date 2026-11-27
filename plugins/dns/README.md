# dns plugin

Review and change DNS zones at any provider with a Tranibor agent, under human approval. This plugin holds
what does not depend on the provider: the **profile** every DNS server must serve, the agent, the skill, the
template, the evals and the conformance suite. A provider plugin brings the server:
[`dns-ovh`](../dns-ovh/README.md), [`dns-godaddy`](../dns-godaddy/README.md).

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `dns` 0.1.0, `contents.profiles`; core `>=0.30.0,<1.0.0` is a placeholder (see below) |
| Profile | [`profiles/dns.yaml`](profiles/dns.yaml) | `dns/dns@1`: 11 tools, their schemas and the governance floor |
| Agent | [`agents/dns-steward.yaml`](agents/dns-steward.yaml) | names no provider; finds the instance that serves the zone, refuses ambiguity |
| Skill | [`skills/dns-change-hygiene`](skills/dns-change-hygiene/SKILL.md) | TTL before a change, CNAME rules, dangling records, what a publish does |
| Template | [`templates/dns-change`](templates/dns-change/README.md) | request, plan (agent), review (zone owner), done |
| Evals | [`evals/dns-steward.yaml`](evals/dns-steward.yaml) | instruction checks, and live cases on a provider's fake |
| Conformance | [`conformance/`](conformance) | pytest suite a server runs against itself |

## The profile

ADR 0224: a profile is a contract of tools. A server that `implements: [dns/dns@1]` serves all of them, with
a schema compatible with the profile's, and governance at least as strict. The platform names an organisation's
instances `<instance>__<tool>`; the agent does not know which provider is behind one.

| Tool | Access | Governance floor |
|:--|:--|:--|
| `get_provider`, `list_zones`, `get_zone`, `list_records`, `get_record`, `export_zone` | read | none |
| `plan_changes` | read | none; computes a diff, writes nothing, proposes the calls (ending with `publish` iff the provider stages changes) |
| `create_record` | write | idempotent through the platform's `action_key` |
| `update_record` | write | compensable: the result carries `previous` |
| `delete_record` | write | `human_approval`; compensable by a create from the returned record |
| `publish` | write | `human_approval`; a no-op that says so for a provider that applies each change at once |

Record model: `{id, zone, name, type, ttl, value}`. `id` is an opaque string, `name` is relative to the zone
(`""` is the apex), `ttl` 0 means the provider's default.

Left out on purpose: zone audit and history (provider-specific; the agent finds dangling records from the
zone's own records), DNSSEC, NS and SOA changes.

## Conformance

[`conformance/`](conformance) is a package (`tranibor-dns-conformance`). A server's tests subclass
`Conformance`, give it a `dns_target` fixture and inherit the checks: every tool served, schemas, create
idempotent for one `action_key`, compensation of update and delete, allow-list refusal before any upstream
call (and look-alike zones), plan writes nothing, publish semantics. Each provider plugin declares it as a
path dev-dependency (`[tool.uv.sources]`, editable) of its `server/pyproject.toml`; the image does not install
it. See [`conformance/README.md`](conformance/README.md).

## Not proven yet

`validate` and `pack` need a core that knows `contents.profiles`, `implements: <profile>` and
`mcp_servers[].profile` (core stories S86-01/S86-02). The core floor in `plugin.yaml` is a placeholder until a
release carries them, and the evals' live cases have never run.
