# SPDX-License-Identifier: Apache-2.0
"""The signed client: the signature, the clock, the retry and the error texts."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from tranibor_ovh_dns.client import OvhClient, signature
from tranibor_ovh_dns.config import Settings
from tranibor_ovh_dns.errors import OvhError
from tranibor_ovh_dns.fake import FakeOvh

BASE = "https://eu.api.ovh.com/1.0"


def settings() -> Settings:
    return Settings.from_env(
        {
            "OVH_DNS_ENDPOINT": "ovh-eu",
            "OVH_DNS_APPLICATION_KEY": "ak-for-tests",
            "OVH_DNS_APPLICATION_SECRET": "as-for-tests",
            "OVH_DNS_CONSUMER_KEY": "ck-for-tests",
            "OVH_DNS_ZONES": "example.com",
        }
    )


def test_the_signature_is_dollar_one_dollar_then_sha1_of_the_plus_joined_parts() -> None:
    material = "as+ck+POST+https://eu.api.ovh.com/1.0/domain/zone/example.com/refresh++1700000000"
    expected = "$1$" + hashlib.sha1(material.encode(), usedforsecurity=False).hexdigest()
    got = signature("as", "ck", "POST", "https://eu.api.ovh.com/1.0/domain/zone/example.com/refresh", "", 1700000000)
    assert got == expected
    assert got.startswith("$1$") and len(got) == 43


@respx.mock
async def test_a_request_carries_the_four_headers_and_the_servers_clock() -> None:
    respx.get(f"{BASE}/auth/time").mock(return_value=httpx.Response(200, text="1700000100"))
    route = respx.post(f"{BASE}/domain/zone/example.com/record").mock(return_value=httpx.Response(200, json={"id": 1}))
    client = OvhClient(settings(), clock=lambda: 1700000000.0)
    await client.request("POST", "/domain/zone/example.com/record", body={"fieldType": "A", "target": "192.0.2.1"})
    request = route.calls.last.request
    body = request.content.decode()
    assert json.loads(body) == {"fieldType": "A", "target": "192.0.2.1"}
    assert request.headers["X-Ovh-Application"] == "ak-for-tests"
    assert request.headers["X-Ovh-Consumer"] == "ck-for-tests"
    assert request.headers["X-Ovh-Timestamp"] == "1700000100"  # the local clock, corrected by the delta
    assert request.headers["X-Ovh-Signature"] == signature(
        "as-for-tests", "ck-for-tests", "POST", str(request.url), body, 1700000100
    )
    await client.aclose()


@respx.mock
async def test_the_query_is_part_of_the_signed_url() -> None:
    respx.get(f"{BASE}/auth/time").mock(return_value=httpx.Response(200, text="1700000000"))
    route = respx.get(url__regex=r".*/record.*").mock(return_value=httpx.Response(200, json=[]))
    client = OvhClient(settings(), clock=lambda: 1700000000.0)
    await client.request("GET", "/domain/zone/example.com/record", {"fieldType": "A", "subDomain": "www"})
    request = route.calls.last.request
    assert str(request.url).endswith("/record?fieldType=A&subDomain=www")
    assert request.headers["X-Ovh-Signature"] == signature(
        "as-for-tests", "ck-for-tests", "GET", str(request.url), "", 1700000000
    )
    await client.aclose()


@respx.mock
async def test_a_clock_error_is_retried_once_after_resynchronising() -> None:
    times = iter(["1700000000", "1700009999"])
    respx.get(f"{BASE}/auth/time").mock(side_effect=lambda _: httpx.Response(200, text=next(times)))
    route = respx.get(f"{BASE}/domain/zone").mock(
        side_effect=[
            httpx.Response(401, json={"message": "Invalid signature: bad timestamp"}),
            httpx.Response(200, json=["example.com"]),
        ]
    )
    client = OvhClient(settings(), clock=lambda: 1700000000.0)
    assert await client.request("GET", "/domain/zone") == ["example.com"]
    assert route.call_count == 2
    await client.aclose()


@respx.mock
@pytest.mark.parametrize(
    ("status", "words"),
    [
        (400, "refused"),
        (401, "credential"),
        (403, "access rules"),
        (404, "not found"),
        (409, "conflict"),
        (429, "rate"),
        (503, "OVHcloud"),
    ],
)
async def test_errors_become_one_line_with_a_meaning(status: int, words: str) -> None:
    respx.get(f"{BASE}/auth/time").mock(return_value=httpx.Response(200, text="1700000000"))
    respx.get(f"{BASE}/domain/zone").mock(return_value=httpx.Response(status, json={"message": "nope"}))
    client = OvhClient(settings(), clock=lambda: 1700000000.0)
    with pytest.raises(OvhError) as raised:
        await client.request("GET", "/domain/zone")
    assert raised.value.status == status
    assert "nope" in str(raised.value)
    assert words.lower() in str(raised.value).lower()
    await client.aclose()


@respx.mock
async def test_a_transport_failure_is_status_zero() -> None:
    respx.get(f"{BASE}/auth/time").mock(side_effect=httpx.ConnectError("down"))
    client = OvhClient(settings(), clock=lambda: 1700000000.0)
    with pytest.raises(OvhError) as raised:
        await client.request("GET", "/domain/zone")
    assert raised.value.status == 0
    await client.aclose()


async def test_the_fake_checks_the_signature_and_survives_a_skewed_clock() -> None:
    fake = FakeOvh(skew=3600)  # the "server" is an hour ahead of this machine
    client = OvhClient(Settings.from_env({"OVH_DNS_FAKE": "1"}), transport=httpx.MockTransport(fake))
    assert await client.request("GET", "/domain/zone") == ["example.com", "example.net", "private.example"]
    await client.aclose()


async def test_the_fake_refuses_a_wrong_secret() -> None:
    bad = Settings.from_env({"OVH_DNS_FAKE": "1"}).model_copy(update={"application_secret": SecretStr("wrong")})
    client = OvhClient(bad, transport=httpx.MockTransport(FakeOvh()))
    with pytest.raises(OvhError) as raised:
        await client.request("GET", "/domain/zone")
    assert raised.value.status == 401
    await client.aclose()
