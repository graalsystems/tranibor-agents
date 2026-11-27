# SPDX-License-Identifier: Apache-2.0
"""The tools: what the two UniFi APIs expose, minimised and typed. They are the `lan/lan@1` profile's
(plugins/lan/profiles/lan.yaml) plus the UniFi-only extras (get_info, vouchers, guest authorisation, the
cloud firmware view), which agents of the profile never see.

The governed writes the official Integration API supports: restart a device, power-cycle a PoE port,
authorise or unauthorise a guest, create or revoke hotspot vouchers. The profile also has `block_client`,
`unblock_client` and `upgrade_firmware`: the official API has no such call, so they answer `not_supported`
and `get_provider` says so (`unsupported_tools`); no undocumented endpoint is used in their place.

What the official API does NOT expose, and so what no tool here invents (OpenAPI of Network 10.1.68):
alarms and events, the list of firmware versions available, a wireless client's signal strength or SSID
(a radio's retry rate, channel and width are), and the edit of an SSID or a VLAN through a governed action
(the API has the verbs, this server does not serve them: a change to the wireless or network design is
written as a plan in the agent's report, never applied by a call).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .client import Controller, SiteManager
from .errors import Failure, invalid

LOCAL = "unifi-network-integration-api"
CLOUD = "unifi-site-manager-api"
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_LIMIT = 500
#: Profile tools the official Integration API has no call for (OpenAPI of Network 10.1.68).
UNSUPPORTED = ("block_client", "unblock_client", "upgrade_firmware")
DeviceState = Literal[
    "ONLINE",
    "OFFLINE",
    "PENDING_ADOPTION",
    "UPDATING",
    "GETTING_READY",
    "ADOPTING",
    "DELETING",
    "CONNECTION_INTERRUPTED",
    "ISOLATED",
    "U5G_INCORRECT_TOPOLOGY",
]


class Result(BaseModel):
    source: str
    as_of: str


# -- reads ------------------------------------------------------------------------------------------------


class Info(Result):
    application_version: str


class Site(BaseModel):
    id: str
    name: str
    internal_reference: str


class Sites(Result):
    total: int
    sites: list[Site]


class DeviceSummary(BaseModel):
    id: str
    name: str
    model: str
    mac_address: str
    ip_address: str
    state: str
    firmware_version: str | None = None
    firmware_updatable: bool
    supported: bool
    features: list[str] = Field(default_factory=list)


class Devices(Result):
    site_id: str
    total: int
    truncated: bool
    devices: list[DeviceSummary]


class PoE(BaseModel):
    standard: str | None = None
    type: int | None = None
    enabled: bool | None = None
    state: str | None = None


class Port(BaseModel):
    idx: int
    state: str | None = None
    connector: str | None = None
    max_speed_mbps: int | None = None
    speed_mbps: int | None = None
    poe: PoE | None = None


class Radio(BaseModel):
    wlan_standard: str | None = None
    frequency_ghz: float | None = None
    channel: int | None = None
    channel_width_mhz: int | None = None


class Device(Result):
    site_id: str
    device: DeviceSummary
    adopted_at: str | None = None
    provisioned_at: str | None = None
    uplink_device_id: str | None = None
    ports: list[Port]
    radios: list[Radio]


class RadioStatistics(BaseModel):
    frequency_ghz: float | None = None
    tx_retries_pct: float | None = None


class DeviceStatistics(Result):
    site_id: str
    device_id: str
    uptime_sec: int | None = None
    last_heartbeat_at: str | None = None
    next_heartbeat_at: str | None = None
    cpu_utilization_pct: float | None = None
    memory_utilization_pct: float | None = None
    load_average_1m: float | None = None
    load_average_5m: float | None = None
    load_average_15m: float | None = None
    uplink_tx_rate_bps: int | None = None
    uplink_rx_rate_bps: int | None = None
    radios: list[RadioStatistics]


class ClientSummary(BaseModel):
    id: str
    name: str
    type: str
    ip_address: str | None = None
    mac_address: str | None = None
    connected_at: str | None = None
    uplink_device_id: str | None = None
    access_type: str | None = None
    guest_authorized: bool | None = None


class Clients(Result):
    site_id: str
    total: int
    truncated: bool
    clients: list[ClientSummary]


class GuestAuthorization(BaseModel):
    authorized_at: str | None = None
    expires_at: str | None = None
    method: str | None = None
    data_usage_limit_mbytes: int | None = None
    rx_rate_limit_kbps: int | None = None
    tx_rate_limit_kbps: int | None = None


class Client(Result):
    site_id: str
    client: ClientSummary
    guest_authorization: GuestAuthorization | None = None


class Network(BaseModel):
    id: str
    name: str
    management: str
    enabled: bool
    vlan_id: int
    origin: str | None = None


class WifiBroadcast(BaseModel):
    id: str
    name: str
    type: str | None = None
    enabled: bool
    security_type: str | None = None
    frequencies_ghz: list[float] = Field(default_factory=list)
    origin: str | None = None


class Networks(Result):
    site_id: str
    networks: list[Network]


class Wlans(Result):
    site_id: str
    wlans: list[WifiBroadcast]
    note: str = (
        "Read only. A passphrase is never returned. Changing an SSID or a VLAN is a change plan in the report, "
        "not a tool."
    )


class ModelFirmware(BaseModel):
    model: str
    devices: int
    versions: dict[str, int]
    updatable: int


class FirmwareDrift(Result):
    site_id: str
    devices_examined: int
    updatable: list[DeviceSummary]
    offline: list[DeviceSummary]
    by_model: list[ModelFirmware]
    note: str = (
        "The Integration API says only whether an update is available for a device (`firmware_updatable`), "
        "never to which version: the target version is for the console's own screen, or the cloud tool."
    )


class Voucher(BaseModel):
    id: str
    name: str
    code: str | None = None
    time_limit_minutes: int
    authorized_guest_limit: int | None = None
    authorized_guest_count: int
    data_usage_limit_mbytes: int | None = None
    expired: bool
    created_at: str | None = None
    expires_at: str | None = None


class Vouchers(Result):
    site_id: str
    total: int
    vouchers: list[Voucher]
    note: str = "Codes are withheld from a listing: only creating vouchers returns them."


class CloudDevice(BaseModel):
    host_id: str
    host_name: str | None = None
    id: str
    name: str | None = None
    model: str | None = None
    ip: str | None = None
    status: str | None = None
    version: str | None = None
    firmware_status: str | None = None
    update_available: str | None = None
    is_console: bool = False


class CloudFirmware(Result):
    hosts: int
    devices_examined: int
    needing_update: list[CloudDevice]
    offline: list[CloudDevice]
    note: str = "`firmware_status` and `update_available` are the Site Manager's, per device of every console."


class Provider(Result):
    instance: str
    provider: str
    sites: list[Site]
    unsupported_tools: list[str]
    note: str = "The tools in `unsupported_tools` answer not_supported: the vendor's API has no such call."


# -- writes -----------------------------------------------------------------------------------------------


class ActionResult(Result):
    site_id: str
    action: str
    target: str
    target_name: str | None = None
    previous_state: str | None = None
    accepted: bool = True


class GuestAccess(Result):
    site_id: str
    client_id: str
    client_name: str | None = None
    action: str
    granted: GuestAuthorization | None = None
    revoked: GuestAuthorization | None = None


class ClientBlock(Result):
    site_id: str
    client_id: str
    blocked: bool


class FirmwareUpgrade(Result):
    site_id: str
    device_id: str
    accepted: bool


class VoucherBatch(Result):
    site_id: str
    #: The name the vouchers carry on the console: the request's name and the idempotency key.
    voucher_name: str
    reused: bool
    count: int
    vouchers: list[Voucher]


class VoucherRevocation(Result):
    site_id: str
    voucher_name: str
    vouchers_deleted: int


# -- mapping ----------------------------------------------------------------------------------------------


def _s(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    return value if isinstance(value, str) else None


def _i(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _f(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _b(row: dict[str, Any], key: str) -> bool | None:
    value = row.get(key)
    return value if isinstance(value, bool) else None


def _uuid(name: str, value: str) -> str:
    if not UUID.match(value):
        raise invalid(f"{name} must be a UUID as the console lists it")
    return value.lower()


def _limit(limit: int) -> int:
    if not 1 <= limit <= MAX_LIMIT:
        raise invalid(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def _quote(value: str) -> str:
    """A string of the API's filter language: single quotes, a quote doubled."""
    return "'" + value.replace("'", "''") + "'"


def _device(row: dict[str, Any]) -> DeviceSummary:
    features = row.get("features")
    return DeviceSummary(
        id=_s(row, "id") or "",
        name=_s(row, "name") or "",
        model=_s(row, "model") or "",
        mac_address=_s(row, "macAddress") or "",
        ip_address=_s(row, "ipAddress") or "",
        state=_s(row, "state") or "UNKNOWN",
        firmware_version=_s(row, "firmwareVersion"),
        firmware_updatable=_b(row, "firmwareUpdatable") or False,
        supported=_b(row, "supported") if _b(row, "supported") is not None else True,
        features=[f for f in features if isinstance(f, str)] if isinstance(features, list) else [],
    )


def _authorization(row: Any) -> GuestAuthorization | None:
    if not isinstance(row, dict):
        return None
    return GuestAuthorization(
        authorized_at=_s(row, "authorizedAt"),
        expires_at=_s(row, "expiresAt"),
        method=_s(row, "authorizationMethod"),
        data_usage_limit_mbytes=_i(row, "dataUsageLimitMBytes"),
        rx_rate_limit_kbps=_i(row, "rxRateLimitKbps"),
        tx_rate_limit_kbps=_i(row, "txRateLimitKbps"),
    )


def _client(row: dict[str, Any]) -> ClientSummary:
    access = row.get("access")
    access = access if isinstance(access, dict) else {}
    return ClientSummary(
        id=_s(row, "id") or "",
        name=_s(row, "name") or "",
        type=_s(row, "type") or "UNKNOWN",
        ip_address=_s(row, "ipAddress"),
        mac_address=_s(row, "macAddress"),
        connected_at=_s(row, "connectedAt"),
        uplink_device_id=_s(row, "uplinkDeviceId"),
        access_type=_s(access, "type"),
        guest_authorized=_b(access, "authorized"),
    )


def _voucher(row: dict[str, Any], *, with_code: bool) -> Voucher:
    return Voucher(
        id=_s(row, "id") or "",
        name=_s(row, "name") or "",
        code=_s(row, "code") if with_code else None,
        time_limit_minutes=_i(row, "timeLimitMinutes") or 0,
        authorized_guest_limit=_i(row, "authorizedGuestLimit"),
        authorized_guest_count=_i(row, "authorizedGuestCount") or 0,
        data_usage_limit_mbytes=_i(row, "dataUsageLimitMBytes"),
        expired=_b(row, "expired") or False,
        created_at=_s(row, "createdAt"),
        expires_at=_s(row, "expiresAt"),
    )


def _origin(row: dict[str, Any]) -> str | None:
    metadata = row.get("metadata")
    return _s(metadata, "origin") if isinstance(metadata, dict) else None


class Tools:
    def __init__(
        self,
        controller: Controller,
        cloud: SiteManager | None = None,
        clock: Callable[[], datetime] | None = None,
        instance: str = "lan-unifi",
    ) -> None:
        self.instance = instance
        self.controller = controller
        self.cloud = cloud
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> str:
        return self.clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _cloud(self) -> SiteManager:
        if self.cloud is None:
            raise Failure(
                "not_configured", "the Site Manager (cloud) API is not configured on this connector: local data only"
            )
        return self.cloud

    # -- reads ----------------------------------------------------------------------------------------

    async def get_provider(self) -> Provider:
        rows, _ = await self.controller.collect("/v1/sites", limit=MAX_LIMIT)
        sites = [
            Site(id=_s(r, "id") or "", name=_s(r, "name") or "", internal_reference=_s(r, "internalReference") or "")
            for r in rows
        ]
        return Provider(
            source=LOCAL,
            as_of=self._now(),
            instance=self.instance,
            provider="unifi",
            sites=sites,
            unsupported_tools=list(UNSUPPORTED),
        )

    async def get_info(self) -> Info:
        body = await self.controller.get("/v1/info")
        return Info(source=LOCAL, as_of=self._now(), application_version=_s(body, "applicationVersion") or "")

    async def list_sites(self, limit: int = 100) -> Sites:
        rows, total = await self.controller.collect("/v1/sites", limit=_limit(limit))
        sites = [
            Site(id=_s(r, "id") or "", name=_s(r, "name") or "", internal_reference=_s(r, "internalReference") or "")
            for r in rows
        ]
        return Sites(source=LOCAL, as_of=self._now(), total=total, sites=sites)

    async def _devices(self, site: str, limit: int) -> tuple[list[dict[str, Any]], int]:
        return await self.controller.collect(f"/v1/sites/{site}/devices", limit=_limit(limit))

    async def list_devices(self, site_id: str, state: DeviceState | None = None, limit: int = 200) -> Devices:
        site = _uuid("site_id", site_id)
        rows, total = await self._devices(site, limit)
        devices = [d for d in map(_device, rows) if state is None or d.state == state]
        return Devices(
            source=LOCAL, as_of=self._now(), site_id=site, total=total, truncated=len(rows) < total, devices=devices
        )

    async def get_device(self, site_id: str, device_id: str) -> Device:
        site, dev = _uuid("site_id", site_id), _uuid("device_id", device_id)
        row = await self.controller.get(f"/v1/sites/{site}/devices/{dev}")
        interfaces = row.get("interfaces")
        interfaces = interfaces if isinstance(interfaces, dict) else {}
        ports: list[Port] = []
        for p in interfaces.get("ports") or []:
            if not isinstance(p, dict) or _i(p, "idx") is None:
                continue
            poe = p.get("poe")
            ports.append(
                Port(
                    idx=_i(p, "idx") or 0,
                    state=_s(p, "state"),
                    connector=_s(p, "connector"),
                    max_speed_mbps=_i(p, "maxSpeedMbps"),
                    speed_mbps=_i(p, "speedMbps"),
                    poe=PoE(
                        standard=_s(poe, "standard"),
                        type=_i(poe, "type"),
                        enabled=_b(poe, "enabled"),
                        state=_s(poe, "state"),
                    )
                    if isinstance(poe, dict)
                    else None,
                )
            )
        radios = [
            Radio(
                wlan_standard=_s(r, "wlanStandard"),
                frequency_ghz=_f(r, "frequencyGHz"),
                channel=_i(r, "channel"),
                channel_width_mhz=_i(r, "channelWidthMHz"),
            )
            for r in interfaces.get("radios") or []
            if isinstance(r, dict)
        ]
        uplink = row.get("uplink")
        return Device(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            device=_device(row),
            adopted_at=_s(row, "adoptedAt"),
            provisioned_at=_s(row, "provisionedAt"),
            uplink_device_id=_s(uplink, "deviceId") if isinstance(uplink, dict) else None,
            ports=ports,
            radios=radios,
        )

    async def get_device_statistics(self, site_id: str, device_id: str) -> DeviceStatistics:
        site, dev = _uuid("site_id", site_id), _uuid("device_id", device_id)
        row = await self.controller.get(f"/v1/sites/{site}/devices/{dev}/statistics/latest")
        uplink = row.get("uplink")
        uplink = uplink if isinstance(uplink, dict) else {}
        interfaces = row.get("interfaces")
        radios = interfaces.get("radios") if isinstance(interfaces, dict) else None
        return DeviceStatistics(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            device_id=dev,
            uptime_sec=_i(row, "uptimeSec"),
            last_heartbeat_at=_s(row, "lastHeartbeatAt"),
            next_heartbeat_at=_s(row, "nextHeartbeatAt"),
            cpu_utilization_pct=_f(row, "cpuUtilizationPct"),
            memory_utilization_pct=_f(row, "memoryUtilizationPct"),
            load_average_1m=_f(row, "loadAverage1Min"),
            load_average_5m=_f(row, "loadAverage5Min"),
            load_average_15m=_f(row, "loadAverage15Min"),
            uplink_tx_rate_bps=_i(uplink, "txRateBps"),
            uplink_rx_rate_bps=_i(uplink, "rxRateBps"),
            radios=[
                RadioStatistics(frequency_ghz=_f(r, "frequencyGHz"), tx_retries_pct=_f(r, "txRetriesPct"))
                for r in radios or []
                if isinstance(r, dict)
            ],
        )

    async def list_clients(
        self, site_id: str, type: Literal["WIRED", "WIRELESS", "VPN", "TELEPORT"] | None = None, limit: int = 200
    ) -> Clients:
        site = _uuid("site_id", site_id)
        rows, total = await self.controller.collect(f"/v1/sites/{site}/clients", limit=_limit(limit))
        clients = [c for c in map(_client, rows) if type is None or c.type == type]
        return Clients(
            source=LOCAL, as_of=self._now(), site_id=site, total=total, truncated=len(rows) < total, clients=clients
        )

    async def get_client(self, site_id: str, client_id: str) -> Client:
        site, cid = _uuid("site_id", site_id), _uuid("client_id", client_id)
        row = await self.controller.get(f"/v1/sites/{site}/clients/{cid}")
        access = row.get("access")
        return Client(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            client=_client(row),
            guest_authorization=_authorization(access.get("authorization")) if isinstance(access, dict) else None,
        )

    async def list_networks(self, site_id: str) -> Networks:
        site = _uuid("site_id", site_id)
        nets, _ = await self.controller.collect(f"/v1/sites/{site}/networks", limit=MAX_LIMIT)
        networks = [
            Network(
                id=_s(n, "id") or "",
                name=_s(n, "name") or "",
                management=_s(n, "management") or "",
                enabled=_b(n, "enabled") or False,
                vlan_id=_i(n, "vlanId") or 0,
                origin=_origin(n),
            )
            for n in nets
        ]
        return Networks(source=LOCAL, as_of=self._now(), site_id=site, networks=networks)

    async def list_wlans(self, site_id: str) -> Wlans:
        site = _uuid("site_id", site_id)
        wifi, _ = await self.controller.collect(f"/v1/sites/{site}/wifi/broadcasts", limit=MAX_LIMIT)
        broadcasts: list[WifiBroadcast] = []
        for w in wifi:
            security = w.get("securityConfiguration")  # holds a passphrase in a detail: only its type is kept
            freqs = w.get("broadcastingFrequenciesGHz")
            broadcasts.append(
                WifiBroadcast(
                    id=_s(w, "id") or "",
                    name=_s(w, "name") or "",
                    type=_s(w, "type"),
                    enabled=_b(w, "enabled") or False,
                    security_type=_s(security, "type") if isinstance(security, dict) else None,
                    frequencies_ghz=[float(f) for f in freqs if isinstance(f, (int, float))]
                    if isinstance(freqs, list)
                    else [],
                    origin=_origin(w),
                )
            )
        return Wlans(source=LOCAL, as_of=self._now(), site_id=site, wlans=broadcasts)

    async def firmware_status(self, site_id: str) -> FirmwareDrift:
        site = _uuid("site_id", site_id)
        rows, _ = await self._devices(site, MAX_LIMIT)
        devices = [_device(r) for r in rows]
        by_model: dict[str, list[DeviceSummary]] = {}
        for d in devices:
            by_model.setdefault(d.model, []).append(d)
        return FirmwareDrift(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            devices_examined=len(devices),
            updatable=[d for d in devices if d.firmware_updatable],
            offline=[d for d in devices if d.state in ("OFFLINE", "CONNECTION_INTERRUPTED", "ISOLATED")],
            by_model=[
                ModelFirmware(
                    model=model,
                    devices=len(group),
                    versions=dict(Counter(d.firmware_version or "unknown" for d in group)),
                    updatable=sum(d.firmware_updatable for d in group),
                )
                for model, group in sorted(by_model.items())
            ],
        )

    async def list_vouchers(self, site_id: str, limit: int = 100) -> Vouchers:
        site = _uuid("site_id", site_id)
        rows, total = await self.controller.collect(f"/v1/sites/{site}/hotspot/vouchers", limit=_limit(limit))
        return Vouchers(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            total=total,
            vouchers=[_voucher(r, with_code=False) for r in rows],
        )

    async def cloud_firmware_status(self, host_id: str | None = None) -> CloudFirmware:
        cloud = self._cloud()
        params = {"hostIds[]": host_id} if host_id else None
        groups = await cloud.collect("/v1/devices", params)
        found: list[CloudDevice] = []
        for group in groups:
            for d in group.get("devices") or []:
                if not isinstance(d, dict):
                    continue
                update = d.get("updateAvailable")
                found.append(
                    CloudDevice(
                        host_id=_s(group, "hostId") or "",
                        host_name=_s(group, "hostName"),
                        id=_s(d, "id") or "",
                        name=_s(d, "name"),
                        model=_s(d, "model"),
                        ip=_s(d, "ip"),
                        status=_s(d, "status"),
                        version=_s(d, "version"),
                        firmware_status=_s(d, "firmwareStatus"),
                        update_available=update if isinstance(update, str) else None,
                        is_console=_b(d, "isConsole") or False,
                    )
                )
        return CloudFirmware(
            source=CLOUD,
            as_of=self._now(),
            hosts=len(groups),
            devices_examined=len(found),
            needing_update=[d for d in found if d.firmware_status not in (None, "upToDate") or d.update_available],
            offline=[d for d in found if d.status not in (None, "online")],
        )

    # -- writes ---------------------------------------------------------------------------------------

    async def restart_device(self, site_id: str, device_id: str) -> ActionResult:
        site, dev = _uuid("site_id", site_id), _uuid("device_id", device_id)
        before = await self.controller.get(f"/v1/sites/{site}/devices/{dev}")  # also proves it exists
        await self.controller.request("POST", f"/v1/sites/{site}/devices/{dev}/actions", body={"action": "RESTART"})
        return ActionResult(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            action="RESTART",
            target=dev,
            target_name=_s(before, "name"),
            previous_state=_s(before, "state"),
        )

    async def power_cycle_port(self, site_id: str, device_id: str, port_idx: int) -> ActionResult:
        site, dev = _uuid("site_id", site_id), _uuid("device_id", device_id)
        if not 0 <= port_idx <= 1024:
            raise invalid("port_idx must be a port index of the device")
        device = await self.get_device(site, dev)
        port = next((p for p in device.ports if p.idx == port_idx), None)
        if port is None:
            raise invalid(f"the device has no port {port_idx}")
        if port.poe is None or port.poe.enabled is False:
            raise invalid(f"port {port_idx} has no PoE enabled: there is nothing to power-cycle")
        await self.controller.request(
            "POST",
            f"/v1/sites/{site}/devices/{dev}/interfaces/ports/{port_idx}/actions",
            body={"action": "POWER_CYCLE"},
        )
        return ActionResult(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            action="POWER_CYCLE",
            target=f"{dev}/ports/{port_idx}",
            target_name=device.device.name,
            previous_state=port.poe.state,
        )

    async def _guest(self, site_id: str, client_id: str, body: dict[str, Any]) -> GuestAccess:
        site, cid = _uuid("site_id", site_id), _uuid("client_id", client_id)
        before = await self.controller.get(f"/v1/sites/{site}/clients/{cid}")
        answer = await self.controller.request("POST", f"/v1/sites/{site}/clients/{cid}/actions", body=body)
        answer = answer if isinstance(answer, dict) else {}
        return GuestAccess(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            client_id=cid,
            client_name=_s(before, "name"),
            action=str(body["action"]),
            granted=_authorization(answer.get("grantedAuthorization")),
            revoked=_authorization(answer.get("revokedAuthorization")),
        )

    async def authorize_guest(
        self,
        site_id: str,
        client_id: str,
        time_limit_minutes: int | None = None,
        data_usage_limit_mbytes: int | None = None,
        rx_rate_limit_kbps: int | None = None,
        tx_rate_limit_kbps: int | None = None,
    ) -> GuestAccess:
        body: dict[str, Any] = {"action": "AUTHORIZE_GUEST_ACCESS"}
        for key, value, low, high in (
            ("timeLimitMinutes", time_limit_minutes, 1, 1_000_000),
            ("dataUsageLimitMBytes", data_usage_limit_mbytes, 1, 1_048_576),
            ("rxRateLimitKbps", rx_rate_limit_kbps, 2, 100_000),
            ("txRateLimitKbps", tx_rate_limit_kbps, 2, 100_000),
        ):
            if value is not None:
                if not low <= value <= high:
                    raise invalid(f"{key} must be between {low} and {high}")
                body[key] = value
        return await self._guest(site_id, client_id, body)

    async def unauthorize_guest(self, site_id: str, client_id: str) -> GuestAccess:
        return await self._guest(site_id, client_id, {"action": "UNAUTHORIZE_GUEST_ACCESS"})

    async def create_vouchers(
        self,
        site_id: str,
        name: str,
        time_limit_minutes: int,
        count: int = 1,
        authorized_guest_limit: int | None = None,
        data_usage_limit_mbytes: int | None = None,
        rx_rate_limit_kbps: int | None = None,
        tx_rate_limit_kbps: int | None = None,
        action_key: str = "",
    ) -> VoucherBatch:
        site = _uuid("site_id", site_id)
        if not name.strip() or len(name) > 100:
            raise invalid("name must be 1 to 100 characters: it is the note printed with the vouchers")
        if not 1 <= time_limit_minutes <= 1_000_000:
            raise invalid("time_limit_minutes must be between 1 and 1000000")
        if not 1 <= count <= 100:
            raise invalid("count must be between 1 and 100")
        key = re.sub(r"[^A-Za-z0-9_-]", "", action_key)[:16]
        tagged = f"{name.strip()} [{key}]" if key else name.strip()
        if key:  # the API has no idempotency key: a retry finds what the first attempt created
            found, _ = await self.controller.collect(
                f"/v1/sites/{site}/hotspot/vouchers", {"filter": f"name.eq({_quote(tagged)})"}, limit=MAX_LIMIT
            )
            if found:
                return VoucherBatch(
                    source=LOCAL,
                    as_of=self._now(),
                    site_id=site,
                    voucher_name=tagged,
                    reused=True,
                    count=len(found),
                    vouchers=[_voucher(v, with_code=True) for v in found],
                )
        body: dict[str, Any] = {"name": tagged, "count": count, "timeLimitMinutes": time_limit_minutes}
        for api_key, value, low, high in (
            ("authorizedGuestLimit", authorized_guest_limit, 1, 1_000_000),
            ("dataUsageLimitMBytes", data_usage_limit_mbytes, 1, 1_048_576),
            ("rxRateLimitKbps", rx_rate_limit_kbps, 2, 100_000),
            ("txRateLimitKbps", tx_rate_limit_kbps, 2, 100_000),
        ):
            if value is not None:
                if not low <= value <= high:
                    raise invalid(f"{api_key} must be between {low} and {high}")
                body[api_key] = value
        answer = await self.controller.request("POST", f"/v1/sites/{site}/hotspot/vouchers", body=body)
        rows = answer.get("vouchers") if isinstance(answer, dict) else None
        vouchers = [_voucher(v, with_code=True) for v in rows or [] if isinstance(v, dict)]
        return VoucherBatch(
            source=LOCAL,
            as_of=self._now(),
            site_id=site,
            voucher_name=tagged,
            reused=False,
            count=len(vouchers),
            vouchers=vouchers,
        )

    async def revoke_vouchers(self, site_id: str, voucher_name: str) -> VoucherRevocation:
        """Deletes the vouchers of one name. It is `create_vouchers`'s compensation: the name is the tagged one."""
        site = _uuid("site_id", site_id)
        if not voucher_name.strip():
            raise invalid("voucher_name is required")
        answer = await self.controller.request(
            "DELETE", f"/v1/sites/{site}/hotspot/vouchers", params={"filter": f"name.eq({_quote(voucher_name)})"}
        )
        deleted = _i(answer, "vouchersDeleted") if isinstance(answer, dict) else None
        return VoucherRevocation(
            source=LOCAL, as_of=self._now(), site_id=site, voucher_name=voucher_name, vouchers_deleted=deleted or 0
        )

    # -- profile tools the official API cannot serve --------------------------------------------------

    @staticmethod
    def _not_supported(tool: str, why: str) -> Failure:
        return Failure("not_supported", f"{tool}: {why}; nothing was sent to the console")

    async def block_client(self, site_id: str, client_id: str) -> ClientBlock:
        raise self._not_supported(
            "block_client", "the Integration API offers only guest authorisation on a client (extras authorize_guest)"
        )

    async def unblock_client(self, site_id: str, client_id: str) -> ClientBlock:
        raise self._not_supported("unblock_client", "the Integration API has no block or unblock of a client")

    async def upgrade_firmware(self, site_id: str, device_id: str) -> FirmwareUpgrade:
        raise self._not_supported(
            "upgrade_firmware", "the Integration API reports `firmware_updatable` but has no upgrade action"
        )
