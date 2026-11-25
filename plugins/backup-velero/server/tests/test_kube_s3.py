# SPDX-License-Identifier: Apache-2.0
"""The Kubernetes and S3 clients against mocked wire answers."""

from __future__ import annotations

import httpx
import pytest
import respx

from tranibor_backup_velero.config import Settings
from tranibor_backup_velero.errors import Failure
from tranibor_backup_velero.kube import Kube
from tranibor_backup_velero.s3 import Bucket, sign

from .conftest import NOW

API = "https://kubernetes.example.com:6443"
SETTINGS = Settings.from_env(
    {
        "VELERO_KUBE_API_URL": API,
        "VELERO_KUBE_TOKEN": "not-a-real-token",
        "VELERO_RESTORE_NAMESPACE_PREFIX": "restore-test-",
    }
)


@respx.mock
async def test_list_follows_continue_and_sends_the_token() -> None:
    route = respx.get(f"{API}/apis/velero.io/v1/namespaces/velero/backups").mock(
        side_effect=[
            httpx.Response(200, json={"metadata": {"continue": "abc"}, "items": [{"metadata": {"name": "a"}}]}),
            httpx.Response(200, json={"metadata": {}, "items": [{"metadata": {"name": "b"}}]}),
        ]
    )
    kube = Kube(SETTINGS)
    items = await kube.list(kube.velero("backups"), "velero.io/schedule-name=x")
    assert [i["metadata"]["name"] for i in items] == ["a", "b"]
    first, second = route.calls[0].request, route.calls[1].request
    assert first.headers["authorization"] == "Bearer not-a-real-token"
    assert first.url.params["labelSelector"] == "velero.io/schedule-name=x"
    assert "continue" not in first.url.params and second.url.params["continue"] == "abc"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (429, "rate_limited"),
        (500, "upstream_error"),
    ],
)
@respx.mock
async def test_errors_are_typed(status: int, code: str) -> None:
    respx.get(f"{API}/api/v1/namespaces/x").mock(
        return_value=httpx.Response(status, json={"kind": "Status", "message": "the reason"})
    )
    with pytest.raises(Failure) as error:
        await Kube(SETTINGS).get("/api/v1/namespaces/x")
    assert error.value.code == code
    assert "not-a-real-token" not in str(error.value)


@respx.mock
async def test_a_redirect_is_not_followed_so_the_token_stays_home() -> None:
    respx.get(f"{API}/api/v1/namespaces/x").mock(
        return_value=httpx.Response(302, headers={"location": "https://elsewhere.example.org/"})
    )
    with pytest.raises(Failure) as error:
        await Kube(SETTINGS).get("/api/v1/namespaces/x")
    assert error.value.code == "upstream_error"


@respx.mock
async def test_unreachable_is_upstream_error() -> None:
    respx.get(f"{API}/api/v1/namespaces/x").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(Failure) as error:
        await Kube(SETTINGS).get("/api/v1/namespaces/x")
    assert error.value.code == "upstream_error"


def test_a_bad_ca_is_a_configuration_error() -> None:
    settings = Settings.from_env({**{"VELERO_KUBE_API_URL": API, "VELERO_KUBE_CA_CERT": "not a pem"}})
    with pytest.raises(Failure) as error:
        Kube(settings)._client()
    assert error.value.code == "not_configured"


def test_sigv4_against_the_documented_example() -> None:
    """AWS's SigV4 example "GET Object" (s3 docs, `sigv4-header-based-auth`): bucket examplebucket, key
    test.txt, Range bytes=0-9, 2013-05-24T00:00:00Z, the documentation's example credentials."""
    from datetime import UTC, datetime

    auth = sign(
        method="GET",
        path="/test.txt",
        query={},
        headers={
            "host": "examplebucket.s3.amazonaws.com",
            "range": "bytes=0-9",
            "x-amz-content-sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "x-amz-date": "20130524T000000Z",
        },
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        when=datetime(2013, 5, 24, tzinfo=UTC),
    )
    assert auth.endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in auth


LISTING = """<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<IsTruncated>true</IsTruncated><NextContinuationToken>tok</NextContinuationToken>
<Contents><Key>backups/a/x</Key><LastModified>2026-11-24T02:00:00.000Z</LastModified><Size>12</Size></Contents>
</ListBucketResult>"""


@respx.mock
async def test_bucket_listing_pages_and_signs() -> None:
    route = respx.get("https://s3.example.com/bk").mock(return_value=httpx.Response(200, text=LISTING))
    bucket = Bucket("https://s3.example.com", "bk", "eu-west-1", "AKEXAMPLE", "not-a-secret")
    objects, token = await bucket.list_page(NOW, "backups/", None)
    assert objects == [{"key": "backups/a/x", "size": "12", "last_modified": "2026-11-24T02:00:00.000Z"}]
    assert token == "tok"
    request = route.calls[0].request
    assert request.url.params["list-type"] == "2" and request.url.params["prefix"] == "backups/"
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKEXAMPLE/20261125/eu-west-1/s3/")
    assert "not-a-secret" not in request.headers["authorization"]


@pytest.mark.parametrize(("status", "code"), [(403, "forbidden"), (404, "not_found"), (500, "upstream_error")])
@respx.mock
async def test_bucket_errors(status: int, code: str) -> None:
    respx.get("https://s3.example.com/bk").mock(return_value=httpx.Response(status, text="<Error/>"))
    with pytest.raises(Failure) as error:
        await Bucket("https://s3.example.com", "bk", "r", "a", "s").list_page(NOW, "", None)
    assert error.value.code == code


@respx.mock
async def test_a_dtd_in_the_bucket_answer_is_refused() -> None:
    respx.get("https://s3.example.com/bk").mock(
        return_value=httpx.Response(200, text='<!DOCTYPE x [<!ENTITY a "b">]><ListBucketResult/>')
    )
    with pytest.raises(Failure):
        await Bucket("https://s3.example.com", "bk", "r", "a", "s").list_page(NOW, "", None)
