---
name: cert-manager-troubleshooting
description: How to read cert-manager resources and troubleshoot ACME certificate issuance - the Certificate, CertificateRequest, Order, Challenge chain, HTTP-01 and DNS-01 failures, CA rate limits, and how to tell an undeclared certificate from a mis-issuance. Use when a certificate is not renewing or an expiry report needs explaining.
---

# Troubleshooting cert-manager and ACME

## 1. Follow the chain

cert-manager issues in a fixed chain, each object owned by the one before (`ownerReferences`):

`Certificate` -> `CertificateRequest` -> `Order` -> `Challenge` (one per name, for ACME issuers).

Read from the top and stop at the first object that says what is wrong:

| Object | Where the cause shows | A common reading |
|:--|:--|:--|
| Issuer / ClusterIssuer | `Ready` condition | Not Ready: every certificate it signs is stuck, whatever the challenge says (account not registered, bad server URL) |
| Certificate | `Ready`, `Issuing`; `status.notAfter`, `renewalTime`, `failedIssuanceAttempts`, `lastFailureTime` | `Issuing=True` for hours with a rising failure count: renewal is stuck below |
| CertificateRequest | `Approved`, `Denied`, `Ready`, `InvalidRequest` | `Denied` or `InvalidRequest`: a policy or a malformed request, before any ACME call |
| Order | `status.state` (pending, ready, processing, valid, invalid, expired, errored), `status.reason` | `errored` with a CA message: read the message (rate limit, CAA, unauthorized) |
| Challenge | `spec.type`, `status.reason`, `status.presented`, `status.processing` | The reason is the self check's last result |

`renewalTime` in the past with a healthy `Ready=True` is the dangerous case: the certificate still works,
renewal does not, and the clock is running. Compare `renewalTime` with `notAfter`.

## 2. HTTP-01

cert-manager creates a solver pod, a service and (with an ingress solver) an ingress that serves
`/.well-known/acme-challenge/<token>`; it first checks that URL itself, then asks the CA to validate.

- `wrong status code '404'` or `did not get expected response`: the name reached something, but not the
  solver. The usual cause is DNS pointing at another ingress controller or load balancer than the solver's
  ingress class, or a catch-all rule ahead of the challenge path.
- `connection refused` / timeout: nothing answers on port 80 from where the check runs: firewall, load
  balancer without a port 80 listener, or an internal-only name.
- The self check runs from inside the cluster: a name that resolves differently inside (split-horizon DNS)
  or cannot loop back through the public address (NAT loopback) fails there while the CA could succeed.
- A global HTTP to HTTPS redirect is fine when it lets the challenge path through; a redirect to another host
  or an authentication wall is not.
- Wildcard names cannot use HTTP-01: they need DNS-01.

## 3. DNS-01

cert-manager writes a TXT record `_acme-challenge.<name>`, waits until it sees it, then asks the CA.

- `Waiting for DNS-01 challenge propagation: DNS record ... not yet propagated`: the check does not see the
  record yet. Wait a few minutes first; if it persists, suspect an internal view of the zone, a delegated
  zone the solver is not configured for, or a CNAME on `_acme-challenge`.
- A provider error in the reason (`AccessDenied`, `403`, `401`) is the solver's credential: rights on the
  zone, not propagation.
- Records left behind from earlier attempts can confuse a check; they are removed by the solver on success.

## 4. Rate limits

A CA limits new orders per set of names per week and failed validations per hour. The reason says
`rateLimited` or `too many ...`. Retrying makes it worse. Fix the cause of the failures first, test the
fix on the CA's staging endpoint, and wait out the window; do not delete and recreate the Certificate to
"reset" it (it creates a new order against the same limit).

## 5. What the probe adds

The cluster says what cert-manager believes; a TLS probe says what clients get. Compare:

- `not_after` served vs the Certificate's `notAfter`: a renewed secret not picked up by the pod or the ingress.
- `chain_complete: false` or `trusted: false`: the intermediate is missing from what the server sends.
- `hostname_matches: false`: the host is served another certificate (default backend, wrong SNI route).
- Unreachable: nothing to compare; do not infer a certificate problem from it.

## 6. Certificate Transparency

Every publicly trusted certificate is logged. Entries for your domains whose names no Certificate declares
are a lead, not a verdict: manual issuance, another cluster or team, a CDN or SaaS provider, a former
owner, or a mis-issuance. An issuer the platform never uses and a recent date strengthen the case; an
expired entry rarely matters. Wildcards cover exactly one label: `*.example.com` covers `a.example.com`
and not `a.b.example.com`.

## 7. Proposing a fix

Smallest change that the evidence supports; as text or YAML, for a person to review. Name what it touches,
who should look at it, and what to check afterwards (the `Ready` condition, the next Order, a probe). Never
put a token, key or secret in a proposal.
