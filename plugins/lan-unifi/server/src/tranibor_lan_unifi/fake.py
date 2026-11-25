# SPDX-License-Identifier: Apache-2.0
"""A fake UniFi: an `httpx.MockTransport` handler answering the Integration API and the Site Manager API
from a seeded in-memory site, so the server runs, and its tests and evals run, without a console.

The seed is a small office, `Example Office`, invented, on documentation addresses (RFC 5737) and MAC
addresses (RFC 7042). Its story, which the evals rely on:

- `uap-room-12` (room 12) is online with 41 % transmit retries on 2.4 GHz and 8 % on 5 GHz: a busy
  channel, on firmware that has an update.
- `uap-room-14` (room 14) is OFFLINE, on port 5 of `usw-core`, a PoE port that is powered: a power-cycle
  candidate.
- `uap-lobby` is online and healthy but one firmware behind.
- `usw-core` is the switch every access point hangs on. `udm-gateway` is the gateway.
- `guest-phone` is a wireless guest, not yet authorised; `staff-laptop` is wireless, `nvr-1` is wired.

It answers what the real API answers in shape (pages with `offset`, `limit`, `count`, `totalCount`,
`data`), and applies the actions: a restart marks the device UPDATING, a power-cycle is recorded, a guest
authorisation sticks. `calls` records every request, so a test can say what was sent and what was not.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

SITE = "11111111-1111-4111-8111-111111111111"
GATEWAY = "aaaaaaaa-0000-4000-8000-000000000001"
SWITCH = "aaaaaaaa-0000-4000-8000-000000000002"
AP_ROOM_12 = "aaaaaaaa-0000-4000-8000-000000000012"
AP_ROOM_14 = "aaaaaaaa-0000-4000-8000-000000000014"
AP_LOBBY = "aaaaaaaa-0000-4000-8000-000000000010"
GUEST_PHONE = "cccccccc-0000-4000-8000-000000000001"
STAFF_LAPTOP = "cccccccc-0000-4000-8000-000000000002"
NVR = "cccccccc-0000-4000-8000-000000000003"
NET_DEFAULT = "dddddddd-0000-4000-8000-000000000001"
NET_GUEST = "dddddddd-0000-4000-8000-000000000002"
WIFI_STAFF = "eeeeeeee-0000-4000-8000-000000000001"
WIFI_GUEST = "eeeeeeee-0000-4000-8000-000000000002"
VOUCHER_1 = "ffffffff-0000-4000-8000-000000000001"
CLOUD_HOST = "9f00a000-0000-4000-8000-00000000c0de"
MISSING = "00000000-0000-4000-8000-000000000000"


def _device(
    id: str, name: str, model: str, mac: str, ip: str, state: str, version: str, updatable: bool, features: list[str]
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "model": model,
        "macAddress": mac,
        "ipAddress": ip,
        "state": state,
        "supported": True,
        "firmwareVersion": version,
        "firmwareUpdatable": updatable,
        "features": features,
        "interfaces": ["PORTS"] if "switching" in features else ["RADIOS"],
    }


def seed() -> dict[str, Any]:
    switch_ports = [
        {"idx": i, "state": "UP" if i in (1, 2, 3, 5) else "DOWN", "connector": "RJ45", "maxSpeedMbps": 1000,
         "speedMbps": 1000 if i in (1, 2, 3, 5) else None,
         "poe": {"standard": "802.3at", "type": 2, "enabled": True, "state": "UP" if i in (2, 3, 5) else "DOWN"}}
        for i in range(1, 9)
    ]  # fmt: skip
    return {
        "info": {"applicationVersion": "10.1.68"},
        "devices": {
            GATEWAY: {
                "overview": _device(GATEWAY, "udm-gateway", "UDM-Pro", "00:00:5e:00:53:01", "192.0.2.1", "ONLINE",
                                    "5.0.0", False, ["gateway"]),
                "detail": {"adoptedAt": "2026-01-10T09:00:00Z", "provisionedAt": "2026-11-20T06:00:00Z",
                           "interfaces": {"ports": [], "radios": []}},
                "statistics": {"uptimeSec": 2_000_000, "cpuUtilizationPct": 12.0, "memoryUtilizationPct": 41.0,
                               "uplink": {"txRateBps": 4_000_000, "rxRateBps": 21_000_000}, "interfaces": {"radios": []}},
            },
            SWITCH: {
                "overview": _device(SWITCH, "usw-core", "USW-24-PoE", "00:00:5e:00:53:02", "192.0.2.2", "ONLINE",
                                    "7.0.0", False, ["switching"]),
                "detail": {"adoptedAt": "2026-01-10T09:10:00Z", "provisionedAt": "2026-11-20T06:00:00Z",
                           "uplink": {"deviceId": GATEWAY}, "interfaces": {"ports": switch_ports, "radios": []}},
                "statistics": {"uptimeSec": 1_900_000, "cpuUtilizationPct": 8.0, "memoryUtilizationPct": 33.0,
                               "uplink": {"txRateBps": 9_000_000, "rxRateBps": 17_000_000}, "interfaces": {"radios": []}},
            },
            AP_ROOM_12: {
                "overview": _device(AP_ROOM_12, "uap-room-12", "U6-Pro", "00:00:5e:00:53:12", "192.0.2.12", "ONLINE",
                                    "6.6.0", True, ["accessPoint"]),
                "detail": {"adoptedAt": "2026-01-11T09:00:00Z", "provisionedAt": "2026-11-20T06:00:00Z",
                           "uplink": {"deviceId": SWITCH},
                           "interfaces": {"ports": [], "radios": [
                               {"wlanStandard": "802.11ax", "frequencyGHz": 2.4, "channelWidthMHz": 40, "channel": 6},
                               {"wlanStandard": "802.11ax", "frequencyGHz": 5, "channelWidthMHz": 80, "channel": 36}]}},
                "statistics": {"uptimeSec": 864_000, "cpuUtilizationPct": 38.0, "memoryUtilizationPct": 55.0,
                               "uplink": {"txRateBps": 30_000_000, "rxRateBps": 6_000_000},
                               "interfaces": {"radios": [{"frequencyGHz": 2.4, "txRetriesPct": 41.0},
                                                         {"frequencyGHz": 5, "txRetriesPct": 8.0}]}},
            },
            AP_ROOM_14: {
                "overview": _device(AP_ROOM_14, "uap-room-14", "U6-Pro", "00:00:5e:00:53:14", "192.0.2.14", "OFFLINE",
                                    "6.7.0", False, ["accessPoint"]),
                "detail": {"adoptedAt": "2026-01-11T09:30:00Z", "provisionedAt": "2026-11-20T06:00:00Z",
                           "uplink": {"deviceId": SWITCH},
                           "interfaces": {"ports": [], "radios": [
                               {"wlanStandard": "802.11ax", "frequencyGHz": 2.4, "channelWidthMHz": 20, "channel": 11},
                               {"wlanStandard": "802.11ax", "frequencyGHz": 5, "channelWidthMHz": 80, "channel": 149}]}},
                "statistics": {"uptimeSec": 0, "lastHeartbeatAt": "2026-11-25T03:12:00Z",
                               "interfaces": {"radios": []}},
            },
            AP_LOBBY: {
                "overview": _device(AP_LOBBY, "uap-lobby", "U6-Lite", "00:00:5e:00:53:10", "192.0.2.10", "ONLINE",
                                    "6.6.0", True, ["accessPoint"]),
                "detail": {"adoptedAt": "2026-01-11T08:00:00Z", "provisionedAt": "2026-11-20T06:00:00Z",
                           "uplink": {"deviceId": SWITCH},
                           "interfaces": {"ports": [], "radios": [
                               {"wlanStandard": "802.11ax", "frequencyGHz": 2.4, "channelWidthMHz": 20, "channel": 1},
                               {"wlanStandard": "802.11ax", "frequencyGHz": 5, "channelWidthMHz": 40, "channel": 44}]}},
                "statistics": {"uptimeSec": 864_000, "cpuUtilizationPct": 9.0, "memoryUtilizationPct": 40.0,
                               "uplink": {"txRateBps": 1_000_000, "rxRateBps": 800_000},
                               "interfaces": {"radios": [{"frequencyGHz": 2.4, "txRetriesPct": 4.0},
                                                         {"frequencyGHz": 5, "txRetriesPct": 2.0}]}},
            },
        },
        "clients": {
            GUEST_PHONE: {"type": "WIRELESS", "id": GUEST_PHONE, "name": "guest-phone", "connectedAt": "2026-11-25T08:50:00Z",
                          "ipAddress": "198.51.100.23", "macAddress": "00:00:5e:00:53:a1", "uplinkDeviceId": AP_ROOM_12,
                          "access": {"type": "GUEST", "authorized": False}},
            STAFF_LAPTOP: {"type": "WIRELESS", "id": STAFF_LAPTOP, "name": "staff-laptop", "connectedAt": "2026-11-25T07:30:00Z",
                           "ipAddress": "192.0.2.101", "macAddress": "00:00:5e:00:53:a2", "uplinkDeviceId": AP_ROOM_12,
                           "access": {"type": "DEFAULT"}},
            NVR: {"type": "WIRED", "id": NVR, "name": "nvr-1", "connectedAt": "2026-11-01T00:00:00Z",
                  "ipAddress": "192.0.2.50", "macAddress": "00:00:5e:00:53:a3", "uplinkDeviceId": SWITCH,
                  "access": {"type": "DEFAULT"}},
        },
        "networks": [
            {"id": NET_DEFAULT, "name": "Default", "management": "GATEWAY", "enabled": True, "vlanId": 1,
             "metadata": {"origin": "SYSTEM_DEFINED"}},
            {"id": NET_GUEST, "name": "Guests", "management": "GATEWAY", "enabled": True, "vlanId": 20,
             "metadata": {"origin": "USER_DEFINED"}},
        ],
        "wifi": [
            {"id": WIFI_STAFF, "name": "example-staff", "type": "STANDARD", "enabled": True,
             "securityConfiguration": {"type": "WPA2_ENTERPRISE"}, "broadcastingFrequenciesGHz": [2.4, 5],
             "metadata": {"origin": "USER_DEFINED"}},
            {"id": WIFI_GUEST, "name": "example-guest", "type": "STANDARD", "enabled": True,
             "securityConfiguration": {"type": "OPEN"}, "broadcastingFrequenciesGHz": [2.4, 5],
             "metadata": {"origin": "USER_DEFINED"}},
        ],
        "vouchers": [
            {"id": VOUCHER_1, "createdAt": "2026-11-20T10:00:00Z", "name": "Conference day", "code": "00000-00000",
             "authorizedGuestLimit": 1, "authorizedGuestCount": 0, "expired": False, "timeLimitMinutes": 480},
        ],
        "cloud": [
            {"hostId": CLOUD_HOST, "hostName": "example-office-gateway", "updatedAt": "2026-11-25T09:00:00Z",
             "devices": [
                 {"id": "000000530001", "name": "udm-gateway", "model": "UDM-Pro", "ip": "192.0.2.1", "status": "online",
                  "version": "5.0.0", "firmwareStatus": "upToDate", "updateAvailable": None, "isConsole": True},
                 {"id": "000000530012", "name": "uap-room-12", "model": "U6-Pro", "ip": "192.0.2.12", "status": "online",
                  "version": "6.6.0", "firmwareStatus": "updateAvailable", "updateAvailable": "6.7.0", "isConsole": False},
                 {"id": "000000530014", "name": "uap-room-14", "model": "U6-Pro", "ip": "192.0.2.14", "status": "offline",
                  "version": "6.7.0", "firmwareStatus": "upToDate", "updateAvailable": None, "isConsole": False},
             ]},
        ],
    }  # fmt: skip


def _page(rows: list[dict[str, Any]], query: httpx.QueryParams) -> dict[str, Any]:
    offset, limit = int(query.get("offset", 0)), int(query.get("limit", 25))
    window = rows[offset : offset + limit]
    return {"offset": offset, "limit": limit, "count": len(window), "totalCount": len(rows), "data": window}


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


class FakeUnifi:
    """The handler of `httpx.MockTransport`: both APIs, told apart by host."""

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 11, 25, 9, 17, tzinfo=UTC)
        self.state = seed()
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.guest_authorizations: dict[str, dict[str, Any]] = {}
        self.power_cycles: list[tuple[str, int]] = []
        self.restarts: list[str] = []
        self.voucher_serial = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if not (request.headers.get("X-API-KEY") or request.headers.get("X-API-Key")):
            return _json(401, {"statusCode": 401, "message": "Unauthorized"})
        if request.url.host == "api.ui.com":
            return self._cloud(request)
        prefix = "/proxy/network/integration"
        if not request.url.path.startswith(prefix + "/v1/"):
            return _json(404, {"statusCode": 404, "message": "Not found"})
        return self._local(request.method, request.url.path.removeprefix(prefix), request.url.params, body)

    # -- cloud ----------------------------------------------------------------------------------------

    def _cloud(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or request.url.path != "/v1/devices":
            return _json(404, {"httpStatusCode": 404, "message": "Not found"})
        wanted = request.url.params.get_list("hostIds[]")
        data = [h for h in self.state["cloud"] if not wanted or h["hostId"] in wanted]
        return _json(200, {"data": deepcopy(data), "httpStatusCode": 200, "traceId": "fake", "nextToken": ""})

    # -- local ----------------------------------------------------------------------------------------

    def _local(self, method: str, path: str, query: httpx.QueryParams, body: dict[str, Any] | None) -> httpx.Response:
        if path == "/v1/info":
            return _json(200, self.state["info"])
        if path == "/v1/sites":
            return _json(200, _page([{"id": SITE, "internalReference": "default", "name": "Example Office"}], query))
        m = re.fullmatch(r"/v1/sites/([^/]+)(/.*)", path)
        if m is None:
            return _json(404, {"statusCode": 404, "message": "Not found"})
        if m.group(1) != SITE:
            return _json(404, {"statusCode": 404, "message": "Site not found"})
        rest = m.group(2)
        if method == "GET":
            return self._get(rest, query)
        return self._write(method, rest, query, body)

    def _device_overview(self, dev: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(dev["overview"])

    def _get(self, rest: str, query: httpx.QueryParams) -> httpx.Response:
        s = self.state
        if rest == "/devices":
            return _json(200, _page([self._device_overview(d) for d in s["devices"].values()], query))
        m = re.fullmatch(r"/devices/([^/]+)(/statistics/latest)?", rest)
        if m:
            dev = s["devices"].get(m.group(1))
            if dev is None:
                return _json(404, {"statusCode": 404, "message": "Device not found"})
            if m.group(2):
                return _json(200, {"interfaces": {"radios": []}, **deepcopy(dev["statistics"])})
            return _json(200, {**self._device_overview(dev), **deepcopy(dev["detail"])})
        if rest == "/clients":
            rows = [
                {
                    k: v
                    for k, v in c.items()
                    if k in ("type", "id", "name", "connectedAt", "ipAddress", "macAddress", "uplinkDeviceId", "access")
                }
                for c in s["clients"].values()
            ]
            return _json(200, _page(deepcopy(rows), query))
        m = re.fullmatch(r"/clients/([^/]+)", rest)
        if m:
            client = s["clients"].get(m.group(1))
            if client is None:
                return _json(404, {"statusCode": 404, "message": "Client not found"})
            out = deepcopy(client)
            if m.group(1) in self.guest_authorizations:
                out["access"] = {
                    "type": "GUEST",
                    "authorized": True,
                    "authorization": self.guest_authorizations[m.group(1)],
                }
            return _json(200, out)
        if rest == "/networks":
            return _json(200, _page(deepcopy(s["networks"]), query))
        if rest == "/wifi/broadcasts":
            return _json(200, _page(deepcopy(s["wifi"]), query))
        if rest == "/hotspot/vouchers":
            rows = s["vouchers"]
            name = re.fullmatch(r"name\.eq\('((?:[^']|'')*)'\)", query.get("filter", ""))
            if name:
                rows = [v for v in rows if v["name"] == name.group(1).replace("''", "'")]
            return _json(200, _page(deepcopy(rows), query))
        return _json(404, {"statusCode": 404, "message": "Not found"})

    def _write(self, method: str, rest: str, query: httpx.QueryParams, body: dict[str, Any] | None) -> httpx.Response:
        s = self.state
        action = (body or {}).get("action")
        m = re.fullmatch(r"/devices/([^/]+)/actions", rest)
        if method == "POST" and m:
            dev = s["devices"].get(m.group(1))
            if dev is None:
                return _json(404, {"statusCode": 404, "message": "Device not found"})
            if action != "RESTART":
                return _json(400, {"statusCode": 400, "message": "Unknown action"})
            self.restarts.append(m.group(1))
            dev["overview"]["state"] = "UPDATING"
            return _json(200, {})
        m = re.fullmatch(r"/devices/([^/]+)/interfaces/ports/(\d+)/actions", rest)
        if method == "POST" and m:
            dev = s["devices"].get(m.group(1))
            if dev is None:
                return _json(404, {"statusCode": 404, "message": "Device not found"})
            if action != "POWER_CYCLE":
                return _json(400, {"statusCode": 400, "message": "Unknown action"})
            self.power_cycles.append((m.group(1), int(m.group(2))))
            return _json(200, {})
        m = re.fullmatch(r"/clients/([^/]+)/actions", rest)
        if method == "POST" and m:
            client = s["clients"].get(m.group(1))
            if client is None:
                return _json(404, {"statusCode": 404, "message": "Client not found"})
            if client["access"].get("type") != "GUEST":
                return _json(400, {"statusCode": 400, "message": "Client must be a guest"})
            previous = self.guest_authorizations.pop(m.group(1), None)
            if action == "AUTHORIZE_GUEST_ACCESS":
                minutes = (body or {}).get("timeLimitMinutes", 480)
                granted = {
                    "authorizedAt": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "authorizationMethod": "API",
                    "expiresAt": (self.now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for k in ("dataUsageLimitMBytes", "rxRateLimitKbps", "txRateLimitKbps"):
                    if k in (body or {}):
                        granted[k] = (body or {})[k]
                self.guest_authorizations[m.group(1)] = granted
                out: dict[str, Any] = {"action": action, "grantedAuthorization": granted}
                if previous:
                    out["revokedAuthorization"] = previous
                return _json(200, out)
            if action == "UNAUTHORIZE_GUEST_ACCESS":
                if previous is None:
                    return _json(400, {"statusCode": 400, "message": "Guest is not authorized"})
                return _json(200, {"action": action, "revokedAuthorization": previous})
            return _json(400, {"statusCode": 400, "message": "Unknown action"})
        if rest == "/hotspot/vouchers" and method == "POST":
            made = []
            for _ in range(int((body or {}).get("count", 1))):
                self.voucher_serial += 1
                made.append(
                    {
                        "id": f"ffffffff-0000-4000-8000-{self.voucher_serial + 100:012d}",
                        "createdAt": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "name": (body or {})["name"],
                        "code": f"{self.voucher_serial:05d}-00000",
                        "authorizedGuestCount": 0,
                        "expired": False,
                        **{k: v for k, v in (body or {}).items() if k not in ("name", "count")},
                    }
                )
            s["vouchers"].extend(made)
            return _json(201, {"vouchers": deepcopy(made)})
        if rest == "/hotspot/vouchers" and method == "DELETE":
            name = re.fullmatch(r"name\.eq\('((?:[^']|'')*)'\)", query.get("filter", ""))
            if not name:
                return _json(400, {"statusCode": 400, "message": "A filter is required"})
            wanted = name.group(1).replace("''", "'")
            kept = [v for v in s["vouchers"] if v["name"] != wanted]
            deleted = len(s["vouchers"]) - len(kept)
            s["vouchers"] = kept
            return _json(200, {"vouchersDeleted": deleted})
        return _json(404, {"statusCode": 404, "message": "Not found"})


Clock = Callable[[], datetime]
