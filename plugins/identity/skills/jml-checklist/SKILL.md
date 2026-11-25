---
name: jml-checklist
description: The method for onboarding and offboarding a person's access — what a request must say, the order of the writes, segregation of duties, who approves, and what only a person can do. Use when planning a joiner or a leaver.
---

# Joiner and leaver checklist

## 1. A request is complete or it is not planned

A joiner needs: the username, the e-mail, the first and last name, the role, the manager, the start date and
an **approver** who is neither the person nor, when the requester is the person, the requester. A leaver
needs: the username (or the id), the last day and an approver. A request without an approver is not planned,
whatever its urgency: say that an approver is needed.

## 2. Read before you plan

Always read the directory first: does the account exist (disabled or not)? which groups does the person
hold? are there open sessions? The profile's `plan_joiner` and `plan_leaver` do exactly this reading and
return the calls to propose, in order, with the warnings. Use their output, not your own reckoning, and
never propose a plan whose `valid` is false: report its errors.

## 3. Order of the writes

- Joiner: create the account (no password is ever set by the agent), add the groups the role gives, then
  send the setup mail (the person sets a password and a second factor). An account that already exists is a
  person's decision: stop.
- Leaver: disable first, then end the sessions, then remove the groups. Disabling before the sign-out stops
  a new session; the sign-out ends the open ones. Never delete: the profile has no delete, and an account
  kept disabled keeps its history.
- A write is applied no earlier than the effective date.

## 4. Segregation of duties

A person never ends with two groups the role catalogue declares incompatible. If a plan would, plan
neither and say why.

## 5. What only a person can do

Say, every time, what no connected system covers: the hardware, shared mailboxes and calendars, the transfer
of ownership (repositories, documents, on-call), accounts in systems that are not connected, licences.

## 6. Nothing the agent does is final

Every write is a governed action. The agent proposes; the approvers decide each one; a refused action is
reported as not done, never retried.
