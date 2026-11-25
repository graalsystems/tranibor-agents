# SPDX-License-Identifier: Apache-2.0
"""The clients against mocked HTTP (respx): the real wire format, authentication, pagination, and errors."""

from __future__ import annotations

import httpx
import pytest
import respx

from tranibor_unifi.client import Controller, SiteManager
from tranibor_unifi.errors import Failure

BASE = "https://unifi.example.com"
API = f"{BASE}/proxy/network/integration"
KEY = "an-api-key-for-tests-only"


@respx.mock
async def test_the_controller_sends_the_key_in_x_api_key_and_follows_offset_pages() -> None:
    def page(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        assert request.headers["X-API-KEY"] == KEY
        assert int(request.url.params["limit"]) <= 200
        rows = [{"id": str(i)} for i in range(offset, min(offset + 200, 450))]
        return httpx.Response(
            200, json={"offset": offset, "limit": 200, "count": len(rows), "totalCount": 450, "data": rows}
        )

    route = respx.get(f"{API}/v1/sites").mock(side_effect=page)
    items, total = await Controller(BASE, KEY).collect("/v1/sites", limit=500)
    assert (len(items), total, route.call_count) == (450, 450, 3)
    assert [c.request.url.params["offset"] for c in route.calls] == ["0", "200", "400"]


@respx.mock
async def test_the_controller_stops_at_the_limit_asked() -> None:
    route = respx.get(f"{API}/v1/sites").mock(
        return_value=httpx.Response(
            200,
            json={"offset": 0, "limit": 5, "count": 5, "totalCount": 99, "data": [{"id": str(i)} for i in range(5)]},
        )
    )
    items, total = await Controller(BASE, KEY).collect("/v1/sites", limit=5)
    assert (len(items), total, route.call_count) == (5, 99, 1)


@respx.mock
async def test_an_empty_page_ends_the_loop_even_if_the_total_lies() -> None:
    respx.get(f"{API}/v1/sites").mock(
        return_value=httpx.Response(200, json={"offset": 0, "limit": 200, "count": 0, "totalCount": 10, "data": []})
    )
    assert await Controller(BASE, KEY).collect("/v1/sites", limit=100) == ([], 10)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_arguments"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (429, "rate_limited"),
        (500, "upstream_error"),
        (502, "upstream_error"),
    ],
)
@respx.mock
async def test_statuses_become_typed_failures_that_never_carry_the_body_nor_the_key(status: int, code: str) -> None:
    respx.get(f"{API}/v1/info").mock(return_value=httpx.Response(status, json={"message": f"secret-{KEY}"}))
    with pytest.raises(Failure) as failure:
        await Controller(BASE, KEY).get("/v1/info")
    assert failure.value.code == code
    assert KEY not in str(failure.value)
    assert "secret" not in str(failure.value)


@respx.mock
async def test_a_redirect_is_not_followed_and_a_transport_error_is_typed() -> None:
    respx.get(f"{API}/v1/info").mock(return_value=httpx.Response(302, headers={"Location": "https://evil.example/"}))
    with pytest.raises(Failure) as redirected:
        await Controller(BASE, KEY).get("/v1/info")
    assert redirected.value.code == "upstream_error"
    respx.get(f"{API}/v1/sites").mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(Failure) as down:
        await Controller(BASE, KEY).get("/v1/sites")
    assert "ConnectError" in str(down.value)


@respx.mock
async def test_the_site_manager_uses_its_own_header_and_follows_next_token() -> None:
    def page(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == KEY
        token = request.url.params.get("nextToken")
        return httpx.Response(
            200, json={"data": [{"hostId": token or "first"}], "nextToken": "" if token else "second"}
        )

    route = respx.get("https://api.ui.com/v1/devices").mock(side_effect=page)
    rows = await SiteManager("https://api.ui.com", KEY).collect("/v1/devices")
    assert [r["hostId"] for r in rows] == ["first", "second"]
    assert route.call_count == 2


@respx.mock
async def test_a_write_posts_json_and_the_voucher_delete_carries_its_filter() -> None:
    post = respx.post(f"{API}/v1/sites/s/devices/d/actions").mock(return_value=httpx.Response(200, json={}))
    delete = respx.delete(f"{API}/v1/sites/s/hotspot/vouchers").mock(
        return_value=httpx.Response(200, json={"vouchersDeleted": 2})
    )
    controller = Controller(BASE, KEY)
    await controller.request("POST", "/v1/sites/s/devices/d/actions", body={"action": "RESTART"})
    assert post.calls[0].request.content == b'{"action":"RESTART"}'
    out = await controller.request("DELETE", "/v1/sites/s/hotspot/vouchers", params={"filter": "name.eq('x')"})
    assert out == {"vouchersDeleted": 2}
    assert delete.calls[0].request.url.params["filter"] == "name.eq('x')"
