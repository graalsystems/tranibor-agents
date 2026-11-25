# gitops-argocd plugin

The Argo CD server of the [`gitops`](../gitops/README.md) profile: it serves `gitops/gitops@1` for the
Applications of the Argo CD projects it is allowed, under human approval. Install it with the `gitops` plugin;
the agent that uses it is `gitops-steward`, which names no controller. It supersedes the earlier `argocd`
plugin (PR #59), whose tools it renames to the profile's vocabulary.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `gitops-argocd` 0.1.0, requires `gitops` |
| Server | [`servers/gitops-argocd.yaml`](servers/gitops-argocd.yaml), [`server/`](server) | `implements: [gitops/gitops@1]`, over the Argo CD REST API, and its `fake` |
| Procedure | [`procedures/connector.mcp.gitops-argocd.md`](procedures/connector.mcp.gitops-argocd.md) | local account, API key, RBAC, rotation |

```sh
tranibor plugin validate plugins/gitops-argocd
tranibor plugin pack plugins/gitops-argocd -o gitops-argocd-0.1.0.tar.gz   # same bytes every time
cd plugins/gitops-argocd/server && uv sync --all-extras --dev && uv run pytest -q
```

Run `validate` on a clean tree (no `server/.venv`, no caches). `validate` needs a core that knows profiles
(core stories S86-01/S86-02).

## Connector

`base_url` (the API server, HTTPS), `token` (an API key of a local account, sealed), `projects` (the allow-list:
the profile's scopes) and, optionally, `ca_cert`. Any application of another project is refused by name, **even on
read**, and no write is sent for it. Egress is limited to the host of `base_url`. An instance names itself with
`TRANIBOR_INSTANCE_NAME` (default `gitops-argocd`): that is what `get_provider` answers.

## Reaching the system

Argo CD is often not on the internet. The server runs in the platform, so it must be able to open a TCP connection
to `base_url`.

| Where Argo CD is | Works now? | What it takes |
|:--|:--|:--|
| A public HTTPS endpoint (an ingress open to the internet, behind your own access control) | yes | set `base_url`; egress is limited to that host; keep the token's role minimal |
| A private address (a cluster IP, an internal load balancer, a VPN-only host) | **no, not yet** | ADR 0224 section 6: either the private CIDRs of the organisation are declared to the platform's egress, or an outpost (an agent inside your network that the server reaches through) is installed. Neither exists yet |

This plugin does not work around that: no tunnel, no relay, no widening of egress. Until section 6 is available,
a private Argo CD cannot be connected; the tools, the fake and the tests are unaffected.

## Tools and governance

The profile's tools, with its governance floor kept:

| Tool | Access | Governance |
|:--|:--|:--|
| `get_provider`, `list_applications`, `get_application`, `list_resources`, `get_diff`, `list_events`, `get_sync_windows`, `get_history`, `get_revision_metadata` | read | none; Secret values are never returned |
| `plan_sync` | read | none; compares live and desired, lists what a sync would change and what blocks it, starts no operation |
| `refresh_application` | write | none: re-reads git, changes nothing that runs |
| `sync_application` | write | human approval; idempotent through `action_key`; full by default, a partial sync needs a written justification |
| `rollback_application` | write | human approval; compensated by a sync back to the previous revision (`prune` as it was) |
| `set_auto_sync` | write | human approval; a pause keeps the removed settings in the annotation `tranibor.io/paused-automated`; compensated by `set_auto_sync` with `previous_enabled` |

Left out: stopping a running operation (`terminate_operation` of the earlier plugin): it is Argo CD specific, so it
is not in the profile, and a running operation is reported as a blocker for an administrator.

## Conformance

`server/tests/test_conformance.py` runs the profile's suite ([`plugins/gitops/conformance`](../gitops/conformance))
against this server over the fake. The suite is a path dev-dependency of `server/pyproject.toml`
(`tranibor-gitops-conformance`, editable), installed by `uv sync --all-extras --dev`; the image installs no dev
group. `server/tests/live/` checks a real instance, skipped without `TRANIBOR_LIVE_ARGOCD_*`.

## The fake

`GITOPS_ARGOCD_FAKE=1` serves a seeded Argo CD API (projects `shop`, `billing` and a `platform` one outside the
default allow-list): a drift, a pending commit, a lingering partial sync, a failed hook, controller-owned noise, a
deny window, a running operation, a Secret, a parent-managed Application. The unit tests and the evals run on it.

## Not confirmed

Not run against a real Argo CD. Points the documentation did not settle are listed at the end of the
[procedure](procedures/connector.mcp.gitops-argocd.md): the `update` right for the auto-sync patch, Argo CD's
refusal of a sync to a revision while auto-sync is on, `automated: {}` and `automated: null`, multi-source
Applications, and a parent Application reverting a pause. Argo CD's `dryRun` is not used (it overwrites the
operation state); `plan_sync` compares instead, so it cannot tell hooks or webhooks that a real sync would run.
