# SPDX-License-Identifier: Apache-2.0
"""Read-only checks against a real console. They prove the wire format the fake imitates."""

from __future__ import annotations

from tranibor_lan_unifi.app import Runtime

from .conftest import missing


async def test_info_and_sites(live: Runtime) -> None:
    assert (await live.tools.get_info()).application_version
    sites = await live.tools.list_sites()
    assert sites.sites, "the console has at least one site"


async def test_devices_statistics_clients_and_networks(live: Runtime) -> None:
    site = (await live.tools.list_sites()).sites[0].id
    devices = await live.tools.list_devices(site)
    assert devices.devices
    first = devices.devices[0]
    assert (await live.tools.get_device(site, first.id)).id == first.id
    if first.state == "ONLINE":
        assert (await live.tools.get_device_statistics(site, first.id)).uptime_sec is not None
    await live.tools.list_clients(site)
    await live.tools.list_networks(site)
    drift = await live.tools.firmware_status(site)
    assert drift.devices_examined == devices.total


async def test_cloud_firmware_status(live: Runtime) -> None:
    if not live.settings.cloud_enabled:
        missing("TRANIBOR_LIVE_UNIFI_SITE_MANAGER_API_KEY")
    assert (await live.tools.cloud_firmware_status()).source == "unifi-site-manager-api"
