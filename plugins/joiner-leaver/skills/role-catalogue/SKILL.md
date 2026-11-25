---
name: role-catalogue
description: How an organisation describes role to Keycloak groups, sensitivity, approvers and incompatibilities, as a knowledge base the agent reads; with a sample table to replace. Use when resolving which groups a role gets or loses.
---

# Role catalogue

**Replace the tables below with your organisation's**: publish a new version of this skill in the
organisation (a project born later never rewrites it). The agent reads it; it never invents a group, an
id or an approver that is not here, and it confirms in Keycloak that each group exists.

## Format

One row per **role**. A role is what a person is hired for or moved to; the team is context, not a key.

| Column | Meaning |
|:--|:--|
| Role | the name a request uses (case does not matter); the catalogue's spelling is what is planned |
| Groups | the Keycloak group paths the role holds, each with its id between parentheses (a UUID: the workflow refuses anything else) |
| Sensitive | the groups among them that are sensitive (privileged, financial, personal data); each waits for its own stricter approvers |
| Approvers | the suffix of the identity-provider group whose members approve the writes: `engineering` means `jml-approvers-engineering`; a sensitive group names its own |
| Incompatible | pairs of groups no one may hold together (segregation of duties) |

**Account lifecycle** (create, invitation, disable, sign out) is approved by one fixed suffix, written in
the last section: it is not a role's.

## Roles

| Role | Groups (id) | Sensitive | Approvers |
|:--|:--|:--|:--|
| Software engineer | `/engineering/developers` (`00000000-0000-0000-0000-0000000000a1`), `/tools/wiki` (`00000000-0000-0000-0000-0000000000a2`) | none | `engineering` |
| Platform engineer | `/engineering/developers` (`00000000-0000-0000-0000-0000000000a1`), `/engineering/platform` (`00000000-0000-0000-0000-0000000000a3`), `/tools/wiki` (`00000000-0000-0000-0000-0000000000a2`) | `/engineering/platform` | `engineering`; `platform-leads` for the sensitive one |
| Accountant | `/finance/ledger-read` (`00000000-0000-0000-0000-0000000000b1`), `/tools/wiki` (`00000000-0000-0000-0000-0000000000a2`) | none | `finance` |
| Finance approver | `/finance/ledger-read` (`00000000-0000-0000-0000-0000000000b1`), `/finance/payments-approve` (`00000000-0000-0000-0000-0000000000b2`), `/tools/wiki` (`00000000-0000-0000-0000-0000000000a2`) | `/finance/payments-approve` | `finance`; `finance-leads` for the sensitive one |
| Support agent | `/support/agents` (`00000000-0000-0000-0000-0000000000c1`), `/tools/wiki` (`00000000-0000-0000-0000-0000000000a2`) | none | `support` |

## Incompatible groups

| Group | Never together with | Why |
|:--|:--|:--|
| `/finance/payments-approve` | `/finance/payments-create` (`00000000-0000-0000-0000-0000000000b3`) | whoever creates a payment does not approve it |
| `/engineering/platform` | `/finance/payments-approve` | production operations and payment approval |

## Account lifecycle

Approvers of the account's creation, invitation, disabling and sign-out: `it-identity`.

## Rules

- A role missing from this table is never planned: the request is `refused`, and the summary names the
  role asked.
- A group listed here that Keycloak does not have is never planned: say so, a person fixes the
  catalogue.
- A group in both the previous and the new role is neither removed nor added.
- Groups that are not here and that the person holds (a project, an exception) are reported and left to a
  person.
- Keep it short: a role with thirty groups is two roles.
