# certificates plugin

Watch the TLS certificates an organisation depends on with a Tranibor agent: what expires, what is expired or
broken, why a renewal is stuck, and which certificates nobody manages. This plugin holds what does not depend
on the source of the certificates: the **profile** every certificates server must serve, the agent, the skill,
the template, the evals and the conformance suite. A provider plugin brings the server:
[`certificates-probe`](../certificates-probe/README.md) (what endpoints serve),
[`certificates-cert-manager`](../certificates-cert-manager/README.md) (what cert-manager renews in a cluster),
[`certificates-ct`](../certificates-ct/README.md) (what the public CAs logged for your domains).

**Read-only in this version.** No tool of the profile writes: the agent proposes every fix as text for a
person to apply, and no server of the profile declares a write operation.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `certificates` 0.1.0, `contents.profiles`; core `>=0.30.0,<1.0.0` is a placeholder (see below) |
| Profile | [`profiles/certificates.yaml`](profiles/certificates.yaml) | `certificates/certificates@1`: 4 read tools and their schemas |
| Agent | [`agents/cert-watch.yaml`](agents/cert-watch.yaml) | names no provider; reads every instance, `get_provider` first |
| Skill | [`skills/certificate-renewal-triage`](skills/certificate-renewal-triage/SKILL.md) | following the renewal chain, HTTP-01, DNS-01, rate limits, what a probe adds |
| Template | [`templates/cert-review`](templates/cert-review/README.md) | schedule, review (agent), read (platform team), done |
| Evals | [`evals/cert-watch.yaml`](evals/cert-watch.yaml) | instruction checks, and live cases on the providers' fakes |
| Conformance | [`conformance/`](conformance) | pytest suite a server runs against itself |

## The profile

ADR 0224: a profile is a contract of tools. A server that `implements: [certificates/certificates@1]` serves
all of them, with a schema compatible with the profile's. The platform names an organisation's instances
`<instance>__<tool>`; the agent does not know which source is behind one, and calls `get_provider` on every
instance first because the sources do not see the same things.

| Tool | Access | What it answers |
|:--|:--|:--|
| `get_provider` | read | the source, its kind (`endpoint`, `manager`, `log`), its scope and what it can and cannot see |
| `list_certificates` | read | the inventory, soonest expiry first; `within_days`, `name`, `limit`; what could not be read is in `unavailable` |
| `get_certificate` | read | one certificate by id, with the renewal attempts and notes on what the source cannot tell |
| `list_findings` | read | expired, expiring, renewal failing or overdue, not trusted, chain incomplete, name not covered, unreachable; each with its evidence |

Certificate model: `{id, names, issuer, not_before, not_after, days_remaining, renewal, ...}`; `id` is opaque
and belongs to the source. A renewal is `{managed, manager, state}` with a state of `ok`, `due`, `pending`,
`failing` or `unknown`; a source that cannot see renewal says `managed: false, state: unknown`. A field a
source does not know is omitted, never null.

Left out on purpose: any write (issuing, renewing, revoking), private keys.
`expiry_report` of the earlier `cert-watch` plugin is replaced by `list_findings`; "undeclared certificate"
detection is the agent's, across instances: a name the log shows that no probe serves and no manager holds.

## Conformance

[`conformance/`](conformance) is a package (`tranibor-certificates-conformance`). A server's tests subclass
`Conformance`, give it a `certificates_target` fixture and inherit the checks: every tool served and declared in
`operations`, read-only, schemas, ordering and window, refusal of what is not configured before any upstream
call, a source that fails reported rather than raised. Each provider plugin declares it as a path
dev-dependency (`[tool.uv.sources]`, editable) of its `server/pyproject.toml`; the image does not install it.
See [`conformance/README.md`](conformance/README.md).

## Not proven yet

`validate` and `pack` need a core that knows `contents.profiles`, `implements: <profile>` and
`mcp_servers[].profile` (core stories S86-01/S86-02). The core floor in `plugin.yaml` is a placeholder until a
release carries them, and the evals' live cases have never run. No server was run against a real cluster or a
real endpoint.
