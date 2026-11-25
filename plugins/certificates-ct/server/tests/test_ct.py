# SPDX-License-Identifier: Apache-2.0
"""The CT client: what it sends, what it refuses before sending, and how a busy or odd crt.sh is reported."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from tranibor_certificates_ct.config import Settings, normalise_domain
from tranibor_certificates_ct.ct import CtClient, entry_view
from tranibor_certificates_ct.errors import Refused, SourceError

NOW = datetime(2026, 11, 1, tzinfo=UTC)
CT = "https://crt.sh/"


def client() -> CtClient:
    return CtClient(Settings.from_env({"CERTIFICATES_CT_DOMAINS": "example.com"}))


@pytest.mark.parametrize(
    ("raw", "domain"),
    [("Example.COM.", "example.com"), ("*.example.com", "example.com"), ("%.a.example.com", "a.example.com")],
)
def test_normalise_domain(raw: str, domain: str) -> None:
    assert normalise_domain(raw) == domain


@pytest.mark.parametrize("raw", ["com", "a b", "", "https://example.com", "-x.example.com"])
def test_a_domain_must_be_a_domain(raw: str) -> None:
    with pytest.raises(ValueError, match="not a domain"):
        normalise_domain(raw)


@respx.mock
async def test_a_search_asks_for_the_wildcard_and_json_and_nothing_else() -> None:
    route = respx.get(CT).mock(return_value=httpx.Response(200, json=[{"id": 1}, "junk"]))
    c = client()
    assert await c.search("example.com") == [{"id": 1}]
    assert route.calls.last.request.url.params["q"] == "%.example.com"
    assert route.calls.last.request.url.params["output"] == "json"
    assert route.calls.last.request.method == "GET"
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
        datetime(2026, 11, 2, tzinfo=UTC),
    )
    assert view["names"] == ["*.example.com", "a.example.com", "b.example.com"]
    assert view["not_after"] == "2026-11-01T00:00:00Z" and view["expired"] is True
    assert view["logged_at"] == "2026-10-01T00:01:00Z"
    assert entry_view({}, NOW)["names"] == []
