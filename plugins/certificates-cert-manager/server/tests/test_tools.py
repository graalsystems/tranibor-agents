# SPDX-License-Identifier: Apache-2.0
"""The cert-manager reading of the profile, on the seeded fake with a fixed clock."""

from __future__ import annotations

import pytest

from tranibor_certificates_cert_manager.fake import FakeKube

from .conftest import Mcp


async def test_get_provider(mcp: Mcp) -> None:
    provider = await mcp.call("get_provider")
    assert provider["provider"] == "cert-manager" and provider["kind"] == "manager"
    assert provider["instance"] == "certificates-cert-manager"
    assert provider["scope"] == ["namespace shop", "namespace mail", "namespace legacy"]


async def test_the_four_seeded_situations_get_the_right_renewal_state(mcp: Mcp) -> None:
    listed = await mcp.call("list_certificates")
    by_id = {c["id"]: c for c in listed["certificates"]}
    assert {i: c["renewal"]["state"] for i, c in by_id.items()} == {
        "mail/mail-wildcard-tls": "pending",
        "legacy/legacy-tls": "failing",
        "shop/api-tls": "failing",
        "shop/shop-tls": "ok",
    }
    # Not issued first, then soonest expiry.
    assert [c["id"] for c in listed["certificates"]] == [
        "mail/mail-wildcard-tls",
        "legacy/legacy-tls",
        "shop/api-tls",
        "shop/shop-tls",
    ]
    assert by_id["shop/shop-tls"]["days_remaining"] == 60
    assert by_id["shop/shop-tls"]["renewal"]["manager"] == "cert-manager"
    assert by_id["shop/shop-tls"]["issuer"] == "ClusterIssuer/letsencrypt-prod"
    assert by_id["shop/shop-tls"]["location"] == "secret shop/shop-tls-secret"


async def test_get_certificate_says_what_the_renewal_is_waiting_on(mcp: Mcp) -> None:
    answer = await mcp.call("get_certificate", id="shop/api-tls")
    kinds = {a["kind"]: a for a in answer["renewal_attempts"]}
    assert set(kinds) == {"request", "order", "challenge", "issuer"}
    assert "wrong status code" in kinds["challenge"]["detail"]
    assert kinds["challenge"]["signals"] == ["http01_wrong_status"]
    assert kinds["issuer"]["state"] == "ready"


async def test_findings_carry_evidence_and_the_signals_of_the_text(mcp: Mcp) -> None:
    findings = (await mcp.call("list_findings", within_days=30))["findings"]
    kinds = {(f["certificate"], f["kind"]): f for f in findings}
    assert ("shop/api-tls", "renewal_failing") in kinds
    assert ("legacy/legacy-tls", "renewal_failing") in kinds
    assert kinds[("legacy/legacy-tls", "renewal_failing")]["signals"] == ["rate_limited"]
    assert kinds[("legacy/legacy-tls", "renewal_failing")]["severity"] == "critical"  # 4 days left
    assert ("mail/mail-wildcard-tls", "not_issued") in kinds
    assert not [f for f in findings if f["certificate"] == "shop/shop-tls"]
    assert all(f["evidence"] for f in findings)


@pytest.mark.parametrize("bad", ["shop", "/x", "shop/", "a/b/c"])
async def test_an_id_that_is_not_namespace_slash_name_is_refused(mcp: Mcp, bad: str) -> None:
    assert "not a certificate id" in await mcp.error("get_certificate", id=bad)


async def test_a_namespace_outside_the_allow_list_is_refused_without_a_request(mcp: Mcp, fake: FakeKube) -> None:
    before = len(fake.requests)
    assert "allow-list" in await mcp.error("get_certificate", id="kube-system/x")
    assert len(fake.requests) == before


async def test_only_get_requests_are_ever_sent(mcp: Mcp, fake: FakeKube) -> None:
    await mcp.call("list_findings")
    await mcp.call("get_certificate", id="shop/api-tls")
    assert {method for method, _ in fake.requests} == {"GET"}
