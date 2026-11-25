# SPDX-License-Identifier: Apache-2.0
"""The `dns/dns@1` tools over the OVHcloud API v1: allow-list first, then OVHcloud.

Every call that names a zone passes `_zone` before anything is sent: a zone outside `DNS_OVH_ZONES` is
refused, on a read as on a write. The record routes are `/domain/zone/{zone}/record[/{id}]`; OVHcloud holds
changes in the zone until `POST /domain/zone/{zone}/refresh` publishes them (`needs_publish` says so, and
`publish` is that refresh).

Writes return what the platform needs to undo them (`previous`, or the deleted record's own fields), as the
declared compensations in `servers/dns-ovh.yaml` read it. Every record, TXT value and zone text returned is
third-party data.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .client import OvhClient
from .config import ZONE_NAME, Settings, normalise_zone
from .errors import OvhError, Refused
from .plan import Desired, Key, plan
from .records import Record, cname_problems, normalise_name, same_value, validate
from .rules import RULES

MAX_RECORDS = 1000
MAX_EXPORT = 200_000
FETCH_AT_ONCE = 10
PROVIDER = "ovhcloud"


def from_ovh(zone: str, raw: dict[str, Any]) -> Record:
    """OVHcloud's `domain.zone.Record` as the profile's record (ttl 0 when the zone's default applies)."""
    return {
        "id": str(int(raw["id"])),
        "zone": zone,
        "name": str(raw.get("subDomain") or ""),
        "type": str(raw["fieldType"]),
        "ttl": int(raw.get("ttl") or 0),
        "value": str(raw["target"]),
    }


class OvhDnsTools:
    def __init__(self, client: OvhClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self.allowed = settings.zones

    def _zone(self, zone: str) -> str:
        name = normalise_zone(zone)
        if not ZONE_NAME.fullmatch(name) or name not in self.allowed:
            # Not a word about whether the zone exists: the allow-list is the only thing said.
            raise Refused(f"zone {zone!r} is not in this server's allow-list ({', '.join(self.allowed)})")
        return name

    def _path(self, zone: str, tail: str = "") -> str:
        return f"/domain/zone/{self._zone(zone)}{tail}"

    @staticmethod
    def _record_id(record_id: str) -> int:
        text = str(record_id).strip()
        if not text.isdigit():
            raise Refused(f"id {record_id!r} is not a record id of this provider (a number)")
        return int(text)

    # ---- reads ----------------------------------------------------------------------------------

    async def get_provider(self) -> dict[str, Any]:
        return {"instance": self.settings.instance, "provider": PROVIDER, "zones": list(self.allowed)}

    async def list_zones(self) -> dict[str, Any]:
        account = {str(z).lower() for z in (await self.client.request("GET", "/domain/zone") or [])}
        return {"zones": [z for z in self.allowed if z in account]}

    async def get_zone(self, zone: str) -> dict[str, Any]:
        name = self._zone(zone)
        base = f"/domain/zone/{name}"
        info, status = await asyncio.gather(
            self.client.request("GET", base), self.client.request("GET", f"{base}/status")
        )
        deployed = (status or {}).get("isDeployed")
        return {
            "zone": name,
            "name_servers": info.get("nameServers", []),
            # OVHcloud answers isDeployed false while changes wait for the refresh (or are in flight).
            "pending_changes": deployed is False,
            "dnssec_activated": info.get("dnssecActivated"),
            "last_update": info.get("lastUpdate"),
        }

    async def _records(self, zone: str, type: str | None = None, name: str | None = None) -> list[Record]:
        query: dict[str, str] = {}
        if type:
            wanted = type.strip().upper()
            if wanted not in RULES.types:
                raise Refused(f"type {type!r} is not one of {', '.join(RULES.types)}")
            query["fieldType"] = wanted
        if name is not None:
            query["subDomain"] = normalise_name(name)
        base = self._path(zone, "/record")
        ids: list[int] = list(await self.client.request("GET", base, query or None) or [])
        if len(ids) > MAX_RECORDS:
            raise Refused(
                f"the zone holds {len(ids)} matching records, more than {MAX_RECORDS}: narrow with type and name"
            )
        gate = asyncio.Semaphore(FETCH_AT_ONCE)

        async def one(record_id: int) -> Record:
            async with gate:
                return from_ovh(zone, await self.client.request("GET", f"{base}/{int(record_id)}"))

        found = await asyncio.gather(*(one(i) for i in ids))
        return sorted(found, key=lambda r: (r["name"], r["type"], int(r["id"])))

    async def list_records(self, zone: str, type: str | None = None, name: str | None = None) -> dict[str, Any]:
        zone = self._zone(zone)
        records = await self._records(zone, type, name)
        return {"zone": zone, "count": len(records), "records": records}

    async def get_record(self, zone: str, id: str) -> dict[str, Any]:
        zone = self._zone(zone)
        raw = await self.client.request("GET", self._path(zone, f"/record/{self._record_id(id)}"))
        return {"zone": zone, "record": from_ovh(zone, raw)}

    async def export_zone(self, zone: str) -> dict[str, Any]:
        text = await self.client.request("GET", self._path(zone, "/export"))
        text = text if isinstance(text, str) else ""
        return {
            "zone": self._zone(zone),
            "format": "BIND zone file",
            "truncated": len(text) > MAX_EXPORT,
            "text": text[:MAX_EXPORT],
        }

    async def plan_changes(self, zone: str, desired: list[Desired] | None, clear: list[Key] | None) -> dict[str, Any]:
        name = self._zone(zone)
        if not desired and not clear:
            raise Refused("nothing to plan: give `desired` records, or `clear` names")
        live = await self._records(name)
        status = await self.client.request("GET", f"/domain/zone/{name}/status")
        result = plan(name, live, desired or [], clear or [], stages_changes=True)
        if status and status.get("isDeployed") is False:
            result["warnings"].append(
                "the zone reports it is not deployed: changes already waiting (or in flight) are part of the live "
                "records read here and will be published by the next publish too"
            )
        return result

    # ---- writes ---------------------------------------------------------------------------------

    async def create_record(
        self, zone: str, name: str, type: str, value: str, ttl: int = 0, action_key: str | None = None
    ) -> dict[str, Any]:
        """Creates a record, unless an identical one exists (same type, name and value): then that record
        is returned and nothing is written, which makes a replayed action harmless (`action_key`)."""
        zone = self._zone(zone)
        kind = type.strip().upper()
        sub = normalise_name(name)
        validate(kind, sub, value, ttl)
        here = await self._records(zone, name=sub)
        for existing in here:
            if existing["type"] == kind and same_value(kind, existing["value"], value):
                return self._created(existing, created=False, needs_publish=False)
        candidate: Record = {"id": "new", "zone": zone, "name": sub, "type": kind, "ttl": ttl, "value": value.strip()}
        problems = cname_problems([*here, candidate])
        if sub in problems:
            raise Refused(problems[sub])
        body: dict[str, Any] = {"fieldType": kind, "subDomain": sub, "target": value.strip()}
        if ttl:
            body["ttl"] = ttl
        raw = await self.client.request("POST", self._path(zone, "/record"), body=body)
        return self._created(from_ovh(zone, raw), created=True, needs_publish=True)

    @staticmethod
    def _created(record: Record, *, created: bool, needs_publish: bool) -> dict[str, Any]:
        return {
            "zone": record["zone"],
            "created": created,
            "id": record["id"],
            "name": record["name"],
            "type": record["type"],
            "value": record["value"],
            "ttl": record["ttl"],
            "needs_publish": needs_publish,
        }

    async def update_record(
        self, zone: str, id: str, value: str | None = None, ttl: int | None = None
    ) -> dict[str, Any]:
        """Changes a record's value and/or TTL (never its name or type). `previous` is what the declared
        compensation writes back. ttl 0 asks for the zone's default: it is sent as null, which OVHcloud's
        `domain.zone.RecordUpdate` documents as allowed for `ttl` (the meaning of an explicit 0 is not)."""
        zone = self._zone(zone)
        base = self._path(zone, f"/record/{self._record_id(id)}")
        before = from_ovh(zone, await self.client.request("GET", base))
        new_value = before["value"] if value is None else value.strip()
        new_ttl = before["ttl"] if ttl is None else int(ttl)
        validate(before["type"], before["name"], new_value, new_ttl)
        previous = {"value": before["value"], "ttl": before["ttl"]}
        changed = (new_value, new_ttl) != (before["value"], before["ttl"])
        if changed:
            await self.client.request(
                "PUT", base, body={"subDomain": before["name"], "target": new_value, "ttl": new_ttl or None}
            )
        return {
            "zone": zone,
            "id": before["id"],
            "changed": changed,
            "name": before["name"],
            "type": before["type"],
            "value": new_value,
            "ttl": new_ttl,
            "previous": previous,
            "needs_publish": changed,
        }

    async def delete_record(self, zone: str, id: str) -> dict[str, Any]:
        """Deletes one record. The result holds the record's own fields: the declared compensation recreates it."""
        zone = self._zone(zone)
        base = self._path(zone, f"/record/{self._record_id(id)}")
        before = from_ovh(zone, await self.client.request("GET", base))
        await self.client.request("DELETE", base)
        return {
            "zone": zone,
            "deleted": True,
            "id": before["id"],
            "name": before["name"],
            "type": before["type"],
            "value": before["value"],
            "ttl": before["ttl"],
            "needs_publish": True,
        }

    async def publish(self, zone: str) -> dict[str, Any]:
        """Publishes the zone's pending changes: `POST /domain/zone/{zone}/refresh`. Nothing pending: a no-op."""
        zone = self._zone(zone)
        status = await self.client.request("GET", self._path(zone, "/status"))
        if (status or {}).get("isDeployed") is not False:
            return {"zone": zone, "published": False, "pending": False, "note": "no changes were waiting"}
        await self.client.request("POST", self._path(zone, "/refresh"))
        try:
            after = await self.client.request("GET", self._path(zone, "/status"))
        except OvhError:
            after = None
        return {
            "zone": zone,
            "published": True,
            "pending": (after or {}).get("isDeployed") is False,
            "note": "OVHcloud will now serve the changes from its name servers",
        }
