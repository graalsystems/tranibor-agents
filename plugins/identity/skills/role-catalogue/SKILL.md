---
name: role-catalogue
description: How an organisation describes which groups a role holds, which are sensitive, who approves and which are incompatible, as a knowledge base the agent reads; with a sample table to replace. Use when resolving which groups a role gets.
---

# Role catalogue

**Replace the tables below with your organisation's**: publish a new version of this skill in the
organisation. The agent reads it; it never invents a group or an approver that is not here, and it confirms in
the directory (`list_groups`) that each group exists, taking the id the directory gives.

## Roles

| Role | Groups (path) | Sensitive | Approvers |
|:--|:--|:--|:--|
| Software engineer | `/staff/engineering`, `/tools/wiki` | none | `engineering` |
| Accountant | `/staff/finance`, `/tools/wiki` | none | `finance` |
| Finance approver | `/staff/finance`, `/finance/payments-approve`, `/tools/wiki` | `/finance/payments-approve` | `finance`; `finance-leads` for the sensitive one |
| Support agent | `/staff/support`, `/tools/wiki` | none | `support` |

A role is what a person is hired for; the team is context, not a key. A role missing here is not planned.

## Incompatible groups

| Group | Never together with | Why |
|:--|:--|:--|
| `/finance/payments-approve` | `/finance/payments-create` | whoever creates a payment does not approve it |

## Account lifecycle

Approvers of creating an account, sending the setup mail, disabling and signing out: `it-identity`.

## Rules

- A group listed here that the directory does not have is not planned: say so, a person fixes the catalogue.
- A group the person holds that is not here (a project, an exception) is reported and left to a person: you
  never remove what you cannot explain.
- Keep it short: a role with thirty groups is two roles.
