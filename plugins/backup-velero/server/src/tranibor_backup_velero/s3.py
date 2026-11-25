# SPDX-License-Identifier: Apache-2.0
"""Read-only listing of the backup bucket: S3 ListObjectsV2 with AWS Signature Version 4.

Path-style addressing only (`<endpoint>/<bucket>?list-type=2`), so the one host the connector names is the
only host reached (MinIO, Ceph RGW, Garage and most S3-compatible stores; AWS S3 accepts it too, though AWS
deprecates it for new buckets). No object is ever read, written or deleted: the only request this module
builds is `GET /<bucket>?list-type=2`.

Docs: ListObjectsV2 https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html ; signing
https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv4-create-signed-request.html and
https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html (the unit test replays the
documented GET Object example against `sign`).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

import httpx

from .errors import Failure

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
TIMEOUT_S = 20.0
PAGE_KEYS = 1000
MAX_PAGES = 20


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def _q(value: str) -> str:
    return quote(value, safe="-_.~")


def sign(
    *,
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    access_key: str,
    secret_key: str,
    region: str,
    when: datetime,
    payload_hash: str = EMPTY_SHA256,
    service: str = "s3",
) -> str:
    """The `Authorization` header value. `headers` are the ones to sign (lower-case names), `host` included;
    `x-amz-date` and `x-amz-content-sha256` must be among them."""
    stamp = when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    names = sorted(h.lower() for h in headers)
    lowered = {k.lower(): " ".join(v.split()) for k, v in headers.items()}
    canonical_headers = "".join(f"{n}:{lowered[n]}\n" for n in names)
    canonical_query = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items()))
    canonical = "\n".join(
        [method, quote(path, safe="/-_.~"), canonical_query, canonical_headers, ";".join(names), payload_hash]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"


class Bucket:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint, self.bucket, self.region = endpoint.rstrip("/"), bucket, region
        self.access_key, self.secret_key = access_key, secret_key
        self.transport = transport
        self.host = urlsplit(endpoint).netloc

    async def list_page(
        self, when: datetime, prefix: str, token: str | None
    ) -> tuple[list[dict[str, str]], str | None]:
        query = {"list-type": "2", "max-keys": str(PAGE_KEYS)}
        if prefix:
            query["prefix"] = prefix
        if token:
            query["continuation-token"] = token
        path = f"/{self.bucket}"
        headers = {
            "host": self.host,
            "x-amz-content-sha256": EMPTY_SHA256,
            "x-amz-date": when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
        }
        auth = sign(
            method="GET",
            path=path,
            query=query,
            headers=headers,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region=self.region,
            when=when,
        )
        sent = {k: v for k, v in headers.items() if k != "host"} | {"Authorization": auth}
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT_S, follow_redirects=False) as c:
                response = await c.get(f"{self.endpoint}{path}", params=query, headers=sent)
        except httpx.HTTPError as error:
            raise Failure("upstream_error", f"the bucket could not be reached ({type(error).__name__})") from None
        if response.status_code in (401, 403):
            raise Failure("forbidden", f"the bucket refused the access key (HTTP {response.status_code})")
        if response.status_code == 404:
            raise Failure("not_found", "the bucket does not exist at this endpoint")
        if not response.is_success:
            raise Failure("upstream_error", f"the bucket answered {response.status_code}")
        return _parse(response.text)


def _parse(text: str) -> tuple[list[dict[str, str]], str | None]:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:  # no DTD in an S3 answer: refuse entity tricks
        raise Failure("upstream_error", "the bucket answered a document with a DTD")
    try:
        root = ET.fromstring(text)  # noqa: S314 - DTDs refused above
    except ET.ParseError:
        raise Failure("upstream_error", "the bucket answered something that is not S3 XML") from None
    objects = [
        {
            "key": (c.findtext(f"{NS}Key") or c.findtext("Key") or ""),
            "size": (c.findtext(f"{NS}Size") or c.findtext("Size") or "0"),
            "last_modified": (c.findtext(f"{NS}LastModified") or c.findtext("LastModified") or ""),
        }
        for c in [*root.findall(f"{NS}Contents"), *root.findall("Contents")]
    ]
    truncated = (root.findtext(f"{NS}IsTruncated") or root.findtext("IsTruncated") or "").lower() == "true"
    token = root.findtext(f"{NS}NextContinuationToken") or root.findtext("NextContinuationToken")
    return objects, (token if truncated and token else None)
