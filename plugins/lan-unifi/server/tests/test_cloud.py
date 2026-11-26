# SPDX-License-Identifier: Apache-2.0
"""The cloud connector mode: Ubiquiti's `api.ui.com/v1/connector/consoles/<id>/...`, the same tools, the
connector's limits (100 requests per minute per console, 25 s, 10 MB) kept under or reported clearly."""

from __future__ import annotations

import httpx
import pytest
import respx

from tranibor_lan_unifi.app import Runtime
from tranibor_lan_unifi.client import CLOUD_BODY_MAX, CLOUD_TIMEOUT_S, Controller, Throttle
from tranibor_lan_unifi.config import FAKE_CONSOLE_ID, Settings
from tranibor_lan_unifi.errors import Failure
from tranibor_lan_unifi.fake import SITE, FakeUnifi

CLOUD = "https://api.ui.com"
CONSOLE = "0A1B2C3D4E5F0000000000000000000000000000:1234567890"
KEY = "a-site-manager-key-for-tests-only"
API = f"{CLOUD}/v1/connector/consoles/{CONSOLE}/network/integration"
ENV = {
    "UNIFI_CONNECTION": "cloud",
    "UNIFI_CONSOLE_ID": CONSOLE,
    "UNIFI_SITE_MANAGER_API_KEY": KEY,
    "UNIFI_SITE_MANAGER_URL": CLOUD,
}


def cloud(throttle: Throttle | None = None) -> Controller:
    """With the prefix already known: the probe has its own tests below."""
    controller = Controller(CLOUD, KEY, console_id=CONSOLE, throttle=throttle)
    controller.prefix = "/network/integration"
    return controller


@respx.mock
async def test_the_connector_path_and_the_site_manager_header_carry_the_same_paths() -> None:
    route = respx.get(f"{API}/v1/sites").mock(
        return_value=httpx.Response(200, json={"offset": 0, "limit": 200, "count": 0, "totalCount": 0, "data": []})
    )
    await cloud().collect("/v1/sites")
    request = route.calls.last.request
    assert request.headers["X-API-Key"] == KEY
    assert "proxy" not in request.url.path  # the connector adds `/proxy/` itself
    assert request.url.path == f"/v1/connector/consoles/{CONSOLE}/network/integration/v1/sites"


@pytest.mark.parametrize("bad", ["", "x", "has space/../x", "a/b", "../../v1/hosts", "id?x=1"])
def test_a_console_id_that_is_not_one_is_refused_before_any_call(bad: str) -> None:
    with pytest.raises(Failure) as failure:
        Controller(CLOUD, KEY, console_id=bad)
    assert failure.value.code == "invalid_arguments"


@respx.mock
async def test_a_429_is_said_and_never_carries_the_key_nor_the_body() -> None:
    respx.get(f"{API}/v1/info").mock(return_value=httpx.Response(429, json={"message": f"secret-{KEY}"}))
    with pytest.raises(Failure) as failure:
        await cloud().get("/v1/info")
    assert failure.value.code == "rate_limited"
    assert KEY not in str(failure.value) and "secret" not in str(failure.value)


@respx.mock
@pytest.mark.parametrize("status", [408, 504])
async def test_the_connectors_timeout_is_a_timeout(status: int) -> None:
    respx.get(f"{API}/v1/info").mock(return_value=httpx.Response(status, json={"message": "timeout"}))
    with pytest.raises(Failure) as failure:
        await cloud().get("/v1/info")
    assert failure.value.code == "timeout"


@respx.mock
async def test_our_own_timeout_is_under_the_connectors_25_seconds() -> None:
    assert CLOUD_TIMEOUT_S < 25
    assert cloud().timeout == CLOUD_TIMEOUT_S
    respx.get(f"{API}/v1/info").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(Failure) as failure:
        await cloud().get("/v1/info")
    assert failure.value.code == "timeout"


@respx.mock
async def test_a_body_above_10_mb_is_response_too_large() -> None:
    respx.get(f"{API}/v1/info").mock(return_value=httpx.Response(200, content=b" " * (CLOUD_BODY_MAX + 1)))
    with pytest.raises(Failure) as failure:
        await cloud().get("/v1/info")
    assert failure.value.code == "response_too_large"
    respx.get(f"{API}/v1/info").mock(return_value=httpx.Response(413))
    with pytest.raises(Failure) as refused:
        await cloud().get("/v1/info")
    assert refused.value.code == "response_too_large"


@respx.mock
async def test_the_throttle_waits_for_a_slot_then_fails_without_sending_when_it_would_wait_too_long() -> None:
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    throttle = Throttle(rate=3, window=60.0, max_wait=20.0, monotonic=lambda: now[0], sleep=sleep)
    route = respx.get(f"{API}/v1/info").mock(return_value=httpx.Response(200, json={}))
    controller = cloud(throttle)
    for second in (0.0, 1.0, 2.0):
        now[0] = second
        await controller.get("/v1/info")
    assert route.call_count == 3 and slept == []
    now[0] = 50.0  # the oldest request leaves the window at 60 s: 10 s to wait, under the 20 s allowed
    await controller.get("/v1/info")
    assert slept == [10.0] and route.call_count == 4
    now[0] = 61.0  # the window holds 2.0, 60.0 and now 61.0: the next slot is 1 s away, more than allowed here
    throttle.max_wait = 0.5
    await controller.get("/v1/info")
    before = route.call_count
    with pytest.raises(Failure) as failure:
        await controller.get("/v1/info")
    assert failure.value.code == "rate_limited" and "nothing was sent" in str(failure.value)
    assert route.call_count == before


def test_the_default_rate_is_under_the_connectors_100_per_minute() -> None:
    assert Throttle().rate < 100


def test_cloud_settings_need_the_console_id_and_the_site_manager_key_but_no_console_address() -> None:
    settings = Settings.from_env(ENV)
    assert (settings.connection, settings.console_id, settings.site_manager_url) == ("cloud", CONSOLE, CLOUD)
    assert settings.cloud_enabled
    for missing in ("UNIFI_CONSOLE_ID", "UNIFI_SITE_MANAGER_API_KEY", "UNIFI_SITE_MANAGER_URL"):
        with pytest.raises(ValueError, match="needs UNIFI_CONSOLE_ID"):
            Settings.from_env({k: v for k, v in ENV.items() if k != missing})
    with pytest.raises(ValueError, match="not a console id"):
        Settings.from_env({**ENV, "UNIFI_CONSOLE_ID": "../x"})
    with pytest.raises(ValueError, match="local or cloud"):
        Settings.from_env({**ENV, "UNIFI_CONNECTION": "wan"})


def test_local_stays_the_default() -> None:
    assert Settings.from_env({"LAN_UNIFI_FAKE": "1"}).connection == "local"


async def test_the_tools_reach_the_console_only_through_api_ui_com_in_cloud_mode() -> None:
    runtime = Runtime(Settings.from_env({"LAN_UNIFI_FAKE": "1", "UNIFI_CONNECTION": "cloud"}))
    fake = runtime.fake
    assert isinstance(fake, FakeUnifi)
    assert (await runtime.tools.get_provider()).sites
    await runtime.tools.list_devices(site_id=SITE)
    assert fake.calls
    prefix = f"/v1/connector/consoles/{FAKE_CONSOLE_ID}/network/integration/v1/"
    assert all(path.startswith(prefix) for _, path, _ in fake.calls)


CONNECTOR = f"{CLOUD}/v1/connector/consoles/{CONSOLE}"
DIRECT, PROXIED = f"{CONNECTOR}/network/integration", f"{CONNECTOR}/proxy/network/integration"
PAGE = {"offset": 0, "limit": 200, "count": 0, "totalCount": 0, "data": []}


@respx.mock
async def test_the_first_call_probes_info_without_proxy_and_keeps_that_prefix() -> None:
    direct = respx.get(f"{DIRECT}/v1/info").mock(return_value=httpx.Response(200, json={}))
    other = respx.get(f"{PROXIED}/v1/info").mock(return_value=httpx.Response(200, json={}))
    sites = respx.get(f"{DIRECT}/v1/sites").mock(return_value=httpx.Response(200, json=PAGE))
    controller = Controller(CLOUD, KEY, console_id=CONSOLE)
    await controller.collect("/v1/sites")
    await controller.collect("/v1/sites")
    assert (direct.call_count, other.call_count, sites.call_count) == (1, 0, 2)
    assert controller.prefix == "/network/integration"


@respx.mock
async def test_a_404_on_the_first_path_probes_the_proxy_path_and_keeps_it(caplog: pytest.LogCaptureFixture) -> None:
    direct = respx.get(f"{DIRECT}/v1/info").mock(return_value=httpx.Response(404))
    proxied = respx.get(f"{PROXIED}/v1/info").mock(return_value=httpx.Response(200, json={}))
    sites = respx.get(f"{PROXIED}/v1/sites").mock(return_value=httpx.Response(200, json=PAGE))
    controller = Controller(CLOUD, KEY, console_id=CONSOLE)
    with caplog.at_level("INFO", logger="tranibor_lan_unifi.client"):
        await controller.collect("/v1/sites")
        await controller.collect("/v1/sites")
    assert (direct.call_count, proxied.call_count, sites.call_count) == (1, 1, 2)
    assert controller.prefix == "/proxy/network/integration"
    assert "/proxy/network/integration" in caplog.text and KEY not in caplog.text


@respx.mock
async def test_neither_path_answering_is_a_typed_error_naming_both_and_nothing_else_is_sent() -> None:
    respx.get(f"{DIRECT}/v1/info").mock(return_value=httpx.Response(404))
    respx.get(f"{PROXIED}/v1/info").mock(return_value=httpx.Response(404))
    write = respx.post(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))
    controller = Controller(CLOUD, KEY, console_id=CONSOLE)
    with pytest.raises(Failure) as failure:
        await controller.request("POST", f"/v1/sites/{SITE}/devices/x/actions", body={"action": "RESTART"})
    assert failure.value.code == "not_found"
    assert "/network/integration/v1/info" in str(failure.value)
    assert "/proxy/network/integration/v1/info" in str(failure.value)
    assert KEY not in str(failure.value)
    assert write.call_count == 0 and controller.prefix is None


@respx.mock
async def test_a_401_on_the_probe_is_not_a_missing_path() -> None:
    respx.get(f"{DIRECT}/v1/info").mock(return_value=httpx.Response(401))
    proxied = respx.get(f"{PROXIED}/v1/info").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(Failure) as failure:
        await Controller(CLOUD, KEY, console_id=CONSOLE).get("/v1/sites")
    assert failure.value.code == "unauthorized" and proxied.call_count == 0
