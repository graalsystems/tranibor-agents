# SPDX-License-Identifier: Apache-2.0
"""The Kubernetes client: read-only, paths and pagination as the API defines them, allow-list enforced."""

from __future__ import annotations

import httpx
import pytest
import respx

from tranibor_cert_watch.config import Settings
from tranibor_cert_watch.errors import Refused, SourceError
from tranibor_cert_watch.fake import FakeKube
from tranibor_cert_watch.kube import CERTIFICATES, CHALLENGES, CLUSTER_ISSUERS, RESOURCES, KubeClient

from .conftest import NOW

API = "https://kube.example.com:6443"


def settings(**extra: str) -> Settings:
    return Settings.from_env(
        {
            "CERT_WATCH_KUBE_API_URL": API,
            "CERT_WATCH_KUBE_TOKEN": "fake-token-not-real",
            "CERT_WATCH_DOMAINS": "example.com",
            **extra,
        }
    )


def test_paths_are_the_documented_ones() -> None:
    assert CERTIFICATES.path() == "/apis/cert-manager.io/v1/certificates"
    assert CERTIFICATES.path("shop") == "/apis/cert-manager.io/v1/namespaces/shop/certificates"
    assert CERTIFICATES.path("shop", "x") == "/apis/cert-manager.io/v1/namespaces/shop/certificates/x"
    assert CHALLENGES.path("shop") == "/apis/acme.cert-manager.io/v1/namespaces/shop/challenges"
    assert CLUSTER_ISSUERS.path("shop", "le") == "/apis/cert-manager.io/v1/clusterissuers/le"  # never namespaced
    assert len(RESOURCES) == 6


@respx.mock
async def test_bearer_token_pagination_and_get_only() -> None:
    route = respx.get(f"{API}/apis/cert-manager.io/v1/certificates").mock(
        side_effect=[
            httpx.Response(200, json={"metadata": {"continue": "abc"}, "items": [{"metadata": {"name": "a"}}]}),
            httpx.Response(200, json={"metadata": {}, "items": [{"metadata": {"name": "b"}}]}),
        ]
    )
    client = KubeClient(settings())
    items = await client.list(CERTIFICATES)
    await client.aclose()
    assert [i["metadata"]["name"] for i in items] == ["a", "b"] and route.call_count == 2
    assert route.calls[1].request.url.params["continue"] == "abc"
    assert route.calls.last.request.headers["authorization"] == "Bearer fake-token-not-real"
    assert all(call.request.method == "GET" for call in respx.calls)


@respx.mock
async def test_the_allow_list_is_read_namespace_by_namespace_and_never_beyond() -> None:
    shop = respx.get(f"{API}/apis/cert-manager.io/v1/namespaces/shop/certificates").respond(json={"items": []})
    mail = respx.get(f"{API}/apis/cert-manager.io/v1/namespaces/mail/certificates").respond(json={"items": []})
    client = KubeClient(settings(CERT_WATCH_NAMESPACES="shop, mail"))
    await client.list(CERTIFICATES)
    assert shop.called and mail.called and respx.calls.call_count == 2  # no cluster-wide list
    with pytest.raises(Refused, match="allow-list"):
        await client.list(CERTIFICATES, "kube-system")
    with pytest.raises(Refused, match="allow-list"):
        await client.get(CERTIFICATES, "x", "kube-system")
    assert respx.calls.call_count == 2
    await client.aclose()


@respx.mock
@pytest.mark.parametrize(
    ("status", "fragment"),
    [(401, "401"), (403, "lacks get/list"), (404, "not found"), (500, "500")],
)
async def test_http_errors_say_what_to_do(status: int, fragment: str) -> None:
    respx.get(f"{API}/apis/cert-manager.io/v1/certificates").respond(status)
    client = KubeClient(settings())
    with pytest.raises(SourceError, match=fragment):
        await client.list(CERTIFICATES)
    await client.aclose()


@respx.mock
async def test_unreachable_and_not_json() -> None:
    route = respx.get(f"{API}/apis/cert-manager.io/v1/certificates")
    route.side_effect = httpx.ConnectError("boom")
    client = KubeClient(settings())
    with pytest.raises(SourceError, match="did not answer"):
        await client.list(CERTIFICATES)
    route.side_effect = None
    route.respond(200, text="<html>")
    with pytest.raises(SourceError, match="not JSON"):
        await client.list(CERTIFICATES)
    await client.aclose()


async def test_the_token_is_checked_by_the_fake() -> None:
    client = KubeClient(
        Settings.from_env({"CERT_WATCH_FAKE": "1"}).model_copy(update={"kube_token": None}),
        transport=httpx.MockTransport(FakeKube(NOW)),
    )
    with pytest.raises(SourceError, match="401"):
        await client.list(CERTIFICATES)
    await client.aclose()


async def test_a_write_is_refused_by_the_fake_and_the_client_has_no_write_method() -> None:
    assert not any(name in dir(KubeClient) for name in ("post", "put", "patch", "delete", "create", "update"))
    response = FakeKube(NOW)(
        httpx.Request(
            "DELETE",
            f"{API}/apis/cert-manager.io/v1/certificates/x",
            headers={"authorization": "Bearer fake-kube-token"},
        )
    )
    assert response.status_code == 405
