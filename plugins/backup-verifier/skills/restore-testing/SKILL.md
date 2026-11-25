---
name: restore-testing
description: How to prove a Kubernetes backup restores - RPO and RTO, what a test restore proves and what it does not, how to read a Velero backup and restore, and how to clean up. Use when verifying backups, judging a restore test, or explaining a Red verdict.
---

# Testing that a backup restores

## 1. Why test the restore

A backup that has never been restored is an assumption. Backups fail quietly: an expired bucket key, a
schedule paused during an incident and never resumed, a volume snapshot that timed out, a location that
became read-only. Only a restore shows the data comes back.

## 2. Two numbers decide what "good" means

- **RPO** (recovery point objective): how much recent data you can lose. A daily schedule has an RPO of
  about 24 hours; the newest good backup must be younger than the schedule's period plus a margin for the run
  itself. A backup counts from when it **started**, because that is the instant its data represents.
- **RTO** (recovery time objective): how long a restore may take. A test restore gives a measured duration
  (the restore's start and completion). Compare it with the target; a restore that takes ten times longer
  than the target is a finding even when it succeeds.

## 3. What a Velero test restore proves

Restoring ONE namespace of the newest Completed backup into a throw-away namespace proves that:

- the backup's objects can be read back from the bucket (storage and credentials work);
- the Kubernetes resources recreate without errors (`errors: 0`) and something came back
  (`itemsRestored > 0`);
- the restore completes within a measured time.

## 4. What it does not prove

State this in every report; never let Green mean more.

| Not proven | Why |
|:--|:--|
| The application works | Resources exist; pods may crash on missing secrets, configuration or dependencies |
| Volume data is intact | Volumes are restored only on request, and a file-system or snapshot restore needs its own content check |
| Other namespaces and cluster-scoped objects | One namespace is restored; cluster resources (CRDs, cluster roles, storage classes) are not |
| Every object came back | Counts compare only when the restore covers exactly what the backup named; a one-namespace restore of a larger backup is not comparable |
| The restore works in a disaster | A test uses a healthy cluster, the same network and the same credentials |
| Backups older than the one tested | Each night tests the newest; an old restore point may still be unreadable |

## 5. Reading Velero's objects

- **Backup** `status.phase`: `Completed` (all good), `PartiallyFailed` (some items failed: read `errors` and
  the backup's log), `Failed`, `FailedValidation`, `InProgress`. Only Completed counts as fresh.
- **Schedule**: `spec.schedule` (cron), `spec.paused`, `status.lastBackup`. A paused schedule produces no
  backups on purpose or by accident: report it.
- **BackupStorageLocation** `status.phase`: `Available` or `Unavailable`; its `message` names the cause.
- **Restore** `status.phase`, `errors`, `warnings`, and `progress` (items restored of total). Warnings are
  common (resources that already exist); errors are not.

## 6. Plausible, not exact

- When the restore covers the same namespaces as the backup and excludes nothing, restored items should be
  close to backed-up items: at least 0.9.
- Otherwise only check that the restore completed, without errors, with a non-zero count.
- A count of 0 on a Completed restore is a Red: nothing was restored.

## 7. Safety and cleanup

- Restore only into a namespace dedicated to tests, named with a fixed prefix. Never into the source namespace
  and never over a live one: a restore into the namespace of origin overwrites what runs there.
- Disable what a restored application would do to the outside world before it starts: schedules that send
  mail, webhooks, jobs that write to shared stores. Exclude those resources when you restore, and say so.
- Always delete the test namespace afterwards, pass or fail, and say that you did. A leftover namespace is
  cost and a security surface.
- A restore test is itself an operation on the cluster: it consumes capacity, so run it when load is low.

## 8. Writing a Red

Name the failed check, quote the evidence (object names, phases, counts, ages, the location's message), give
the likely cause as a hypothesis, and propose the fix as text. Never describe a fix as done.
