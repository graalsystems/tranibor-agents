# dns-godaddy plugin

The GoDaddy server of the [`dns`](../dns/README.md) profile: it serves `dns/dns@1` for zones hosted at GoDaddy
(Domains API v3, personal access token), under human approval. Install it with the `dns` plugin; the agent that
uses it is `dns-steward`, which names no provider.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `dns-godaddy` 0.1.0, requires `dns` |
| Server | [`servers/dns-godaddy.yaml`](servers/dns-godaddy.yaml), [`server/`](server) | `implements: [dns/dns@1]`, over the Domains v3 API, and its `fake` |
| Procedure | [`procedures/connector.mcp.dns-godaddy.md`](procedures/connector.mcp.dns-godaddy.md) | token, scopes, expiry, revocation, account restrictions |

```sh
tranibor plugin validate plugins/dns-godaddy
tranibor plugin pack plugins/dns-godaddy -o dns-godaddy-0.1.0.tar.gz   # same bytes every time
cd plugins/dns-godaddy/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Connector

`environment` is `production` or `ote` (GoDaddy's test environment); `token` is a personal access token with the
scopes `domains.domain:read` and `domains.dns:update`. `zones` is an allow-list: any other zone is refused,
**even on read**, before any call to GoDaddy. A GoDaddy token is not limited to chosen domains, so this list is
the restriction. Egress is limited to the two API hosts. An instance names itself with `TRANIBOR_INSTANCE_NAME`
(default `dns-godaddy`): that is what `get_provider` answers.

## Account restrictions on API access

Per GoDaddy's documentation: the account must hold at least one domain or a plan that grants management access,
otherwise a 403 with a distinct error code; tokens expire (7, 30, 60, 90 days or a year) and are shown once; a
rate limit applies per credential (60 requests a minute per the v3 overview). A "minimum number of domains"
rule known from the older v1 API is **not** in the documentation and is not asserted here. Details and sources
are in the procedure.

## Tools and governance

| Tool | Access | Governance |
|:--|:--|:--|
| `get_provider`, `list_zones`, `get_zone`, `list_records`, `get_record`, `export_zone`, `plan_changes` | read | none; `plan_changes` writes nothing; `export_zone` is built from the records (GoDaddy has no export) |
| `create_record` | write | idempotent through the platform's `action_key` and by content |
| `update_record` | write | compensated: the undo restores `previous`; a full-record replacement |
| `delete_record` | write | human approval; compensated by a create from the returned record |
| `publish` | write | human approval; a no-op, GoDaddy applies every change at once |

NS and SOA records are read-only; SRV is listed but not written.

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/dns/conformance`](../dns/conformance))
over the fake. The suite is a path dev-dependency of `server/pyproject.toml` (editable), installed by
`uv sync --all-extras --dev`; the image installs no dev group. `server/tests/live/` runs it on a throwaway zone,
skipped without `TRANIBOR_LIVE_DNS_GODADDY_*`.

## Not confirmed

Not run against a real account. The OTE host, the 403 body of an ineligible account, the POST answer and id
format, the default TTL, the MX/CAA mapping, SRV and the real rate limit are listed in the procedure.
