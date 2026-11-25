---
name: jml-checklist
description: The method for a joiner, a mover and a leaver — what to read, what to plan in which order, the segregation of duties, the four-eyes rule and the manual tasks no connector covers. Use when planning or verifying a person's arrival, role change or departure.
---

# Joiner, mover, leaver

A person's access changes three ways. The method is the same: **read the state, plan the smallest
change that gets from it to the role's, order it so access is never wider than intended, have every
write approved by someone who is not the person nor who asked, read it back.**

## 1. Before anything: who authorised it

A request carries: the person (name, username), the **date** (start, move or last day), the **role**
and **team**, the **manager** and an **approver** — the person with authority who asked for it or
confirmed it. No approver, no plan: ask for one, never assume the requester is it. The approver is never
the person concerned. A request that arrives only in the person's own name (a joiner asking for their
own access, a leaver extending their own) is refused.

## 2. Read the current state

Always from the identity provider, never from the request's words: does the account exist, is it
enabled, its groups, its sessions. A request that says "new joiner" for an account that exists, or "move"
for an account that does not, is wrong: say so and stop; a person decides (a rehire, a typo, a duplicate).

## 3. Plan, by kind

| Kind | Plan | Order |
|:--|:--|:--|
| Joiner | the account; the groups of the role; the e-mail that asks the person to set a password and a second factor | account, groups, invitation |
| Mover | the groups of the previous role that the new one does not keep, **removed**; the groups the new role needs, **added** | removals, then additions |
| Leaver | the account **disabled**, its sessions **ended**, each group **removed**, all on the end date | removals, disable, sessions |

- **Never delete.** A leaver's account is disabled: its history, its ownership and the audit trail
  stay. Deleting an account is a person's decision, later, and outside this method.
- **The dates are the request's.** A leaver is cut on the last day, not before, not "to be safe" the day
  before; a joiner's access starts on the start day; a mover's old access ends on the move date.
- **Remove before you add** for a mover: at no moment does the person hold both roles' access.
- **Smallest change.** A group the role catalogue does not give to either role, that the person has, is
  reported and left to a person: it may be a project, an exception someone granted for a reason.
- **One write, one entry.** One group, one entry. Each is approved on its own, by the approvers the
  catalogue names for it, so a manager can accept the role's ordinary groups and a security lead decide
  the sensitive one.

## 4. Segregation of duties

- A role never ends with two groups the catalogue declares incompatible (for instance the one that
  raises a payment and the one that approves it; the one that deploys and the one that approves the
  deployment). The check is on the **result**: what the person will hold once the plan is applied,
  including the groups they keep. A plan that would break it is refused for that entry, with the pair
  named.
- Nobody approves a write about themselves, and nobody approves what they asked for: the platform
  excludes the person concerned, the requester and the manager from approving each write, whatever their
  rank. Do not plan around it.

## 5. The four-eyes rule

Two different people before the first write: the person's **manager** and **IT** approve the plan; then,
for each write, the approvers of that group decide it, signed in again. The agent never counts as one of
the people. A sensitive group is approved by its own, stricter group of approvers.

## 6. What a person must do by hand (always list it)

No connector covers these yet. Say so in the plan, every time, so that nobody believes they are done:

- **Leaver**: return and wipe the hardware (laptop, phone, badge, tokens); the shared mailboxes,
  calendars and distribution lists; transfer the ownership of what the person owns (repositories,
  documents, dashboards, on-call rotations, open tickets, customer accounts); the accounts of systems
  that are not connected (the GitHub organisation, licences, SaaS tools, shared passwords to rotate);
  the forwarding of mail and the out-of-office text; the final exit checklist.
- **Joiner**: the hardware to hand over; the licences; the systems that are not connected; the
  introductions (manager, buddy, first-week calendar).
- **Mover**: access to systems that are not connected, on both sides; the transfer of what the person
  owned in the previous role.

## 7. Read it back

After the writes, read the identity provider again and compare it with the plan, entry by entry. A write
that was refused or abandoned is **not done**: say so, never round it up to "done". The work item is
closed by a person once the manual tasks are done or their absence recorded.
