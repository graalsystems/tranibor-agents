# SPDX-License-Identifier: Apache-2.0
"""The six tools on the seeded fakes, through the MCP protocol: the situations the seed holds are the ones
the agent has to explain (see `fake.py`)."""

from __future__ import annotations

from tranibor_cert_watch.fake import FakeKube

from .conftest import Mcp


async def test_list_certificates_orders_by_expiry_and_reports_the_ready_condition(mcp: Mcp) -> None:
    result = await mcp.call("list_certificates")
    assert [(c["namespace"], c["name"]) for c in result["certificates"]] == [
        ("legacy", "legacy-tls"),
        ("shop", "api-tls"),
        ("shop", "shop-tls"),
        ("mail", "mail-wildcard-tls"),  # never issued: no notAfter, last
    ]
    assert result["total"] == 4 and result["truncated"] is False
    by_name = {c["name"]: c for c in result["certificates"]}
    assert by_name["api-tls"]["days_remaining"] == 9
    assert by_name["api-tls"]["renewal_overdue"] is True and by_name["api-tls"]["issuing"] is True
    assert by_name["api-tls"]["failed_issuance_attempts"] == 3
    assert by_name["shop-tls"]["renewal_overdue"] is False and by_name["shop-tls"]["days_remaining"] == 60
    assert by_name["mail-wildcard-tls"]["ready"]["reason"] == "DoesNotExist"
    assert by_name["mail-wildcard-tls"]["not_after"] is None
    assert by_name["shop-tls"]["issuer"] == {
        "name": "letsencrypt-prod",
        "kind": "ClusterIssuer",
        "group": "cert-manager.io",
    }


async def test_list_certificates_filters(mcp: Mcp) -> None:
    not_ready = await mcp.call("list_certificates", ready=False)
    assert [c["name"] for c in not_ready["certificates"]] == ["mail-wildcard-tls"]
    shop = await mcp.call("list_certificates", namespace="shop", limit=1)
    assert shop["total"] == 2 and shop["truncated"] is True and len(shop["certificates"]) == 1


async def test_get_certificate_follows_the_chain_to_the_challenge(mcp: Mcp) -> None:
    result = await mcp.call("get_certificate", namespace="shop", name="api-tls")
    assert result["certificate"]["name"] == "api-tls"
    assert [r["name"] for r in result["certificate_requests"]] == ["api-tls-5"]
    assert result["certificate_requests"][0]["revision"] == "5"
    (order,) = result["orders"]
    assert order["state"] == "pending" and order["authorizations"][0]["identifier"] == "api.shop.example.com"
    (challenge,) = result["challenges"]
    assert challenge["type"] == "HTTP-01" and "404" in challenge["reason"]
    assert challenge["solver"] == {
        "kind": "http01",
        "mechanism": "ingress",
        "ingress_class": "nginx",
        "ingress_name": None,
        "service_type": None,
    }
    assert "http01_wrong_status" in result["signals"]
    assert result["issuer"]["type"] == "acme" and result["issuer"]["ready"]["status"] == "True"
    # the challenge's token and key are never returned
    assert "fake-token-not-real" not in str(result) and "fake-key-not-real" not in str(result)


async def test_get_certificate_without_a_chain_has_empty_lists(mcp: Mcp) -> None:
    result = await mcp.call("get_certificate", namespace="shop", name="shop-tls")
    assert result["certificate_requests"] == [] and result["orders"] == [] and result["challenges"] == []
    assert result["signals"] == []


async def test_get_certificate_unknown_is_an_error_not_a_crash(mcp: Mcp) -> None:
    assert "not found" in await mcp.error("get_certificate", namespace="shop", name="nope")


async def test_list_failing_challenges_names_the_certificate_of_each(mcp: Mcp) -> None:
    result = await mcp.call("list_failing_challenges")
    assert result["total"] == 2
    by_type = {c["type"]: c for c in result["challenges"]}
    assert by_type["HTTP-01"]["certificate"] == {"namespace": "shop", "name": "api-tls"}
    assert by_type["HTTP-01"]["order"]["state"] == "pending"
    assert by_type["DNS-01"]["certificate"] == {"namespace": "mail", "name": "mail-wildcard-tls"}
    assert by_type["DNS-01"]["signals"] == ["dns01_not_propagated"]
    assert by_type["DNS-01"]["solver"] == {"kind": "dns01", "providers": ["cloudDNS"]}
    assert by_type["DNS-01"]["wildcard"] is True
    only_mail = await mcp.call("list_failing_challenges", namespace="mail")
    assert [c["type"] for c in only_mail["challenges"]] == ["DNS-01"]


async def test_the_rate_limit_is_read_from_the_order(mcp: Mcp) -> None:
    result = await mcp.call("get_certificate", namespace="legacy", name="legacy-tls")
    (order,) = result["orders"]
    assert order["state"] == "errored" and "rate_limited" in order["signals"]
    assert "rate_limited" in result["signals"]
    assert result["challenges"] == []  # the order failed before any challenge


async def test_ct_search_marks_what_no_certificate_declares(mcp: Mcp) -> None:
    result = await mcp.call("ct_search", domain="example.com")
    assert result["declared_known"] is True and result["total"] == 5
    by_name = {e["names"][0]: e for e in result["entries"]}
    assert by_name["shop.example.com"]["declared"] is True
    assert by_name["shop.example.com"]["declared_by"] == ["shop/shop-tls"]
    assert by_name["vpn.example.com"]["declared"] is False
    assert by_name["vpn.example.com"]["unmatched_names"] == ["vpn.example.com"]
    assert by_name["vpn.example.com"]["issuer"].startswith("C=XX, O=Other Authority")
    assert by_name["staging.example.com"]["declared"] is False and by_name["staging.example.com"]["expired"] is True
    assert result["undeclared_total"] == 2
    assert result["undeclared_current"] == [1004]  # the expired one is not "current"
    only = await mcp.call("ct_search", domain="example.com", undeclared_only=True)
    assert {e["ct_id"] for e in only["entries"]} == {1004, 1005}


async def test_ct_search_of_a_subdomain_and_without_subdomains(mcp: Mcp, fake_ct: object) -> None:
    sub = await mcp.call("ct_search", domain="shop.example.com")
    assert {e["ct_id"] for e in sub["entries"]} == {1001, 1002}
    exact = await mcp.call("ct_search", domain="example.com", include_subdomains=False)
    assert exact["total"] == 0


async def test_ct_search_refuses_a_domain_nobody_configured_before_any_request(mcp: Mcp, fake_ct: object) -> None:
    for domain in ("evil.test", "example.com.evil.test", "notexample.com", "not a domain"):
        assert "refused" in await mcp.error("ct_search", domain=domain)
    assert fake_ct.queries == []  # type: ignore[attr-defined]


async def test_ct_search_still_answers_when_the_cluster_does_not(mcp: Mcp, fake_kube: FakeKube) -> None:
    fake_kube.store["certificates"].clear()
    # a cluster with no Certificate declares nothing: everything is undeclared, but it is known
    result = await mcp.call("ct_search", domain="example.com")
    assert result["declared_known"] is True and result["undeclared_total"] == 5


async def test_probe_tls_of_a_configured_host(mcp: Mcp) -> None:
    ok = await mcp.call("probe_tls", host="shop.example.com")
    assert ok["endpoint"] == "shop.example.com:443" and ok["days_remaining"] == 60
    assert ok["trusted"] is True and ok["chain_complete"] is True and ok["hostname_matches"] is True
    bad = await mcp.call("probe_tls", host="legacy.example.com:8443")
    assert bad["chain_complete"] is False and bad["trusted"] is False
    assert "issuer" in bad["trust_error"]
    assert "no TLS handshake" in await mcp.error("probe_tls", host="down.example.com")


async def test_probe_tls_refuses_what_is_not_configured(mcp: Mcp) -> None:
    for host in ("evil.test", "shop.example.com:22", "169.254.169.254", "127.0.0.1:443", "shop.example.com:abc", ""):
        assert "refused" in await mcp.error("probe_tls", host=host), host


async def test_expiry_report_puts_the_estate_in_one_call(mcp: Mcp) -> None:
    report = await mcp.call("expiry_report", days=30)
    assert [c["name"] for c in report["expiring"]] == ["legacy-tls", "api-tls"]
    assert [c["name"] for c in report["not_ready"]] == ["mail-wildcard-tls"]
    assert {c["name"] for c in report["renewal_failing"]} == {"api-tls", "legacy-tls"}
    assert report["certificates_total"] == 4 and report["errors"] == {}
    assert len(report["failing_challenges"]) == 2
    assert set(report["failing_signals"]) == {"http01_wrong_status", "dns01_not_propagated"}
    probes = {p["endpoint"]: p for p in report["probes"]}
    assert set(probes) == {
        "shop.example.com:443",
        "api.shop.example.com:443",
        "legacy.example.com:8443",
        "down.example.com:443",
    }
    assert "error" in probes["down.example.com:443"]
    findings = {(f["endpoint"], f["finding"]) for f in report["probe_findings"]}
    assert findings == {
        ("api.shop.example.com:443", "expires_within_window"),
        ("legacy.example.com:8443", "expires_within_window"),
        ("legacy.example.com:8443", "chain_incomplete"),
        ("legacy.example.com:8443", "not_trusted"),
        ("down.example.com:443", "unreachable"),
    }


async def test_expiry_report_window_and_bounds(mcp: Mcp) -> None:
    narrow = await mcp.call("expiry_report", days=5)
    assert [c["name"] for c in narrow["expiring"]] == ["legacy-tls"]
    wide = await mcp.call("expiry_report", days=90)
    assert {c["name"] for c in wide["expiring"]} == {"legacy-tls", "api-tls", "shop-tls"}
    for days in (0, -1, 4000):
        assert "refused" in await mcp.error("expiry_report", days=days)


async def test_expiry_report_degrades_when_the_cluster_refuses(mcp: Mcp, fake_kube: FakeKube) -> None:
    fake_kube.store.pop("certificates")
    report = await mcp.call("expiry_report")
    assert "kubernetes" in report["errors"] and report["expiring"] == []
    assert report["probes"]  # the probes still answered
