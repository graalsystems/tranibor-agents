# SPDX-License-Identifier: Apache-2.0
"""A read-only client of the Kubernetes API, for the cert-manager resources and nothing else.

Only `GET` is ever sent, and only to the six cert-manager collections below (RESOURCES). The path scheme is
Kubernetes' own: `/apis/<group>/<version>/[namespaces/<ns>/]<plural>[/<name>]`, with `limit` and `continue`
for pagination (https://kubernetes.io/docs/reference/using-api/api-concepts/#retrieving-large-results-sets-in-chunks)
and `Authorization: Bearer <service account token>` (https://kubernetes.io/docs/reference/access-authn-authz/authentication/).
The API server's certificate is checked against `kube_ca_cert` when given, else against the system roots.

Not confirmed in the docs and therefore not done: reading core `Event` objects (they would need a second
RBAC rule on a core resource; the resources' own conditions and `reason` fields carry the failure text).
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .errors import Refused, SourceError

PAGE_SIZE = 200
MAX_ITEMS = 2000
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


@dataclass(frozen=True)
class Resource:
    group: str
    version: str
    plural: str
    kind: str
    namespaced: bool = True

    def path(self, namespace: str | None = None, name: str | None = None) -> str:
        scope = f"/namespaces/{namespace}" if self.namespaced and namespace else ""
        suffix = f"/{name}" if name else ""
        return f"/apis/{self.group}/{self.version}{scope}/{self.plural}{suffix}"


CERTIFICATES = Resource("cert-manager.io", "v1", "certificates", "Certificate")
CERTIFICATE_REQUESTS = Resource("cert-manager.io", "v1", "certificaterequests", "CertificateRequest")
ISSUERS = Resource("cert-manager.io", "v1", "issuers", "Issuer")
CLUSTER_ISSUERS = Resource("cert-manager.io", "v1", "clusterissuers", "ClusterIssuer", namespaced=False)
ORDERS = Resource("acme.cert-manager.io", "v1", "orders", "Order")
CHALLENGES = Resource("acme.cert-manager.io", "v1", "challenges", "Challenge")
#: The whole surface: nothing outside this tuple is ever requested.
RESOURCES = (CERTIFICATES, CERTIFICATE_REQUESTS, ISSUERS, CLUSTER_ISSUERS, ORDERS, CHALLENGES)


def ssl_context(ca_pem: str | None) -> ssl.SSLContext | bool:
    if not ca_pem:
        return True
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=ca_pem)
    return context


class KubeClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        token = settings.kube_token.get_secret_value() if settings.kube_token else ""
        self._http = httpx.AsyncClient(
            base_url=settings.kube_api_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT,
            transport=transport,
            verify=ssl_context(settings.kube_ca_cert) if transport is None else True,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def check_namespace(self, namespace: str | None) -> None:
        allowed = self._settings.namespaces
        if namespace and allowed and namespace not in allowed:
            raise Refused(f"namespace {namespace!r} is not in the configured allow-list")

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            response = await self._http.get(path, params=params)
        except httpx.HTTPError as error:
            raise SourceError(f"the Kubernetes API did not answer: {type(error).__name__}") from error
        if response.status_code == 403:
            raise SourceError(
                f"the Kubernetes API refused {path} (403): the service account lacks get/list on that resource", 403
            )
        if response.status_code == 401:
            raise SourceError("the Kubernetes API refused the token (401): expired or revoked", 401)
        if response.status_code == 404:
            raise SourceError(f"not found: {path}", 404)
        if response.status_code >= 400:
            raise SourceError(f"the Kubernetes API answered {response.status_code} for {path}", response.status_code)
        try:
            body = response.json()
        except ValueError as error:
            raise SourceError(f"the Kubernetes API answered something that is not JSON for {path}") from error
        if not isinstance(body, dict):
            raise SourceError(f"unexpected answer shape for {path}")
        return body

    async def _list_one(self, resource: Resource, namespace: str | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = ""
        while True:
            params = {"limit": str(PAGE_SIZE)}
            if token:
                params["continue"] = token
            body = await self._get(resource.path(namespace), params)
            for item in body.get("items") or []:
                if isinstance(item, dict):
                    item.setdefault("kind", resource.kind)
                    items.append(item)
            metadata = body.get("metadata")
            token = str(metadata.get("continue") or "") if isinstance(metadata, dict) else ""
            if not token or len(items) >= MAX_ITEMS:
                return items[:MAX_ITEMS]

    async def list(self, resource: Resource, namespace: str | None = None) -> list[dict[str, Any]]:
        """Every object of a collection the configuration allows: one request per allowed namespace, or one
        cluster-wide request when no allow-list is set (and always for a cluster-scoped kind)."""
        self.check_namespace(namespace)
        if not resource.namespaced:
            return await self._list_one(resource, None)
        if namespace:
            return await self._list_one(resource, namespace)
        allowed = self._settings.namespaces
        if not allowed:
            return await self._list_one(resource, None)
        found: list[dict[str, Any]] = []
        for ns in allowed:
            found.extend(await self._list_one(resource, ns))
        return found

    async def get(self, resource: Resource, name: str, namespace: str | None) -> dict[str, Any]:
        self.check_namespace(namespace)
        if resource.namespaced and not namespace:
            raise Refused(f"a {resource.kind} is read by namespace and name")
        return await self._get(resource.path(namespace, name))
