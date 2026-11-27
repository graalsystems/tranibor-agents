# SPDX-License-Identifier: Apache-2.0
"""The client of the Domains v3 API: the bearer header, the rate-limit wait, and the errors it explains."""

from __future__ import annotations

import httpx
import pytest

from tranibor_dns_godaddy.client import MAX_WAIT, GoDaddyClient
from tranibor_dns_godaddy.config import ENVIRONMENTS, Settings
from tranibor_dns_godaddy.errors import GoDaddyError

TOKEN = "pat-for-tests"


def settings(environment: str = "production") -> Settings:
    return Settings.from_env(
        {"DNS_GODADDY_ENVIRONMENT": environment, "DNS_GODADDY_TOKEN": TOKEN, "DNS_GODADDY_ZONES": "example.com"}
    )


def client(
    handler: httpx.MockTransport | None, *, environment: str = "production"
) -> tuple[GoDaddyClient, list[float]]:
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    return GoDaddyClient(settings(environment), transport=handler, sleep=sleep), waited


async def test_the_token_is_sent_as_a_bearer_and_nothing_else_authenticates() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    c, _ = client(httpx.MockTransport(handler))
    assert await c.request("GET", "/v3/domains/zones/example.com/dns-records", {"page": "1"}) == []
    request = seen[0]
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert not [h for h in request.headers if h.lower() in ("x-ovh-signature", "x-api-key")]
    assert str(request.url) == "https://api.godaddy.com/v3/domains/zones/example.com/dns-records?page=1"


async def test_each_environment_has_its_own_host() -> None:
    assert ENVIRONMENTS == {"production": "https://api.godaddy.com", "ote": "https://api.ote-godaddy.com"}
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(204)

    c, _ = client(httpx.MockTransport(handler), environment="ote")
    assert await c.request("DELETE", "/v3/domains/zones/example.com/dns-records/x") is None
    assert hosts == ["api.ote-godaddy.com"]


async def test_a_429_waits_for_retry_after_then_retries_and_gives_up_after_two() -> None:
    answers = [429, 429, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        status = answers.pop(0)
        return httpx.Response(status, json=[], headers={"Retry-After": "7"} if status == 429 else {})

    c, waited = client(httpx.MockTransport(handler))
    assert await c.request("GET", "/x") == []
    assert waited == [7.0, 7.0]

    always = httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "9999"}))
    c, waited = client(always)
    with pytest.raises(GoDaddyError, match=r"429.*rate limited"):
        await c.request("GET", "/x")
    assert waited == [MAX_WAIT, MAX_WAIT], "the wait is capped and the retries are bounded"


async def test_a_429_without_a_usable_retry_after_waits_a_default() -> None:
    answers = [429, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(answers.pop(0), json={}, headers={"Retry-After": "soon"})

    c, waited = client(httpx.MockTransport(handler))
    await c.request("GET", "/x")
    assert waited == [5.0]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"code": "UNABLE_TO_AUTHENTICATE", "message": "bad"}, "the token was refused"),
        (403, {"code": "ACCESS_DENIED", "message": "no"}, "scope"),
        (403, {"code": "ACCESS_DENIED", "message": "no"}, "not eligible for domain management"),
        (404, {"code": "DOMAIN_NOT_FOUND", "message": "gone"}, "not found"),
        (409, {"code": "dns_record_not_mutable", "message": "SOA"}, "dns_record_not_mutable"),
        (422, {"code": "INVALID_TTL", "message": "ttl"}, "invalid"),
        (503, "oops", "GoDaddy failed"),
    ],
)
async def test_errors_are_explained_without_the_token(status: int, body: object, expected: str) -> None:
    c, _ = client(httpx.MockTransport(lambda r: httpx.Response(status, json=body)))
    with pytest.raises(GoDaddyError) as caught:
        await c.request("GET", "/x")
    assert expected in str(caught.value) and TOKEN not in str(caught.value) and caught.value.status == status


async def test_an_unreachable_host_is_an_error_with_status_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    c, _ = client(httpx.MockTransport(handler))
    with pytest.raises(GoDaddyError, match="could not be reached") as caught:
        await c.request("GET", "/x")
    assert caught.value.status == 0


async def test_a_long_error_text_is_shortened() -> None:
    c, _ = client(httpx.MockTransport(lambda r: httpx.Response(400, json={"message": "x" * 5000})))
    with pytest.raises(GoDaddyError) as caught:
        await c.request("GET", "/x")
    assert len(str(caught.value)) < 500
