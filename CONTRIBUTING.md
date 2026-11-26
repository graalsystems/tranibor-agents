# Contributing

## Layout

```
plugins/<name>/
  plugin.yaml          # kind: Plugin — name, version, core range, contents
  agents/*.yaml
  skills/<skill>/SKILL.md
  templates/<template>/manifest.yaml
  evals/*.yaml
  evals/matrix/*.yaml    # optional: cases of the core's eval matrix (`contents.matrix_cases`)
  procedures/*.md
  servers/<server>.yaml  # optional: an MCP server image and the operations it exposes
  server/                # optional: the server's source, built and signed by CI
  README.md
```

## Rules

- **Everything in English**: code, docs, commit messages.
- **A published version is immutable.** Change something, bump `metadata.version`.
- **A server never asks for more than it needs.** Every tool it serves is declared in `operations`,
  with its access (`read` or `write`); a tool that is not declared is refused by the platform. A write
  a customer or the public can see carries `human_approval: true`.
- **Fakes are mandatory.** A server runs without the real system (a fake mode) so its tests and the
  platform's demo need no account; a server that a matrix case connects to declares that mode in its
  `fake` ([below](#matrix-cases-and-a-servers-fake)).
- **No secret in the repository.** Configuration reaches a server through the platform's sealed secrets.
- **Every commit carries `Signed-off-by`** (`git commit -s`): the [DCO](DCO).

## Checks

Every pull request runs `tranibor plugin validate` and a reproducible `pack` on each plugin, the tests
of each server, the ServiceNow plugin's matrix cases played as the core's scripted eval matrix plays them
and judged by its case judge, the repository's own tests (`tests/`), the publication script's tests and
an offline dry run of it, gitleaks on the pull request's own commits (main's whole history on a push)
and the DCO. Every job that needs the core runs it at one commit, `TRANIBOR_REF` in `ci.yml` (the same
in `publish.yml`): move both together, to a release tag's commit.

## The former name, `choregos`

The core's ADR 0226 retires the former product name from every layer: each name gets its `tranibor` twin,
the core reads both for a window of two minor versions (0.30.x and 0.31.x) and writes the new one, and the
former goes in 0.32.0. A plugin here keeps working on every core its `metadata.core` admits, so:

- **write the new name where every core the plugin supports reads it**, otherwise the former one (the
  manifests' `apiVersion: choregos/v1` and the media types stay, since a core before 0.30.0 reads nothing
  else; the template job steps run `choregos-quant`, the only command a job image before 0.30.0 has);
- **read both** wherever a server receives a name: a header (`X-Tranibor-*` first, then `X-Choregos-*`),
  a label or a marker the core hands, and what an earlier version wrote on a customer's system (a work
  note's marker, a correlation id, a mailbox category, an extended property) — a replayed action finds it;
- **never rename stored state in place**: what an administrator created from an earlier procedure (an
  integration user, a role, a system property, an application's name) keeps its name and keeps working;
  the procedure gives a new install the new names and says so;
- a server's package and command are `tranibor_*` and `tranibor-*`; the former import name and command stay
  as aliases until the plugin requires a core past the window.

`tests/test_former_name.py` holds it: the former name may appear only in the files and lines its allowlist
names, each with its reason (a window's twin, stored state, a name no core has renamed, history). A new
occurrence anywhere else fails the job; a twin whose window has closed is removed from the code and from the
list together.

## Documentation of a connector

Every server ships a procedure (ADR 0067 of the core), declared in the plugin's `contents.procedures`:

- its `applies_to` is `{connector: mcp/<plugin>}` (or `mcp/<server>` when the server has its own name);
- the keys of its front matter `fields` are fields of the server's `config_schema`, and every entry of
  `secret_fields` is described there;
- it parses with the core's parser (`tranibor_core.procedures.parse`), as `tranibor plugin validate` does.

From core 0.28.2 (ADR 0215), the console shows it beside the form when the server is configured, and the
core's documentation site publishes it (its connector pages are generated from this repository's
procedures). The `templates` job
holds every server to it (`tests/test_procedures.py`, from a core checkout:
`uv run --frozen pytest -q -p no:cacheprovider <this repo>/tests`).

## Matrix cases and a server's fake

A plugin may ship cases of the core's eval matrix (graalsystems/tranibor#1372; `metadata.core` from
0.23.2, the first core whose schemas accept them): agent cases in the core's own format
(`tranibor_playbooks.evals.agents`; the core's runbook `docs/runbooks/live-eval-matrix.md`, "A plugin's
cases"), declared in the manifest:

```yaml
contents:
  servers: [servers/servicenow.yaml]
  matrix_cases:
    - evals/matrix/itsm-triage-similar-incidents.yaml
```

`tranibor plugin validate` checks what the matrix needs to find and play a case: a `task`, an `agent` the
plugin ships (or the case's own `agent_spec`), `tools_cells` among `mcp` and `scripts`, and each connector
that is a plugin's server written `plugin:<this plugin>/<server>` under the server's own name, since the
agent's tools are `<server>__<operation>`:

```yaml
connectors:
  servicenow: { kind: "plugin:servicenow/servicenow", scene: default }
```

The installer keeps the cases and never plays them. A server a case connects to declares how the platform
runs it on its fake:

```yaml
# servers/<server>.yaml
fake:
  env: {SERVICENOW_FAKE: "1"}   # what switches the server to its seeded fake instance
  scenes: [default]             # the scenes that fake serves (`default` when absent)
```

None of its variables may be `TRANIBOR_*`, `CHOREGOS_*` or one that `config_schema` or `secret_fields` reaches
(`validate` refuses both), so no administrator switches a server to its fake. The server must start on its
fake with exactly these variables plus the platform's token, `TRANIBOR_SERVER_TOKEN` (`CHOREGOS_SERVER_TOKEN` from a
core before the rename: a server reads both, `tests/test_server_token.py`), and nothing of its
configuration: that is how the platform starts it. During the rename's R4 window (core ADR 0176) the
server's Secret gives the token under both names (core S80-35); read `TRANIBOR_SERVER_TOKEN` first and fall back
to the former, since the core removes the former when the window closes.

How the core's matrix plays them:

- **Scripted cell** (the core's CI, `TRANIBOR_FAKES=1`): the matrix names a case `plugin:<plugin>/<file
  stem>` and loads it from the offline plugins (`TRANIBOR_PLUGINS_OFFLINE`, else the core's
  `plugins/offline/`). It reaches the case's server as the broker does, over MCP with a bearer token, on a
  fake registered for it in the process (`tranibor_adapters.fakes.plugin_servers.use_fake_plugin_server`):
  the core's CI registers a recording of the server's fake (`FakePluginServer`); without a registered fake
  the case is skipped, never failed. The server's declaration governs its tools: the declared operations
  only, their access, `human_approval` never lowered, the platform's arguments (`action_key`) filled on an
  executed write.
- **Live cell**: the eval organisation installs the plugin. Where the deployment sets
  `TRANIBOR_EVAL_FAKE_SCENES=1`, the platform runs beside the server an eval instance (the same image,
  `fake.env` and the platform's token, no configuration, no egress), and the matrix's seeding binds the
  connector nobody configured to it.

This repository plays its cases the scripted way (CI's `matrix` job):
`plugins/servicenow/server/tests/test_matrix_cases.py` points `TRANIBOR_PLUGINS_OFFLINE` (or its former name) at `plugins/`,
registers the server's own app on its fake (`build_app` with `fake.env` and the token, behind
`httpx.ASGITransport`), plays each case in each of its cells with the core's `eval_doors.play` and judges
it with the core's `judge`. From a core checkout, v0.23.2 or later:

```sh
cd <core checkout> && uv sync --frozen
PYTHONPATH=<this repo>/plugins/servicenow/server/src uv run --frozen pytest -q -p no:cacheprovider \
    <this repo>/plugins/servicenow/server/tests/test_matrix_cases.py
```

The core plays a published release's cases once its offline lock carries that release: a core maintainer
moves the lock with `uv run python -m tools.plugins.update_lock --plugin <plugin>@<version>`.

## Publishing

On every push to `main` (or a manual run on `main`), `.github/workflows/publish.yml` runs
`scripts/publish.py`. For each plugin whose `metadata.version` is not in the registry yet:

1. **Its servers.** A server whose `image` is the placeholder
   `ghcr.io/graalsystems/tranibor-agents/<image>@sha256:0000…0000` is built from `server/<server>/`
   (or `server/` when the plugin has one server), pushed as `<image>:<plugin version>` and signed.
   The image repository is the one the server file names; it must live under
   `ghcr.io/graalsystems/tranibor-agents/` and not be a plugin's name or `index`. An image already
   pinned to a real digest is used as it is.
2. **The digest goes into the packed descriptor, not into git.** The declared files are copied to a
   staging directory, the placeholder replaced there by the image's digest, and the copy is packed with
   the core's `tranibor plugin pack`. The core's format does not need the source to carry the digest:
   `pack` treats `image` as any other byte of the server file, and the content digest the index records
   is the packed archive's, which holds the real digest. The repository keeps the placeholder, which is
   never pullable, so nothing generated is committed back. Consequence: for a plugin with a server, the
   digest `tranibor plugin validate` prints on the tree is not the published one.
3. **The plugin.** ORAS pushes the archive as `ghcr.io/graalsystems/tranibor-agents/<plugin>:<version>`
   (one layer, `application/vnd.choregos.plugin.v1.tar+gzip`, the only media type the core reads, 0.30.0
   included); cosign signs its manifest digest.
4. **The index.** The published `ghcr.io/graalsystems/tranibor-agents/index` (tag `latest`, one
   layer `application/vnd.choregos.plugin-index.v1+yaml`, `apiVersion: choregos/v1`: every deployment reads
   the same index, and a core before 0.30.0 reads only that) is read, the new versions added — every
   version ever published stays listed, with its digest and `core` range — and it is pushed and signed.

Signatures are keyless, with the workflow's GitHub OIDC token: the identity is
`https://github.com/graalsystems/tranibor-agents/.github/workflows/publish.yml@refs/heads/main`, the
issuer `https://token.actions.githubusercontent.com`, as in the README's trust policy.

**A published version is immutable.** When `<plugin>:<version>` already exists, the published archive is
pulled and the tree — with the images the published version pins — must give the same content digest;
otherwise the run fails before anything is pushed: bump `metadata.version`. A change to a server's
source alone (`server/`) is not part of the plugin's content, so it is not detected: bump the version
with it, or the new code never ships. A run that stopped half-way is safe to replay: an image tag
already pushed is reused, not rebuilt.

A new GHCR package is private until an organisation owner makes it public (package settings →
visibility); do it once for each new plugin, image and the index, or deployments cannot pull them.

To try the publication without a registry:

```sh
cd <core checkout> && uv sync --package tranibor-cli
uv run --package tranibor-cli python <this repo>/scripts/publish.py <this repo>/plugins \
    --registry ghcr.io/graalsystems/tranibor-agents --offline --work /tmp/publish
uv run --package tranibor-cli --with pytest pytest <this repo>/scripts/tests
```
