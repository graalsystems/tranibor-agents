---
id: connector.mcp.certificates-cert-manager
title: Connect certificates-cert-manager to a Kubernetes cluster
applies_to: {connector: mcp/certificates-cert-manager}
audience: org_admin
version: 1
reviewed: 2026-11-26
vendor_docs:
  - {title: "cert-manager API reference", url: "https://cert-manager.io/docs/reference/api-docs/"}
  - {title: "cert-manager: troubleshooting ACME (Orders and Challenges)", url: "https://cert-manager.io/docs/troubleshooting/acme/"}
  - {title: "Kubernetes: using RBAC authorization", url: "https://kubernetes.io/docs/reference/access-authn-authz/rbac/"}
  - {title: "kubectl create token", url: "https://kubernetes.io/docs/reference/kubectl/generated/kubectl_create/kubectl_create_token/"}
  - {title: "Kubernetes: managing service accounts", url: "https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/"}
  - {title: "Kubernetes: API concepts (list, limit and continue)", url: "https://kubernetes.io/docs/reference/using-api/api-concepts/"}
egress: ["<kubernetes-api-host>"]
values: []
fields:
  kube_api_url: "https://<kubernetes-api-host>:6443, the API server's URL as the platform reaches it, without a trailing slash"
  kube_ca_cert: "the PEM text of the CA that signed the API server's certificate; empty when it chains to public roots"
  kube_token: "the bearer token of the read-only service account, typed once"
  namespaces: "optional: the namespaces to read, comma separated; empty reads every namespace the token may list"
---

## Before you start

- A Kubernetes cluster running cert-manager, and `kubectl` with rights to create a service account, a
  role and a binding (cluster-admin or equivalent).
- The `certificates-cert-manager` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- The API server must be reachable from where the platform runs the server. **A public API endpoint works
  now. A cluster API on a private network does not**: it needs the private-network decision of ADR 0224
  section 6 (private CIDRs an administrator declares, or an outpost inside the network), which is not
  available yet. Do not tunnel around it.
- What the server reaches, and nothing else: the Kubernetes API (`kube_api_url`, the descriptor's `egress`).
- This plugin was written against the documentation listed above and tested against a seeded fake only. No
  real cluster was used: see "Test it".

## At the cluster

The service account only reads: `get`, `list` and `watch` on the six cert-manager kinds, nothing else (no
Secret, no write verb, no core resource). The server itself uses `get` and `list`; `watch` is granted
because the Kubernetes documentation treats the three read verbs together, and is never used.

1. **The service account.**

   ```bash
   kubectl create namespace certificates
   kubectl -n certificates create serviceaccount certificates
   ```

2. **Cluster-wide variant** (every namespace; the simplest):

   ```yaml
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRole
   metadata:
     name: certificates-read
   rules:
     - apiGroups: ["cert-manager.io"]
       resources: ["certificates", "certificaterequests", "issuers", "clusterissuers"]
       verbs: ["get", "list", "watch"]
     - apiGroups: ["acme.cert-manager.io"]
       resources: ["orders", "challenges"]
       verbs: ["get", "list", "watch"]
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRoleBinding
   metadata:
     name: certificates-read
   roleRef:
     apiGroup: rbac.authorization.k8s.io
     kind: ClusterRole
     name: certificates-read
   subjects:
     - kind: ServiceAccount
       name: certificates
       namespace: certificates
   ```

3. **Namespaced variant** (only the namespaces you list in `namespaces`; repeat the Role and RoleBinding
   in each). `clusterissuers` is cluster-scoped and cannot be granted in a Role: without the small
   ClusterRole at the end, the issuer of a Certificate is reported as "not readable" and everything else
   still works.

   ```yaml
   apiVersion: rbac.authorization.k8s.io/v1
   kind: Role
   metadata:
     name: certificates-read
     namespace: shop            # one per namespace in `namespaces`
   rules:
     - apiGroups: ["cert-manager.io"]
       resources: ["certificates", "certificaterequests", "issuers"]
       verbs: ["get", "list", "watch"]
     - apiGroups: ["acme.cert-manager.io"]
       resources: ["orders", "challenges"]
       verbs: ["get", "list", "watch"]
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: RoleBinding
   metadata:
     name: certificates-read
     namespace: shop
   roleRef:
     apiGroup: rbac.authorization.k8s.io
     kind: Role
     name: certificates-read
   subjects:
     - kind: ServiceAccount
       name: certificates
       namespace: certificates
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRole
   metadata:
     name: certificates-read-clusterissuers
   rules:
     - apiGroups: ["cert-manager.io"]
       resources: ["clusterissuers"]
       verbs: ["get", "list", "watch"]
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRoleBinding
   metadata:
     name: certificates-read-clusterissuers
   roleRef:
     apiGroup: rbac.authorization.k8s.io
     kind: ClusterRole
     name: certificates-read-clusterissuers
   subjects:
     - kind: ServiceAccount
       name: certificates
       namespace: certificates
   ```

4. **Check the rights before going further** (each must say `yes`, and the last must say `no`):

   ```bash
   SA=system:serviceaccount:certificates:certificates
   kubectl auth can-i list certificates.cert-manager.io --all-namespaces --as=$SA
   kubectl auth can-i list challenges.acme.cert-manager.io --all-namespaces --as=$SA
   kubectl auth can-i get secrets --all-namespaces --as=$SA   # must be "no"
   ```

5. **The token.** A bounded token is the safe default; the API server may cap its duration, and the
   plugin never renews it: put a reminder before it expires, create a new one and update `kube_token`.

   ```bash
   kubectl -n certificates create token certificates --duration=2160h
   ```

   `kubectl create token` prints the token once; it is not stored in the cluster, and it is bound to the
   service account (delete the service account and it stops working). A token that does not expire is a
   `Secret` of type `kubernetes.io/service-account-token` (Kubernetes, "Manually create a long-lived API
   token for a ServiceAccount"): it works until the Secret is deleted, which is why a bounded one is
   preferred here.

6. **The API server's CA**, when its certificate does not chain to public roots (most clusters):

   ```bash
   kubectl -n kube-system get configmap kube-root-ca.crt -o jsonpath='{.data.ca\.crt}'
   ```

   Paste the whole PEM text (from `-----BEGIN CERTIFICATE-----` to `-----END CERTIFICATE-----`) into
   `kube_ca_cert`. That ConfigMap holds the CA the in-cluster clients use; if your API server is reached
   through a load balancer with another certificate, use that certificate's CA instead.

7. **The API server's URL**: `kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'`
   gives the one your kubeconfig uses; it must be the address the platform can reach.

## Permissions

| What | Where | Why |
|:--|:--|:--|
| `get`, `list`, `watch` on `certificates`, `certificaterequests`, `issuers`, `clusterissuers` (`cert-manager.io`) | the cluster, or the listed namespaces | expiry, readiness, renewal time, the issuer's state |
| `get`, `list`, `watch` on `orders`, `challenges` (`acme.cert-manager.io`) | the same | why an issuance is stuck |
| nothing on `secrets`, no `create`, `update`, `patch`, `delete` | | the server cannot read a key or change anything |

## In Tranibor

Open **Connectors**, add the plugin's `certificates-cert-manager` connector and fill the fields above. The
token is sealed and never shown again. Then install the `cert-review` template of the `certificates` plugin
in a project and add the schedule its README gives.

## Test it

1. Press **Test** on the connector, then ask the agent for `list_certificates`: it lists your Certificates. A
   `403` names the rule that is missing (step 4 of the cluster part checks it).
2. `get_provider` shows the namespaces you allowed (or every namespace), and `list_certificates` shows no
   others.
3. `get_certificate` on a Certificate that is renewing lists the request, order and challenge it waits on.

What this page does not confirm:

- No run against a real cluster. The live tests under `server/tests/live` exist and skip without
  `TRANIBOR_LIVE_CERTIFICATES_CERT_MANAGER_*` variables.
- The longest token the API server will mint is its own setting (`--service-account-max-token-expiration`);
  this page does not confirm a default.
- Reading the `Event` objects that explain a stuck issuance is deliberately not done: it needs a rule on a
  core resource, and the resources' own conditions carry the failure text.

## When it fails

| You see | What it means | What to do |
|:--|:--|:--|
| `the Kubernetes API refused the token (401)` | the token expired, or the service account was deleted | create a new one (step 5) and update `kube_token` |
| `... (403): the service account lacks get/list` | a rule of step 2 or 3 is missing | run the `can-i` checks of step 4 |
| `the Kubernetes API did not answer` | the platform cannot reach `kube_api_url`, or its CA is wrong | check the URL, the CA, the network path |
| `is not in the configured allow-list` | the namespace is not in `namespaces` | add it, or ask for another |
