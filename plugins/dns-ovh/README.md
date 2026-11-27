# dns-ovh plugin

The OVHcloud server of the [`dns`](../dns/README.md) profile: it serves `dns/dns@1` for zones hosted at
OVHcloud (API v1), under human approval. Install it with the `dns` plugin; the agent that uses it is
`dns-steward`, which names no provider.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `dns-ovh` 0.1.0, requires `dns` |
| Server | [`servers/dns-ovh.yaml`](servers/dns-ovh.yaml), [`server/`](server) | `implements: [dns/dns@1]`, over the OVHcloud API v1, and its `fake` |
| Procedure | [`procedures/connector.mcp.dns-ovh.md`](procedures/connector.mcp.dns-ovh.md) | application, restricted consumer key, revocation |

```sh
tranibor plugin validate plugins/dns-ovh
tranibor plugin pack plugins/dns-ovh -o dns-ovh-0.1.0.tar.gz   # same bytes every time
cd plugins/dns-ovh/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Connector

Regions `ovh-eu`, `ovh-ca`, `ovh-us`; requests are signed with an application key, application secret and
consumer key. `zones` is an allow-list: any other zone is refused, **even on read**, before any call to
OVHcloud. Egress is limited to the three API hosts. An instance names itself with `TRANIBOR_INSTANCE_NAME`
(default `dns-ovh`): that is what `get_provider` answers.

## Tools and governance

The profile's tools, with its governance floor kept:

| Tool | Access | Governance |
|:--|:--|:--|
| `get_provider`, `list_zones`, `get_zone`, `list_records`, `get_record`, `export_zone`, `plan_changes` | read | none; `plan_changes` computes a diff and writes nothing |
| `create_record` | write | idempotent through the platform's `action_key` and by content; undoing a create is a delete proposal |
| `update_record` | write | compensated: the undo restores `previous` |
| `delete_record` | write | human approval; compensated by a create from the returned record |
| `publish` | write | human approval: it is OVHcloud's zone refresh, which makes every staged change public |

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/dns/conformance`](../dns/conformance))
against this server over the fake. The suite is a path dev-dependency of `server/pyproject.toml`
(`tranibor-dns-conformance`, editable), installed by `uv sync --all-extras --dev`; the image installs no dev
group. `server/tests/live/` runs the same suite against a throwaway zone of a real account, skipped without
`TRANIBOR_LIVE_DNS_OVH_*`.

## The fake

`DNS_OVH_FAKE=1` serves a seeded account (example.com, example.net, and a zone outside the default
allow-list) through a transport that recomputes the request signature. The unit tests and the evals run on
it.

## Not confirmed

Not run against a real account. Points the public documentation did not settle (clock-skew error text,
consumer key validity options, `ttl: 0`, relative targets, TXT quoting, empty `subDomain` filter,
`isDeployed`, CNAME coexistence enforcement, the `api.eu.ovhcloud.com` host) are listed in the procedure.
