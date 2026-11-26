# Procurement operations plugin

A purchasing department's governed writes, over one MCP server. Split out of the `procurement` plugin at its
0.3.0 (core finding graalsystems/tranibor#2134): a plugin that brings a server cannot be installed where no plugin
server runs (the local `docker compose` install; plugin servers only run on Kubernetes), and the comparison of
offers needs none, so it stays in [`procurement`](../procurement).

**Versioning.** `procurement-operations` **starts at 0.3.0, not 0.1.0**: it continues `procurement`'s numbering
because `procurement` 0.2.0 and 0.2.1 already published the server image `purchasing-mcp:<plugin version>`, and
the image tag must keep increasing. The image repository is still `ghcr.io/graalsystems/tranibor-agents/purchasing-mcp`.
It does not require `procurement`: nothing here references an agent, skill or template of it.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `procurement-operations` 0.3.5, core `>=0.28.1,<1.0.0` |
| Agent | [`agents/supplier-file-checker.yaml`](agents/supplier-file-checker.yaml) | catalogue role `structure`: copies a supplier's identity and dated documents into `supplier_file` |
| Agent | [`agents/renewal-analyst.yaml`](agents/renewal-analyst.yaml) | catalogue role `renewal_memo`: writes `renewal_memo_markdown` for one contract ending soon (`get_contract` only) |
| Agent | [`agents/request-qualifier.yaml`](agents/request-qualifier.yaml) | catalogue role `qualify`: reads a purchase request against the house policy and the framework agreements, proposes a route in `qualification` and a `brief_markdown` |
| Agent | [`agents/purchasing-desk.yaml`](agents/purchasing-desk.yaml) | catalogue role `purchasing_desk`, runtime `prompt`: talks with an employee, reads the agreements in force and the policy, proposes the request; two reads, no memory |
| Assistant | [`assistants/purchasing-desk.yaml`](assistants/purchasing-desk.yaml) | wraps `purchasing-desk`; the need `purchasing` (reads only), the bound knowledge `purchasing_policy`, one launch `purchase_request` the person confirms; conversation suite in [`evals/purchasing-desk-conversations.yaml`](evals/purchasing-desk-conversations.yaml) |
| Template | [`templates/procurement-operations`](templates/procurement-operations/README.md) | workflows `supplier-onboarding`, `purchase-request`, `contract-renewals` and `contract-renewal-memo`; needs the connector `purchasing` |
| Server | [`servers/purchasing.yaml`](servers/purchasing.yaml), source [`server/`](server/PROFILE.md) | the MCP server `purchasing`: eleven operations of a fixed profile, served by Odoo 19 and 20 and by a seeded fake |
| Procedure | [`procedures/connector.mcp.purchasing.md`](procedures/connector.mcp.purchasing.md) | how the server is connected to Odoo: a dedicated user, the least rights, an API key |
| Evals | [`evals/`](evals) | instruction checks and live cases of each agent, and the three matrix cases of [`evals/matrix/`](evals/matrix) (named `plugin:procurement-operations/<file stem>`) |

## What version 0.3.5 fixes

The core 0.30.0 lays the knowledge bases under `.tranibor/knowledge/` (its ADR 0226, R9a), no longer under
`.choregos/knowledge/`: `request-qualifier` (catalogue version 2) read the house policy under the former only,
and found nothing there on that core. It now names both, the former for a core before 0.30.0.

## What version 0.3.4 fixes

What the core's third night found (core S85-14, run 45479922075): 0.3.3 read the reference unit in
`ir.model.data`, which the integration user may not read (Odoo 19 answered 403, "Access Rights"), and looked for
the line's unit field under two names Odoo 20 uses neither of. The field is now the line's many2one to `uom.uom`
(the known names first), and the unit is read in `uom.uom` by its English name, "Units". Nothing needs a right
beyond the Purchase / User level the procedure gives.

## What version 0.3.3 fixes

What the core's second night found (core S85-14, 2026-11-24): on Odoo 20, `create_purchase_request` failed
with `Expected singleton: uom.uom()`. A line without a product has no unit to derive; Odoo 19 accepts it, Odoo 20
does not. Every line now carries Odoo's reference unit ("Units", `uom.product_uom_unit`), by the field the version
has (`product_uom_id`, else `product_uom`), asked of Odoo once (`server/tests/test_odoo_backend.py`).

## What version 0.3.2 fixes

The server reads the platform's token under the name core 0.29 gives it, `TRANIBOR_SERVER_TOKEN`, and an
older core's `CHOREGOS_SERVER_TOKEN` when it is absent: 0.3.1 read only the latter and refused to start on
core 0.29 (#35).

## What version 0.3.1 fixes

What the core's first nightly run against a real Odoo found (core S85-14, 2026-11-23): the server read the
platform's idempotency key for an IBAN shape. The key is an opaque hash (24 hexadecimal characters); one in
eighteen starts with two letters and two digits and was refused, so about one governed write in eighteen failed.
The value of the top-level `action_key` is no longer read for an IBAN shape; its name still is, and every other
argument and field still is (`server/tests/test_bank_details.py`).

## What version 0.3.0 changes

The plugin is born from `procurement` 0.2.1 (core finding graalsystems/tranibor#2134). Moved here, unchanged
except for the plugin name in `plugin:procurement-operations/purchasing` (the server's connector kind in the
assistant and the matrix cases): the server and its tests, the procedure, the four agents above, the assistant,
the template `procurement-operations` and the evals and matrix cases that go with them. The server image repository
stays `purchasing-mcp`. A deployment that had `procurement` 0.2.x installed for these parts installs this plugin instead.

## What version 0.2.1 fixed (then in `procurement`)

What the core's end-to-end tests found when they installed the signed 0.2.0 archive (core S85-10):

- Two eval needles were split across a line, so every core refused to install the archive; `tests/test_agent_evals.py` now plays each suite as the core's installer does, level 1 and the deterministic cases.

## What version 0.2.0 added (then in `procurement`): the server `purchasing`

An MCP server the platform runs and governs, named after what it does and not after a vendor: five reads
(`search_suppliers`, `get_supplier`, `list_framework_agreements`, `list_contracts`, `get_contract`) and six
writes (`create_supplier`, `update_supplier`, `archive_supplier`, `add_contract_note`,
`create_purchase_request`, `cancel_purchase_request`). Every write waits for a person, takes the platform's
`action_key` (the same key twice writes once), and three come with a compensation. No operation takes or
returns a bank detail. The profile is fixed in [`server/PROFILE.md`](server/PROFILE.md).

- **Proves**: the profile end to end on a seeded, in-memory fake, in three scenes; that the descriptor and the
  served tools agree; the idempotency, compensation and no-bank-detail rules, by tests that fail without them; and
  the **Odoo backend** (Odoo 19 and 20, through the vendor's JSON-2 API and an API key) on hand-written exchanges:
  every mapped operation, its error paths and its paging, typed filters, the replay of a write after a restart, no
  bank detail on any request, no key or traceback in any error.
- **Does not prove**: anything against a running Odoo: the live proof is the core's S85-14 on a container. What
  Odoo cannot say is *not supported* and written in [`server/PROFILE.md`](server/PROFILE.md): an agreement has no
  category, a contract has no value and is the same record as an agreement, a unit of measure is written in a
  line's name, and on Odoo 20 a supplier's registration number is not supported. Odoo 18 and older are not served.
  The idempotence that survives a restart does not cover two identical calls racing on two replicas.

## What version 0.2.0 added (then in `procurement`): supplier onboarding

The agent `supplier-file-checker` and the template `procurement-operations`, whose workflow
`supplier-onboarding` is the plugin's first governed write: a supplier record created in the purchasing system
after a person of the group `supplier-data` reviewed a file whose completeness a job measured, and after two
people of that group approved the creation. The agent copies and dates (it never says a document is valid or
expired, and never copies a bank detail); the job `supplier-check` measures what is missing, expired,
different from the request or already held. **No bank detail is handled.**

- **Proves**: the workflow's structure (its only action, its approval, its measured gates, its schema), by
  tests that fail without each guarantee.
- **Does not prove**: a run of the workflow, or what a model writes (the agent's live evals are not played
  by CI); that a document is genuine.

## What version 0.2.0 added (then in `procurement`): purchase requests, qualified

The agent `request-qualifier` and the workflow `purchase-request` (template `procurement-operations`): an
employee's request is read against the framework agreements in force and the organisation's own policy (the
knowledge base `purchasing-policy`, shipped empty), one route is proposed, and a buyer chooses the route,
attested. The workflow opens nothing in the purchasing system. The agent must always look at the agreements
(a gate reads the ledger), writes only numbers the requester typed, and asks when the amount is missing.

- **Proves**: the workflow's structure (no action, the four gates, the route values, the attested review), and
  the core's real gates on outputs an agent could write, by tests that fail without each guarantee.
- **Does not prove**: what a model writes, that a route is the right one, or a run (the live evals are not played
  by CI). An agreement's id is read as a reference, not an amount (`identifiers: skip`, see the template README).

## What a reader must know (ADR 0202)

- **No OCR.** A scanned supplier document is not read; it stops at a person.
- **No injection detector.** The agents are told to treat a supplier document as data, but the protection is mechanical: no write outside the platform without a person, number gates that bound what an agent can claim, no credential and no network in the agents.
- **Nothing is written outside the platform without a person.** The
  server of 0.2.0 has writes to Odoo, each held for a person and re-authenticated; the first version of the
  backend has had no live run.

## Checks

```sh
tranibor plugin validate plugins/procurement-operations
tranibor plugin pack plugins/procurement-operations -o procurement-operations-0.3.5.tar.gz   # same bytes every time
```

The server's own checks, in `server/` (the `servers` job): `uv sync --all-extras --dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy --strict src`, `uv run pytest -q`.
