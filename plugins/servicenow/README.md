# ServiceNow plugin

Triage ServiceNow incidents with a Tranibor agent, under human approval.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `servicenow` 0.2.19, core `>=0.29.2,<1.0.0` (the first core whose templates ship their projects' start rules, graalsystems/tranibor#2200) |
| Agent | [`agents/itsm-triager.yaml`](agents/itsm-triager.yaml) | reads, cites, proposes; every write waits for a person |
| Skill | [`skills/servicenow-triage`](skills/servicenow-triage/SKILL.md) | impact/urgency matrix, escalation, work note vs customer comment |
| Template | [`templates/itsm-incident-triage`](templates/itsm-incident-triage/README.md) | queue → triage → review (service desk) → apply → done |
| Evals | [`evals/itsm-triager.yaml`](evals/itsm-triager.yaml) | instruction checks, and six live cases on the fake instance |
| Matrix cases | [`evals/matrix/`](evals/matrix) | E3 and C1 of the core's tool-script matrix, in the core's agent-case format, declared in `contents.matrix_cases` (below) |
| Procedure | [`procedures/connector.mcp.servicenow.md`](procedures/connector.mcp.servicenow.md) | OAuth integration, custom role, Business Rule |
| Server | [`servers/servicenow.yaml`](servers/servicenow.yaml), [`server/`](server) | the MCP server over the Table API (below), and its `fake` for the eval matrix |

## What the agent declares (ADR 0127)

`itsm-triager` declares the five components of ADR 0127 (the core's S60-04), so that a version no longer
inherits any of them from its project; its catalogue version is 2 and no instruction changed.

| Agent | Model | `catalogue` | `extensions` | `web` | `memories` | Why |
|:--|:--|:--|:--|:--|:--|:--|
| `itsm-triager` | `profile:by_size` | none | none | none | `project/shared` write | Everything it reads and proposes goes through its `servicenow` connector (`mcp_servers`); it never browses, and the ticket's text is data, never a link to follow. |

```sh
tranibor plugin validate plugins/servicenow
tranibor plugin pack plugins/servicenow -o servicenow-0.2.19.tar.gz   # same bytes every time
```

`validate` reads the whole directory: run it on a clean tree (no `server/.venv` nor caches), as CI does.

## The agent: `itsm-triager`

It reads the incident (`get_record`), its journal, its CI and that CI's relations, finds similar resolved
incidents and published knowledge articles, and writes `{"itsm_triage_markdown": "…"}` with four
sections: **Findings**, **Proposed classification**, **Proposed actions**, **Confidence**. Every
incident and article is cited by number.

- It proposes category, impact, urgency and assignment group with their evidence — never priority.
- It proposes a work note; a customer comment and a resolution **only when a published fix exists**.
- A vague incident is never resolved (questions for the caller instead); a VIP caller gets no comment
  without a person's approval; text in a ticket is data, never instructions — an instruction found
  there is reported as a suspected prompt injection and not obeyed.
- Its tools: the seven reads, `add_work_note`, `update_record`, `add_comment`, `resolve_incident`. Not
  the `tracker_*` tools, not `create_change_request`. 30 turns, 15 minutes, 1 USD.

The evals' live cases run against the fake instance's seed: the duplicate `INC0010002` (of
`INC0010001`, fixed by `KB0010001`), the vague `INC0010003`, the VIP `INC0010004`, the injection in
`INC0010005`, and the two scenarios of the matrix cases below: `INC0012345` and the change its similar
incidents share, the vague `INC0012399`.

## The matrix cases

[`evals/matrix/`](evals/matrix) holds two cases of the core's tool-script matrix (S57-09), in the core's
agent-case format (`choregos_playbooks.evals.agents`, the core's runbook
`docs/runbooks/live-eval-matrix.md`), declared in `plugin.yaml`'s `contents.matrix_cases`
(graalsystems/tranibor#1372): the core's eval matrix names them `plugin:servicenow/<file stem>` and
plays each in two cells, `mcp` (the model calls the tools one by one) and `scripts` (it may call them from
a script, `from choregos_tools import …`), judging it on its answer and on what its run did. Their
connector is this plugin's server, `{kind: "plugin:servicenow/servicenow", scene: default}`.

| Case | Scenario | What it asserts |
|:--|:--|:--|
| [`itsm-triage-similar-incidents`](evals/matrix/itsm-triage-similar-incidents.yaml) | E3: `INC0012345`, the Lyon site's VPN; ten similar incidents read one by one, two resolved by `CHG0003311` (MTU on the Lyon concentrator), `KB0010042` | the answer cites `CHG0003311`, `KB0010042`, `MTU`; one `add_work_note` executed (writes allowed), one `add_comment` proposed and not executed (`human_approval`); in the `scripts` cell at most 5 model requests and at least 22 calls from the script |
| [`itsm-triage-vague-incident`](evals/matrix/itsm-triage-vague-incident.yaml) | C1, a control: `INC0012399` "The application is slow", no CI | `resolve_incident` neither executed, proposed nor called, in both cells |

`tranibor plugin validate` checks them (their agent, their doors, their connector naming a server of
this plugin) and `pack` ships them; the installer keeps them and never plays them. The server's file
declares how the platform runs it on its fake, `fake: {env: {SERVICENOW_FAKE: "1"}, scenes: [default]}`:
in a live cell, where the deployment sets `TRANIBOR_EVAL_FAKE_SCENES=1` (`CHOREGOS_EVAL_FAKE_SCENES` while the rename's window lasts), the platform runs beside the
server an eval instance with exactly that variable and its own token, nothing of the configuration and no
egress. In a scripted cell, the core reaches the server on a fake registered in its process (in the core's
CI, a recording of this server's fake).

Three tests hold them here:

- `server/tests/test_matrix_dataset.py`, in the server's own environment: what the cases read on the fake
  instance, and every call of their `mcp` cell served with the case's arguments;
- `server/tests/test_descriptor.py`, in the same environment: the declared `fake` is what switches the
  server to its fake, with the platform's token, and no configuration field reaches it;
- `server/tests/test_matrix_cases.py`, in the core's workspace (CI's `matrix` job), as the core's scripted
  matrix plays them: the cases found through this tree's `plugins/` as the offline plugins
  (`TRANIBOR_PLUGINS_OFFLINE`, formerly `CHOREGOS_PLUGINS_OFFLINE`), this server's own app registered as the fake of `servicenow/servicenow`
  (`use_fake_plugin_server`, started with the `fake` variables and the platform's token), each case in each
  of its cells through the core's real doors (`eval_doors.play`: the run's MCP server, and the script with
  `python3 -S`, the generated client and the tool bridge), then the core's judge; and the assertions that
  bite (the work note under approval, a resolved vague incident). Locally, from a core checkout (v0.23.2
  or later) synced with `uv sync --frozen`:

  ```sh
  PYTHONPATH=<this repo>/plugins/servicenow/server/src \
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/servicenow/server/tests/test_matrix_cases.py
  ```

## What the format cannot say yet

Found while writing this plugin against the core at `49fc8082` (S43-02):

- `plugin-server.schema.json` is closed and has no `path`/`health`, no compensation arguments, no
  platform-filled arguments (`action_key`): they are kept as comments and operation descriptions in
  `servers/servicenow.yaml`, and `server/tests/test_descriptor.py` checks them.
- A template cannot name a plugin's server as its tracker (see the template's README).
- A procedure's `{{webhook_url:…}}` only knows the core's webhook sources; the Business Rule's URL is
  `{{plugin_webhook_url:servicenow/servicenow}}`, which the core renders as
  `<api>/api/v1/webhooks/plugins/<org>/servicenow/servicenow`.
- The connector an installed plugin server becomes is assumed to be named after the server
  (`servicenow`), hence the tool names `servicenow__get_record`, … in the agent's instructions.
- An eval suite is only required to name its agent; this one uses the catalogue's `eval_cases` shape plus
  a `cases` list in the `AgentEvalCase` shape.

## The server

An MCP server (streamable HTTP at `/mcp`) over the ServiceNow **Table API** (and the **Change
Management API** for a change request's state, which its change model governs):

| Tools | Access | Notes |
|:--|:--|:--|
| `search_records`, `get_record`, `get_journal`, `get_ci`, `list_ci_relations`, `search_knowledge`, `similar_incidents` | read | `search_knowledge`: published articles only; `similar_incidents`: resolved or closed in the last 90 days, same CI or same terms |
| `add_work_note` | write | internal; idempotent through a `[choregos:<key>]` marker |
| `update_record` | write | category, subcategory, impact, urgency, assignment group, assignee, CI — **never priority**; returns the previous values, its compensation restores them |
| `add_comment` | write, **a person approves** | the caller sees it |
| `resolve_incident` | write, **a person approves** | compensation: `reopen_incident` |
| `reopen_incident` | write | undoes a resolution |
| `create_change_request` | write, **a person approves** | idempotent through the platform's `action_key` (its `correlation_id`) |
| `tracker_*` (8 tools) | read / write | the tracker profile, see [server/TRACKER_PROFILE.md](server/TRACKER_PROFILE.md) |

Every call but `GET /healthz` needs `Authorization: Bearer $TRANIBOR_SERVER_TOKEN`, the token the
platform generates and presents (ADR 0094; an older core's `CHOREGOS_SERVER_TOKEN` is still read),
compared in constant time; without the variable the server refuses to start. It also serves `GET /healthz` and `POST /events`, the webhook a ServiceNow Business Rule
posts to through the platform (the shared secret in `X-Choregos-Secret`, compared in constant time; answers
`[{kind, item_key, delivery_id}]` with `delivery_id = sys_id:sys_mod_count`).

The rules it enforces before any call to ServiceNow:

- a **closed list of tables**: `incident`, `sc_req_item`, `change_request`, `problem`, `kb_knowledge`,
  `cmdb_ci`, `cmdb_rel_ci`, `sys_user_group`, `sys_choice`, `sys_journal_field`; writes on the first four only;
- **typed filters** (`{field, op, value}`) turned into an encoded query; a raw `sysparm_query` is never
  accepted. Values have `^` escaped as `^^`, and `javascript:` and control characters are refused, so a
  term like `x^ORactive=false` cannot change the query;
- every read names its fields (`sysparm_fields`), asks for `sysparm_display_value=all` (each record has
  `fields` with stored values and `display` with labels) and returns at most 50 rows, with `total` (the
  instance's count) and `truncated` when the cap left some out; the tracker's queue poll reads in pages,
  up to 200 keys;
- state codes come from `sys_choice` (cached for an hour), not from constants.

Governance is the platform's: `servers/servicenow.yaml` lists every tool with its access, the three
writes that always need a person, the compensations, and the only host the server may reach
(`egress.from_fields: [base_url]`). A tool not listed there is refused by the platform.

## Configuration

Environment variables (the platform fills them from the descriptor's `config_schema`; `secret_fields`
are sealed):

| Variable | Meaning |
|:--|:--|
| `SERVICENOW_BASE_URL` | `https://<instance>.service-now.com` |
| `SERVICENOW_AUTH` | `oauth` (client credentials, default) or `basic` (a dedicated integration user: older releases, PDIs) |
| `SERVICENOW_CLIENT_ID`, `SERVICENOW_CLIENT_SECRET` | OAuth inbound integration (`auth=oauth`); the token is cached until shortly before it expires and renewed once on a 401 |
| `SERVICENOW_USERNAME`, `SERVICENOW_PASSWORD` | the integration user (`auth=basic`); give it a custom role, never `admin` |
| `SERVICENOW_WEBHOOK_SECRET` | the secret the Business Rule sends; without it, `/events` refuses everything |
| `SERVICENOW_ASSIGNMENT_GROUPS` | comma-separated group names or sys_ids whose queue `tracker_list_candidates` polls |
| `TRANIBOR_SERVER_TOKEN` | the platform's bearer token, required on every call but `/healthz`; set by the platform, never typed (an older core sets `CHOREGOS_SERVER_TOKEN`, still read) |
| `SERVICENOW_FAKE` | `1` for the fake instance (below); what the server's `fake` declares, never a configuration field |
| `TRANIBOR_SERVER_INSECURE_DEV` | `1` with `SERVICENOW_FAKE=1` only: run without a token, for a local demo |
| `PORT` | listening port, `8080` by default |

## Fake mode

`SERVICENOW_FAKE=1` puts a seeded in-memory instance under the real client (as an `httpx` transport),
so the encoded queries, field lists and authentication follow the production path. No ServiceNow
variable is needed (a token is, or `TRANIBOR_SERVER_INSECURE_DEV=1`); the webhook secret defaults to `fake-webhook-secret` and the queue to
`Service Desk`. The seed:

- twelve incidents: a duplicate pair (`INC0010001`/`INC0010002`, mobile mail), a vague one (`INC0010003`),
  a VIP caller (`INC0010004`, the CFO), one whose description carries a prompt injection (`INC0010005`),
  resolved ones that `similar_incidents` finds (and one too old to be found);
- a request item, a change, a problem; CIs with relations; knowledge articles in the published, draft
  and retired states; groups; the `sys_choice` states, impacts, urgencies and priorities;
- the matrix cases' data: `INC0012345` on the Lyon concentrator `vpn-lyon-01`, its ten similar incidents
  (nine of them with their journals), the change `CHG0003311` and the article `KB0010042`; and the vague
  `INC0012399`.

### Seeding the fake from a file

`SERVICENOW_FAKE_SEED=<path>` (with `SERVICENOW_FAKE=1`) makes the fake hold the incidents, groups,
users, CIs and their relations, knowledge articles, journals and `sys_choice` rows of a JSON file, **in
place of** the built-in seed above, so a demo shows its own incidents. Without the variable, the built-in
seed is the instance. The format is documented at the top of
[`seed_file.py`](server/src/choregos_servicenow/seed_file.py); every section is optional (`choices`
absent: the built-in states, impacts and priorities), names are checked against the file's own rows, dates
are days before now so a seed never goes stale, and a mistake stops the server at start with the file, the
entry and the reason. [`server/seeds/sample-company.json`](server/seeds/sample-company.json) is a complete
neutral example (a generic company, a head office and a branch: a duplicate pair, a vague incident, a VIP
caller, a VPN incident whose similar resolved incident and published article name the fix); the image
carries it at `/app/seeds/sample-company.json`. The matrix cases (`evals/matrix/`) read the built-in seed:
they do not run on a custom file.

```sh
SERVICENOW_FAKE=1 SERVICENOW_FAKE_SEED=server/seeds/sample-company.json TRANIBOR_SERVER_INSECURE_DEV=1 \
  uv run choregos-servicenow
docker run --rm -p 8080:8080 -e SERVICENOW_FAKE=1 -e TRANIBOR_SERVER_INSECURE_DEV=1 \
  -e SERVICENOW_FAKE_SEED=/seeds/demo.json -v "$PWD/demo.json:/seeds/demo.json:ro" <the server image>
```

The variable is not a configuration field and not in the server's `fake.env`: the platform's eval
instance (core #1372) runs the built-in seed. Only whoever starts the server on its fake sets it.

## Run locally

```sh
cd plugins/servicenow/server
uv sync --dev
SERVICENOW_FAKE=1 TRANIBOR_SERVER_INSECURE_DEV=1 uv run choregos-servicenow   # http://localhost:8080/mcp
curl localhost:8080/healthz
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && uv run pytest -q
```

Against a real instance (a Personal Developer Instance works):

```sh
SERVICENOW_BASE_URL=https://dev12345.service-now.com SERVICENOW_AUTH=basic \
SERVICENOW_USERNAME=choregos.integration SERVICENOW_PASSWORD=… \
SERVICENOW_WEBHOOK_SECRET=… TRANIBOR_SERVER_TOKEN=… uv run choregos-servicenow
```

### The live suite

`server/tests/live` runs the server, through MCP, against a real instance: both authentications, the
tracker profile on an incident (filed, noted twice and written once, moved, listed in the queue, closed),
the triage writes and their compensations, a value the instance keeps silently, the reads the triager
uses and what their bound left out, a change request filed once and moved along its change model, a request
item on `task`'s states. It is
skipped without its variables (listed in `server/tests/live/conftest.py`); every record it files is tagged
`[choregos-live <run>]` and deleted at the end when a cleanup user is given.

```sh
TRANIBOR_LIVE_SERVICENOW_URL=https://dev12345.service-now.com \
TRANIBOR_LIVE_SERVICENOW_USERNAME=choregos.integration TRANIBOR_LIVE_SERVICENOW_PASSWORD=… \
TRANIBOR_LIVE_SERVICENOW_CLIENT_ID=… TRANIBOR_LIVE_SERVICENOW_CLIENT_SECRET=… \
TRANIBOR_LIVE_SERVICENOW_CLEANUP_USERNAME=… TRANIBOR_LIVE_SERVICENOW_CLEANUP_PASSWORD=… \
  uv run pytest tests/live -q
```

First green run: 16 tests on a Personal Developer Instance of the Australia release (patch 7),
2026-11-21, as a user holding only the procedure's role, by basic authentication and by OAuth client
credentials; 8 more as a user holding `itil`. What the fake did not play and the instance showed, each now
in the fake and under a unit test: a request item's states live on `task`; `sys_choice` refuses an `itil`
user (403) and `sys_journal_field` answers it no row; a write the instance will not apply answers 200 with
the old value; a journal header's date is in the reading user's time zone; a refused write's reason is in
the error's `detail`.

Journal entries are read oldest first by `sys_created_on`, then by their id (0.2.19, finding #63): the instance
stores the time to the second and exposes no sequence of the journal, so the two status notes the suite writes
in one second came back in either order, and the suite failed one run in a few. Within one second the order is
the ids', not the order of writing; it is the same on every read and `tracker_upsert_status` reads its newest
status in it (`server/TRACKER_PROFILE.md`). Under unit tests red without it, and asserted so by the suite.

The change request's move along its change model and the queue poll's paging (0.2.12, findings #24 and #25)
are under unit tests and in this suite (`test_a_change_request_is_filed_once_and_its_state_model_is_said`,
`test_reads_say_what_their_bound_left_out`). The suite passed (9 tests) on the Australia Personal Developer
Instance on 2026-11-24, by basic authentication as the instance's admin: a change moved New to Assess
through the Change Management API, an unreachable state and the close refused before any write (#24),
`total`, `truncated` and the paging (#25). The change must be filed with an assignment group, or the
instance's change model refuses New to Assess (#46).

On the same instance on 2026-11-24 (0.2.15), the suite passed **18 tests: 9 by basic authentication and 9 by
OAuth client credentials, as a non-admin integration user** built by the procedure through the Table API
(`choregos.integration`, web-service-only, internal integration user), the instance's admin as cleanup
user. The user's roles, each found necessary: `choregos_integration` (the procedure's role and its ACLs),
`snc_basic_auth_api_access` (basic authentication only) and `sn_change_read`. Without `sn_change_read`
the Change Management API answers the next states with 200 and `available_states: [null]`, not a 403;
the server said the change "reaches: None", it now says the role is missing (0.2.15, under a unit test
red without it, and read on the instance). `sn_change_write` is not needed: the change's `PATCH` passed
with `sn_change_read` and the custom role's `change_request` write ACLs. `sn_change_read` brings 16
contained roles of its own (`cmdb_read`, `view_changer`, …): the least the Change Management API asks for.
OAuth: an **OAuth Client** in the application registry (`oauth_entity`, `type = client`,
`inbound_grant_type = client_credential`, **OAuth Application User** = `choregos.integration`) and the
system property
`glide.oauth.inbound.client.credential.grant_type.enabled = true`, which the instance did not hold:
without it, or set to `false`, `/oauth_token.do` answers `401 access_denied`.

### What is proven on a deployment, and what is not

The core's own live suite (`tests/live/test_servicenow_live.py` in graalsystems/tranibor, `-m live`; the
server runs by hand and the test reads `TRANIBOR_LIVE_SERVICENOW_MCP_URL`, `_MCP_TOKEN` and `_WEBHOOK_SECRET`)
drives the core's `PluginTracker` through this server to the same instance (4 passed, 2026-11-21, server
0.1.3). On 2026-11-24 the governed path ran end to end on an operator's dev cluster (core 0.29.0, server
0.2.9, the same instance, core story S80-20): the plugin installed from the `tranibor` source with a second
administrator's approval, the connector configured in the console, a project from the template, a new incident
opened as a work item by the poll, triaged by the agent (reads only), the review settled,
`servicenow.apply_triage` approved by a second person and applied once (`update_record` then `add_work_note`,
read back on the instance). It found graalsystems/tranibor-agents#34, #36, #37 and #38, all fixed. Not proven
there that day: the Business Rule's delivery to `POST /webhooks/plugins/…` (the work item came from the poll;
proven later, below), OAuth client credentials on the cluster (Basic was used, OAuth is proven on the instance
above), and a human's governance (the approvers were test accounts driven by an agent). No finding is open.

**A nightly, without CI secrets** (core story S80-46). The P1 nightly runs on the deployment itself: a project
with an internal tracker schedules one work item a night whose only step is a job (the core's
`examples/servicenow-nightly/workflow.yaml`) that files an incident through this plugin's connector
(`tracker_create_item`, allowed for that project's group alone) and reads it back with `tracker_fetch_item`
until the triage project's poll opened it, the agent triaged it, and the platform set it In Progress and asked
for the review. The instance's credentials stay in the connector's sealed secrets. On 2026-11-24 (core 0.29.3,
plugin 0.2.15 installed and Ready) one night was green: the incident was opened by the poll after 48 s and
triaged after 140 s. The core's runbook `docs/runbooks/servicenow-nightly.md` says where to read a night and
what a hibernating or reclaimed PDI does to it. Fourteen green nights in a row are not proven yet.

**The webhook delivery is proven on a deployment** (2026-11-24, core story S80-47). It was first tried that day
and could not run: the dev deployment's API name resolved only on its private network, and the instance's
outbound HTTP log answered `-1`, `java.net.UnknownHostException`. Once that name was public, the procedure's
two system properties and its Business Rule (incident, after insert and update, filter on the Service Desk
group) posted an incident's insert to `/api/v1/webhooks/plugins/<org>/servicenow/servicenow`: `202` in the
instance's `sys_outbound_http_log`, and the work item opened by the delivery about four seconds after the
incident was filed (`initiated_by: tracker:plugin:servicenow/servicenow`, a received event
`created_work_item`), not by the poll (`tracker:sync`). The first delivery was dropped by the core as
unrouted, because an archived project still used the same tracker: graalsystems/tranibor#2221, fixed by
graalsystems/tranibor#2238. The rule stays in place for the nightly, whose incident it delivers on insert.

Reinstalling the plugin on that deployment needs a second administrator's approval in a browser session
(`decision_requires_session`: an API token cannot give it). For the nightly above, the owner had the pending
install (0.2.14) and the upgrade to 0.2.15 approved as the second test administrator by running the approval
route's own code in the API process, without that session; the audit's `plugin.approve` says so (`manual:
true` and a note).

The image: `docker build -t servicenow-mcp plugins/servicenow/server` (non-root, port 8080, exec-form
entry point). CI builds, signs and publishes it and writes its digest into `servers/servicenow.yaml`.
