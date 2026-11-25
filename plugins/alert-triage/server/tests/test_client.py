# SPDX-License-Identifier: Apache-2.0
"""The client against mocked HTTP: routes, parameters, headers and the mapping of errors."""

from __future__ import annotations

import httpx
import pytest
import respx

from tranibor_alert_triage.client import AM, Grafana
from tranibor_alert_triage.errors import Failure

BASE = "https://grafana.example.com"


@pytest.fixture
def grafana() -> Grafana:
    return Grafana(BASE, "glsa_fake_token_for_tests", org_id=7)


@respx.mock
async def test_alerts_send_the_token_the_org_and_repeated_filters(grafana: Grafana) -> None:
    route = respx.get(f"{BASE}{AM}/alerts").mock(return_value=httpx.Response(200, json=[{"labels": {}}]))
    assert await grafana.alerts(['alertname="A"', 'service="b"'], active=True, silenced=False, inhibited=False)
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer glsa_fake_token_for_tests"
    assert request.headers["X-Grafana-Org-Id"] == "7"
    assert request.url.params.get_list("filter") == ['alertname="A"', 'service="b"']
    assert request.url.params["silenced"] == "false"


@respx.mock
async def test_no_org_header_without_an_org() -> None:
    route = respx.get(f"{BASE}/api/datasources").mock(return_value=httpx.Response(200, json=[]))
    await Grafana(BASE, "t").datasources()
    assert "X-Grafana-Org-Id" not in route.calls.last.request.headers


@respx.mock
async def test_silence_routes(grafana: Grafana) -> None:
    created = respx.post(f"{BASE}{AM}/silences").mock(return_value=httpx.Response(200, json={"silenceID": "abc"}))
    assert await grafana.create_silence({"comment": "c"}) == "abc"
    assert created.calls.last.request.read() == b'{"comment":"c"}'
    expired = respx.delete(f"{BASE}{AM}/silence/abc").mock(return_value=httpx.Response(200))
    await grafana.expire_silence("abc")
    assert expired.called


@respx.mock
async def test_a_silence_without_an_id_is_an_upstream_error(grafana: Grafana) -> None:
    respx.post(f"{BASE}{AM}/silences").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(Failure) as failure:
        await grafana.create_silence({})
    assert failure.value.code == "upstream_error"


@respx.mock
async def test_rule_search_dashboard_and_annotation_routes(grafana: Grafana) -> None:
    respx.get(f"{BASE}/api/v1/provisioning/alert-rules/r1").mock(return_value=httpx.Response(200, json={"uid": "r1"}))
    assert (await grafana.alert_rule("r1"))["uid"] == "r1"
    search = respx.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, json=[{"uid": "d"}]))
    await grafana.search_dashboards("checkout", 5)
    assert dict(search.calls.last.request.url.params) == {"type": "dash-db", "query": "checkout", "limit": "5"}
    respx.get(f"{BASE}/api/dashboards/uid/d").mock(return_value=httpx.Response(200, json={"dashboard": {}}))
    assert await grafana.dashboard("d") == {"dashboard": {}}
    listing = respx.get(f"{BASE}/api/annotations").mock(return_value=httpx.Response(200, json=[]))
    await grafana.annotations(1000, 2000, ["deploy", "checkout"], 10, "annotation")
    params = listing.calls.last.request.url.params
    assert (params["from"], params["to"], params["limit"], params["type"]) == ("1000", "2000", "10", "annotation")
    assert params.get_list("tags") == ["deploy", "checkout"]
    respx.post(f"{BASE}/api/annotations").mock(return_value=httpx.Response(200, json={"id": 4}))
    assert await grafana.add_annotation({"text": "t"}) == 4


@respx.mock
async def test_a_failed_query_returns_its_per_query_error(grafana: Grafana) -> None:
    body = {"results": {"A": {"error": "parse error", "status": 400}}}
    respx.post(f"{BASE}/api/ds/query").mock(return_value=httpx.Response(400, json=body))
    assert await grafana.query({"queries": []}) == body


@respx.mock
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_arguments"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (429, "rate_limited"),
        (500, "upstream_error"),
        (502, "upstream_error"),
    ],
)
async def test_errors_are_mapped_by_status(grafana: Grafana, status: int, code: str) -> None:
    respx.get(f"{BASE}/api/datasources").mock(return_value=httpx.Response(status, text="<secret detail>"))
    with pytest.raises(Failure) as failure:
        await grafana.datasources()
    assert failure.value.code == code
    assert "secret detail" not in str(failure.value), "Grafana's body is never relayed"


@respx.mock
async def test_unreachable_and_non_json_answers(grafana: Grafana) -> None:
    respx.get(f"{BASE}/api/datasources").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(Failure, match="could not be reached"):
        await grafana.datasources()
    respx.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, text="<html>login</html>"))
    with pytest.raises(Failure, match="not JSON"):
        await grafana.search_dashboards("", 1)


@respx.mock
async def test_redirects_are_not_followed(grafana: Grafana) -> None:
    """A redirect to a login page is an error, never a second request carrying the token elsewhere."""
    respx.get(f"{BASE}/api/datasources").mock(
        return_value=httpx.Response(302, headers={"Location": "https://elsewhere.example.net/login"})
    )
    with pytest.raises(Failure):
        await grafana.datasources()
