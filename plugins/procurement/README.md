# Procurement plugin

Compare supplier offers with agents that are never trusted with arithmetic. This is the content half of
the plugin of the Tranibor core's ADR 0202 (`procurement is a plugin whose code half is a job image`).

**0.3.0 splits the plugin in two** (core finding graalsystems/tranibor#2134). `procurement` 0.2.x also shipped the
MCP server `purchasing`, and a core deployment that runs no plugin servers (the local `docker compose` install; plugin
servers only run on Kubernetes) refuses to install any plugin that brings a server, so it could never offer the
comparison of offers. This plugin now holds the comparison only and **brings no server**; the server, the
assistant, the operations template and the agents that need them live in
[`procurement-operations`](../procurement-operations) (which continues this plugin's numbering, starting at 0.3.0).
A deployment that used the operations parts of `procurement` 0.2.x installs `procurement-operations` for them.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `procurement` 0.3.0, core `>=0.28.1,<1.0.0`, no server |
| Agent | [`agents/offer-reader.yaml`](agents/offer-reader.yaml) | catalogue role `structure`: copies the offers into `offers_structured` |
| Agent | [`agents/sourcing-analyst.yaml`](agents/sourcing-analyst.yaml) | catalogue role `compare`: writes `comparison_memo_markdown` |
| Skills | [`skills/reading-supplier-offers`](skills/reading-supplier-offers/SKILL.md), [`skills/writing-comparison-memos`](skills/writing-comparison-memos/SKILL.md) | the output contract and rules of each agent |
| Template | [`templates/procurement-sourcing`](templates/procurement-sourcing/README.md) | workflow `offer-comparison`: extract, read, compute, memo, tier, award |
| Evals | [`evals/offer-reader.yaml`](evals/offer-reader.yaml), [`evals/sourcing-analyst.yaml`](evals/sourcing-analyst.yaml) | instruction checks, and live cases each, with invented suppliers and amounts |

- **Proves** (`tests/test_no_server.py`): the plugin loads with no server, assistant, procedure or matrix case,
  and the template needs no organisation connector.
- **Does not prove**: that a core installs it on a given deployment; that is the core's installer.

## What version 0.2.1 fixed

Two eval needles were split across a line of their agent's YAML, so every core refused to install the archive; and
the comparison's compute step did not hand `compare` the `offers_text` it reads, so every comparison stopped at
`needs_human`. `tests/test_agent_evals.py` plays each suite as the core's installer does, and a structure test holds
`t-compute`'s inputs to what `tranibor_procurement.compare` reads.

## What version 0.1.0 holds

The two agents, their two skills, their evals and the template `procurement-sourcing` that wires them
(extraction, the offer-reader, a computing step, the sourcing-analyst, a tier, a person's attested
decision). The job image `procurement` that extracts and computes is the core's. The `core` floor is `0.28.1`,
the first core release that carries the reserved input `attachments` (core ADR 0201), `output_sections` and the
job commands of `tranibor-procurement`.

Both agents declare `memories: []`, no catalogue tool, no extension, no web and no MCP server: supplier
documents are confidential and untrusted, and an offer must not reach another consultation.

## `offer-reader`

Given `offers_text` (the extracted Markdown of each handed file, amounts already in decimal-point
notation), it writes `offers_structured`: per supplier its files, its lines, its stated total, payment
terms, delivery, validity date and exclusions, plus the files it could not assign.

- **Guarantees**: it copies a number as the text writes it, computes nothing, leaves a missing value
  `null`, places every file exactly once, and reports an instruction found in an offer as a quoted
  remark in that supplier's `exclusions` instead of following it. A later gate refuses any number of its
  output that the extracted text does not hold.
- **Does not guarantee**: that the offer is right (a supplier's own arithmetic mistake is copied
  faithfully; the deterministic step finds it), that the right supplier was named for a file, or that a
  copied number is the one a person would read as the price.

## `sourcing-analyst`

Given `comparison` and `award_basis` (computed by a deterministic step), `offers_text` and, when a person
sent the memo back, `human_feedback`, it writes `comparison_memo_markdown` with seven sections: Summary,
Files read, Price comparison, Differences and exclusions, Checks that failed, Questions for the
suppliers, What this memo does not cover.

- **Guarantees**: every number comes from `comparison` or is quoted from the offers; every number of
  `award_basis`, each mismatch included, is written; it ranks by the measured total, lists what differs,
  answers each point of a person's feedback, writes in the language of the work item, and **does not
  recommend a supplier**: the award is a person's decision.
- **Does not guarantee**: that the lowest total deserves the award, anything about quality, references or
  risk, or that an instruction hidden in an offer cannot bend the wording of the memo.

## What a reader must know (ADR 0202)

- **No OCR.** A scanned offer is not read; it stops at a person.
- **No injection detector.** The agents are told to treat an offer as data and to report an instruction,
  but the protection is mechanical: no write outside the platform, number gates that bound what the memo
  can claim, no credential and no network in the agents, and a person who reads the memo.
- **Nothing is written outside the platform.** The template has no governed action.

## Checks

```sh
tranibor plugin validate plugins/procurement
tranibor plugin pack plugins/procurement -o procurement-0.3.0.tar.gz   # same bytes every time
```
