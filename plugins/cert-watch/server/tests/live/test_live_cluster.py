# SPDX-License-Identifier: Apache-2.0
"""Read-only checks against the real systems. NOT run in CI: they need credentials (see conftest)."""

from __future__ import annotations

from ..conftest import Mcp
from .conftest import env, need


async def test_certificates_list_and_expiry_report(live_mcp: Mcp) -> None:
    listed = await live_mcp.call("list_certificates", limit=5)
    assert listed["total"] >= 0 and isinstance(listed["certificates"], list)
    report = await live_mcp.call("expiry_report", days=30)
    assert "kubernetes" not in report["errors"], report["errors"]


async def test_one_certificate_with_its_chain(live_mcp: Mcp) -> None:
    listed = await live_mcp.call("list_certificates", limit=1)
    if not listed["certificates"]:
        return
    first = listed["certificates"][0]
    got = await live_mcp.call("get_certificate", namespace=first["namespace"], name=first["name"])
    assert got["certificate"]["name"] == first["name"]


async def test_failing_challenges_answers(live_mcp: Mcp) -> None:
    assert "challenges" in await live_mcp.call("list_failing_challenges")


async def test_crt_sh(live_mcp: Mcp) -> None:
    need("DOMAINS")
    domain = env("DOMAINS").split(",")[0].strip()
    result = await live_mcp.call("ct_search", domain=domain)
    assert result["total"] >= 0 and "entries" in result


async def test_probe(live_mcp: Mcp) -> None:
    need("PROBE_HOSTS")
    host = env("PROBE_HOSTS").split(",")[0].strip()
    result = await live_mcp.call("probe_tls", host=host)
    assert result["days_remaining"] is not None
