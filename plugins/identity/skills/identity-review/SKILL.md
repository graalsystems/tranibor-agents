---
name: identity-review
description: How to review who has access in a directory — dormant accounts, accounts without a second factor, administrators, group drift — what each signal proves and what it does not, and which change is safe to propose. Use when auditing a directory or judging a finding.
---

# Reviewing access

## 1. A signal is a candidate, never a verdict

| Signal | What it shows | What it cannot show |
|:--|:--|:--|
| Dormant (`dormant_users`) | no sign-in event kept for the window | that the person left; a service account that never signs in; a sign-in older than the retention |
| No second factor (`users_without_mfa`) | no OTP or passkey registered | that the directory does not enforce one elsewhere (a federated login does its own) |
| Administrator (`list_privileged_users`) | the privileges the tool reads, and how each is held | privileges it does not read: its `scope` field lists them, quote it |
| No group (`users_without_group`) | the account belongs to no group | that it needs one |
| Drift (`compare_group_members`) | who differs from the list you were given | which side is right: the list may be out of date |

Say each one as a candidate and name the person to ask (the manager, the owner of the group). Never write
"unused" or "unneeded" from a signal alone.

## 2. Dormancy needs evidence that it can be known

`dormant_users` says `reliable: false` when the directory keeps no sign-in events, or keeps them for less
than the window. Then there is nothing to conclude: say so, and say what to enable (event storage and a
retention at least as long as the window). An account created inside the window is young, not dormant. An
account with an open session is not dormant.

## 3. Administrators

List who holds which privilege and how (direct, or through a group). Many administrators, an administrator
without a second factor, and an administrator account that is also dormant are the findings that matter; the
last two are the most urgent. Do not propose removing a privilege: this profile has no tool for it. Propose
a person reviews it.

## 4. What is safe to propose

- Disable a dormant account **only** after its owner confirmed: disabling is reversible (`enable_user`),
  deleting is not and is never proposed.
- Sign a person out (`logout_user`) after disabling, not instead.
- Send a setup mail (`send_setup_email`) to a person without a second factor, with the action that enrols
  one. It needs the directory to have mail configured (`get_directory`).
- Remove a membership only when a named owner says the list is right.

## 5. Order

Read the directory's state, read each signal, cross them (an administrator who is dormant), then propose,
smallest first. Every write is a governed action a person approves; none is applied by the call itself.
