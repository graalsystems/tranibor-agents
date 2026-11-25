# SPDX-License-Identifier: Apache-2.0
"""The Certificate Transparency client and the name-coverage rule."""

from __future__ import annotations

import httpx
import pytest
import respx

from tranibor_cert_watch.config import Settings
from tranibor_cert_watch.ct import CtClient, covers, entry_view
from tranibor_cert_watch.errors import Refused, SourceError

from .conftest import NOW

CT = "https://crt.sh"


def client() -> CtClient:
    return CtClient(
        Settings.from_env(
            {
                "CERT_WATCH_KUBE_API_URL": "https://kube.example.com:6443",
                "CERT_WATCH_KUBE_TOKEN": "fake-token-not-real",
                "CERT_WATCH_DOMAINS": "Example.com, shop.example.org.",
            }
        )
    )


@pytest.mark.parametrize(
    ("pattern", "name", "expected"),
    [
        ("shop.example.com", "shop.example.com", True),
        ("SHOP.example.com", "shop.EXAMPLE.com", True),
        ("*.example.com", "a.example.com", True),
        ("*.example.com", "a.b.example.com", False),  # one label only
        ("*.example.com", "example.com", False),
        ("*.example.com", ".example.com", False),
        ("shop.example.com", "www.shop.example.com", False),
        ("a.example.com", "example.com", False),
    ],
)
def test_covers(pattern: str, name: str, expected: bool) -> None:
    assert covers(pattern, name) is expected


@respx.mock
async def test_the_query_is_a_wildcard_json_search_of_a_configured_domain() -> None:
    route = respx.get(CT, params={"q": "%.example.com", "output": "json"}).respond(json=[{"id": 1}, "junk"])
    c = client()
    assert await c.search("EXAMPLE.com.") == [{"id": 1}]
    exact = respx.get(CT, params={"q": "shop.example.org", "output": "json"}).respond(json=[])
    assert await c.search("shop.example.org", include_subdomains=False) == []
    assert route.called and exact.called
    await c.aclose()


@respx.mock
async def test_only_configured_domains_leave_the_server() -> None:
    c = client()
    for domain in ("evil.test", "example.com.evil.test", "badexample.com", "com", "a b", ""):
        with pytest.raises(Refused):
            await c.search(domain)
    assert respx.calls.call_count == 0
    assert c.check_domain("deep.sub.example.com") == "deep.sub.example.com"
    await c.aclose()


@respx.mock
@pytest.mark.parametrize(
    "response", [httpx.Response(503), httpx.Response(200, text="<html>busy</html>"), httpx.Response(200, json={"a": 1})]
)
async def test_a_busy_or_odd_crt_sh_is_an_error_not_a_crash(response: httpx.Response) -> None:
    respx.get(CT).mock(return_value=response)
    c = client()
    with pytest.raises(SourceError):
        await c.search("example.com")
    await c.aclose()


@respx.mock
async def test_unreachable() -> None:
    respx.get(CT).mock(side_effect=httpx.ReadTimeout("slow"))
    c = client()
    with pytest.raises(SourceError, match="did not answer"):
        await c.search("example.com")
    await c.aclose()


def test_entry_view_splits_names_and_reads_naive_utc_times() -> None:
    view = entry_view(
        {
            "id": 7,
            "name_value": "B.example.com\n*.example.com\n\na.example.com",
            "common_name": "a.example.com",
            "issuer_name": "C=XX, O=Example Public CA",
            "not_before": "2026-10-01T00:00:00",
            "not_after": "2026-11-01T00:00:00",
            "entry_timestamp": "2026-10-01T00:01:00.123",
            "serial_number": "ab",
        },
        NOW,
    )
    assert view["names"] == ["*.example.com", "a.example.com", "b.example.com"]
    assert view["not_after"] == "2026-11-01T00:00:00Z" and view["expired"] is True
    assert view["logged_at"] == "2026-10-01T00:01:00Z"
    assert entry_view({}, NOW)["names"] == []
