---
id: connector.mcp.velero
title: Connect a Kubernetes cluster running Velero
applies_to: {connector: mcp/velero}
audience: org_admin
version: 1
reviewed: 2026-11-25
vendor_docs:
  - {title: "Velero: how Velero works", url: "https://velero.io/docs/main/how-velero-works/"}
  - {title: "Velero: restore reference", url: "https://velero.io/docs/main/restore-reference/"}
  - {title: "Velero: backup reference", url: "https://velero.io/docs/main/backup-reference/"}
  - {title: "Velero: API types (Backup, Restore, Schedule, BackupStorageLocation)", url: "https://velero.io/docs/main/api-types/"}
  - {title: "Kubernetes: using RBAC authorization", url: "https://kubernetes.io/docs/reference/access-authn-authz/rbac/"}
  - {title: "Kubernetes: managing service accounts (long-lived and bound tokens)", url: "https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/"}
  - {title: "Kubernetes: validating admission policy", url: "https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/"}
  - {title: "Kubernetes API concepts: chunking with limit and continue", url: "https://kubernetes.io/docs/reference/using-api/api-concepts/#retrieving-large-results-sets-in-chunks"}
  - {title: "Kyverno: validate rules", url: "https://kyverno.io/docs/policy-types/cluster-policy/validate/"}
  - {title: "Amazon S3: ListObjectsV2", url: "https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html"}
  - {title: "Amazon S3: authenticating requests (Signature Version 4)", url: "https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-authenticating-requests.html"}
egress: ["<the Kubernetes API server host>", "<the S3 endpoint host, when a bucket is configured>"]
values: []
fields:
  kube_api_url: "https://kubernetes.example.com:6443, the API server, without a trailing slash"
  kube_ca_cert: "the cluster's CA certificate (PEM); leave empty when the API server's certificate chains to a public CA"
  kube_token: "the bearer token of the service account created below, typed once"
  velero_namespace: "the namespace Velero runs in (default velero)"
  restore_namespace_prefix: "the prefix every test namespace must start with, for instance restore-test- (a dash at the end, 5 to 30 characters)"
  s3_endpoint: "optional: the backup bucket's S3-compatible endpoint, https://s3.example.com, path-style"
  s3_bucket: "optional: the bucket's name"
  s3_region: "optional: the bucket's region (default us-east-1)"
  s3_prefix: "optional: the prefix Velero writes under, with a trailing slash (default empty)"
  s3_access_key: "optional: the access key of a read-only identity on the bucket, typed once"
  s3_secret_key: "optional: its secret key, typed once"
---

## Before you start

- A cluster where Velero runs, with at least one Schedule, and someone who can create a service account, a
  role and an admission policy on it.
- The `backup-verifier` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in
  the platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- The API server must be reachable from the platform, and you must allow its host (and the bucket's, if you
  configure one) as the connector's egress.
- Decide the **restore prefix**, for instance `restore-test-`. Every namespace the connector may create or
  delete starts with it. Pick one no real namespace uses.
- This procedure is written from the Velero, Kubernetes and Amazon S3 documentation above and the plugin's
  fake cluster. It has **not** been run against a live cluster or bucket. Not confirmed in the
  documentation, so read defensively by the server and to check on first run: the Restore's
  `status.progress` fields, a BackupStorageLocation's `status.phase` and `status.lastValidationTime`, and the
  `velero.io/schedule-name` and `velero.io/backup-name` labels.

### What the connector does, and where the guard is

It reads the `velero.io/v1` resources (Schedule, Backup, Restore, BackupStorageLocation) in Velero's
namespace, and it may list the bucket. It writes in two ways only: it creates a **Restore** that restores one
namespace of a backup into a new namespace, and it deletes a namespace it created.

**The prefix is enforced by the connector's server, not by Kubernetes.** Kubernetes RBAC cannot grant "create
namespaces whose name starts with `restore-test-`": a rule can name resources, verbs and exact
`resourceNames`, but it cannot match a name by prefix. So the service account below necessarily holds the
right to create and delete namespaces, and the server is what refuses everything else. It checks, before any
write, that the target namespace:

1. starts with the configured prefix;
2. is not a reserved namespace (`default`, `kube-system`, `kube-public`, `kube-node-lease`, the Velero
   namespace);
3. for a delete, carries the label `tranibor.io/test-restore=true` that the server put on it when it created
   it, so a namespace that happens to share the prefix is never deleted.

That is a control in the connector, so a second one in the cluster is worth having: an admission policy
(below) that applies the same rule to this service account, whatever the server does.

## At Kubernetes

1. **Create the service account** in a namespace of your choice, for instance `tranibor-system`:
   `kubectl create serviceaccount tranibor-backup-verifier -n tranibor-system`.
## Permissions

2. **Read access in Velero's namespace** (a Role bound there):

   ```yaml
   apiVersion: rbac.authorization.k8s.io/v1
   kind: Role
   metadata: {name: tranibor-backup-verifier, namespace: velero}
   rules:
     - apiGroups: ["velero.io"]
       resources: ["schedules", "backups", "restores", "backupstoragelocations"]
       verbs: ["get", "list"]
     # Creating a Restore is the one write in this namespace.
     - apiGroups: ["velero.io"]
       resources: ["restores"]
       verbs: ["create"]
   ```

3. **Namespace access, cluster-wide** (a ClusterRole, because namespaces are cluster-scoped). It needs
   `create` and `delete` on `namespaces` and cannot be narrowed by name prefix; this is why step 5 matters:

   ```yaml
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRole
   metadata: {name: tranibor-backup-verifier-namespaces}
   rules:
     - apiGroups: [""]
       resources: ["namespaces"]
       verbs: ["get", "list", "create", "delete"]
   ```

   Bind both to the service account (a RoleBinding in `velero`, a ClusterRoleBinding for the ClusterRole).
4. **Create a token.** For a quick test, `kubectl create token tranibor-backup-verifier -n tranibor-system
   --duration=24h`. For production, a time-bound token expires: use a long-lived token Secret of type
   `kubernetes.io/service-account-token` (see the service-accounts page above), keep it in your secret
   manager, and rotate it. Hand it to the platform administrator through the secret manager, never by mail
   or chat.
5. **Restrict the account to the prefix with an admission policy.** Kubernetes can express it as a
   ValidatingAdmissionPolicy (Kubernetes 1.30 and later, `admissionregistration.k8s.io/v1`):

   ```yaml
   apiVersion: admissionregistration.k8s.io/v1
   kind: ValidatingAdmissionPolicy
   metadata: {name: backup-verifier-namespace-prefix}
   spec:
     failurePolicy: Fail
     matchConstraints:
       resourceRules:
         - apiGroups: [""]
           apiVersions: ["v1"]
           operations: ["CREATE", "DELETE"]
           resources: ["namespaces"]
     validations:
       - expression: >-
           request.userInfo.username != "system:serviceaccount:tranibor-system:tranibor-backup-verifier"
           || (request.operation == "DELETE" ? oldObject.metadata.name : object.metadata.name).startsWith("restore-test-")
         message: "the backup verifier may only create or delete namespaces named restore-test-*"
   ---
   apiVersion: admissionregistration.k8s.io/v1
   kind: ValidatingAdmissionPolicyBinding
   metadata: {name: backup-verifier-namespace-prefix}
   spec:
     policyName: backup-verifier-namespace-prefix
     validationActions: ["Deny"]
   ```

   The same, as a Kyverno policy:

   ```yaml
   apiVersion: kyverno.io/v1
   kind: ClusterPolicy
   metadata: {name: backup-verifier-namespace-prefix}
   spec:
     validationFailureAction: Enforce
     rules:
       - name: only-prefixed-namespaces
         match:
           any:
             - resources: {kinds: ["Namespace"], operations: ["CREATE", "DELETE"]}
               subjects: [{kind: ServiceAccount, name: tranibor-backup-verifier, namespace: tranibor-system}]
         validate:
           message: "the backup verifier may only create or delete namespaces named restore-test-*"
           deny:
             conditions:
               all:
                 - key: "{{ request.object.metadata.name || request.oldObject.metadata.name }}"
                   operator: NotEquals
                   value: "restore-test-*"
   ```

   Both snippets are examples written from the documentation above and **not run on a cluster**: test them
   on a scratch cluster, and adapt the account name and the prefix to your own.
6. **Restoring needs Velero's own rights**, not this account's: Velero (with its own service account)
   creates the objects in the target namespace.

### At the bucket (optional)

The bucket listing is an extra check: it shows whether the newest backup's objects are in the bucket, which
the cluster's resources alone do not prove. Create a **read-only** identity on the backup bucket (list and
get on that bucket only, no write, no delete), and give its access key and secret key to the platform
administrator through your secret manager. The server signs a path-style ListObjectsV2 with Signature
Version 4. It was checked against the AWS signing test vector, not against a live bucket.

## In Tranibor

1. Catalogue, **Plugins**, install `backup-verifier` (a second person approves it).
2. Organisation, **Connectors**, configure the `velero` connector with the fields above. The token and the
   bucket keys are secrets: type them once.
3. Add the project's `schedules` entry from the template's README, and a group named `platform-owner`.
4. **Approval of the restore.** `create_test_restore` waits for a person's approval by default. After the
   first runs, an administrator may lower it to automatic if the admission policy of step 5 is in place.

## Test it

- Run the template once by hand with `velero_schedule` set to one schedule. The report should name that
  schedule, its backups and the storage location.
- Approve the restore. Look in the cluster for a namespace `<prefix><schedule namespace>-<date>` labelled
  `tranibor.io/test-restore=true`, and check it is deleted at the end of the run.
- Try, by hand, to create a namespace named outside the prefix with the service account's token: the
  admission policy must refuse it.
- If you want proof the bucket path works, give an object listing a prefix you know holds a backup.

## When it fails

| Symptom | Likely cause |
|:--|:--|
| `401` or `403` from the API server | the token expired or the Role/ClusterRole is not bound; check `kubectl auth can-i --as=system:serviceaccount:tranibor-system:tranibor-backup-verifier list backups.velero.io -n velero` |
| Every schedule Red, location `Unavailable` | Velero cannot reach the bucket: read the location's message in the report |
| A restore never finishes | Velero itself is unhealthy or the cluster lacks capacity: look at the Velero pod's log |
| `refused: namespace outside the prefix` | working as intended; the target did not start with the configured prefix |
| Test namespaces left behind | a run stopped before cleanup; delete those labelled `tranibor.io/test-restore=true` |
