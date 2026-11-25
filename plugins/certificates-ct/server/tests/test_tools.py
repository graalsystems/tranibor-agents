# SPDX-License-Identifier: Apache-2.0
"""What is specific to a log: ids, superseded entries, a domain that did not answer, text clipped."""

from __future__ import annotations

import httpx
import respx

from tranibor_certificates_ct.app import Runtime, build_server
from tranibor_certificates_ct.config import Settings
from tranibor_certificates_ct.fake import FakeCt

from .conftest import Mcp


async def test_ids_are_the_log_s_and_renewal_is_unknown(mcp: Mcp) -> None:
    listed = await mcp.call("list_certificates")
    assert {c["id"] for c in listed["certificates"]} == {f"ct-{n}" for n in range(1001, 1007)}
    assert all(c["renewal"] == {**c["renewal"], "managed": False, "state": "unknown"} for c in listed["certificates"])
    provider = await mcp.call("get_provider")
    assert provider["kind"] == "log" and provider["scope"] == ["*.example.com"]


async def test_a_replaced_entry_is_not_an_alarm_but_says_what_replaced_it(mcp: Mcp) -> None:
    flagged = {f["certificate"] for f in (await mcp.call("list_findings", within_days=30))["findings"]}
    assert "ct-1006" not in flagged  # 10 days left, but ct-1001 covers shop.example.com until later
    assert {"ct-1002", "ct-1003", "ct-1005"} <= flagged
    one = await mcp.call("get_certificate", id="ct-1006")
    assert any("ct-1001" in note for note in one["notes"])


async def test_an_expired_entry_is_information_not_a_page(mcp: Mcp) -> None:
    found = (await mcp.call("list_findings", within_days=30, certificate="ct-1005"))["findings"]
    assert [(f["kind"], f["severity"]) for f in found] == [("expired", "info")]


async def test_an_id_that_is_not_the_log_s_is_refused_before_any_search(mcp: Mcp, fake: FakeCt) -> None:
    for bad in ("shop.example.com:443", "ct-", "ct-1; drop", "1001"):
        assert "configured" in await mcp.error("get_certificate", id=bad)
    assert fake.calls == 0


@respx.mock
async def test_a_busy_log_is_unavailable_and_a_finding_not_an_error() -> None:
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(503))
    server = build_server(Runtime(Settings.from_env({"CERTIFICATES_CT_DOMAINS": "example.com"})))
    mcp = Mcp(server)
    listed = await mcp.call("list_certificates")
    assert listed["total"] == 0 and listed["unavailable"][0]["id"] == "example.com"
    found = await mcp.call("list_findings")
    assert [f["kind"] for f in found["findings"]] == ["unreachable"]
    assert "could not search" in await mcp.error("get_certificate", id="ct-1")
