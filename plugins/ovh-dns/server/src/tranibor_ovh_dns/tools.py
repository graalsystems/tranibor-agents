# SPDX-License-Identifier: Apache-2.0
"""The tools: allow-list first, then OVHcloud.

Every call that names a zone passes `_zone` before anything is sent: a zone outside `OVH_DNS_ZONES` is
refused, on a read as on a write. The record routes are `/domain/zone/{zone}/record[/{id}]`; OVHcloud holds
changes in the zone until `POST /domain/zone/{zone}/refresh` publishes them (`needs_refresh` says so).

Writes return what the platform needs to undo them (`previous`, or the deleted record's own fields), as the
declared compensations in `servers/ovh-dns.yaml` read it. Every record, TXT value and zone text returned is
third-party data.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .client import OvhClient
from .config import ZONE_NAME, Settings, normalise_zone
from .errors import OvhError, Refused
from .plan import Desired, Key, audit_zone, plan
from .records import (
    RECORD_TYPES,
    Record,
    cname_problems,
    from_ovh,
    normalise_sub_domain,
    same_target,
    validate,
)

MAX_RECORDS = 1000
MAX_EXPORT = 200_000
FETCH_AT_ONCE = 10


class OvhDnsTools:
    def __init__(self, client: OvhClient, settings: Settings) -> None:
        self.client = client
        self.allowed = settings.zones

    def _zone(self, zone: str) -> str:
        name = normalise_zone(zone)
        if not ZONE_NAME.fullmatch(name) or name not in self.allowed:
            # Not a word about whether the zone exists: the allow-list is the only thing said.
            raise Refused(f"zone {zone!r} is not in this server's allow-list ({', '.join(self.allowed)})")
        return name

    def _path(self, zone: str, tail: str = "") -> str:
        return f"/domain/zone/{self._zone(zone)}{tail}"

    # ---- reads ----------------------------------------------------------------------------------

    async def list_zones(self) -> dict[str, Any]:
        account = {str(z).lower() for z in (await self.client.request("GET", "/domain/zone") or [])}
        present = [z for z in self.allowed if z in account]
        return {"zones": present, "allowed_but_not_in_account": [z for z in self.allowed if z not in account]}

    async def get_zone(self, zone: str) -> dict[str, Any]:
        name = self._zone(zone)
        base = f"/domain/zone/{name}"
        info, status, soa, dnssec = await asyncio.gather(
            self.client.request("GET", base),
            self.client.request("GET", f"{base}/status"),
            self.client.request("GET", f"{base}/soa"),
            self.client.request("GET", f"{base}/dnssec"),
        )
        return {
            "zone": name,
            "name_servers": info.get("nameServers", []),
            "last_update": info.get("lastUpdate"),
            "dns_anycast": info.get("hasDnsAnycast"),
            "dnssec_supported": info.get("dnssecSupported"),
            "dnssec": {"activated": info.get("dnssecActivated"), "status": (dnssec or {}).get("status")},
            "status": {
                "is_deployed": status.get("isDeployed"),
                "errors": status.get("errors") or [],
                "warnings": status.get("warnings") or [],
            },
            "soa": {
                "server": soa.get("server"),
                "email": soa.get("email"),
                "serial": soa.get("serial"),
                "refresh": soa.get("refresh"),
                "expire": soa.get("expire"),
                "ttl": soa.get("ttl"),
                "nx_domain_ttl": soa.get("nxDomainTtl"),
            },
        }

    async def _records(self, zone: str, field_type: str | None = None, sub_domain: str | None = None) -> list[Record]:
        query: dict[str, str] = {}
        if field_type:
            wanted = field_type.strip().upper()
            if wanted not in RECORD_TYPES:
                raise Refused(f"field_type {field_type!r} is not one of {', '.join(RECORD_TYPES)}")
            query["fieldType"] = wanted
        if sub_domain is not None:
            query["subDomain"] = normalise_sub_domain(sub_domain)
        base = self._path(zone, "/record")
        ids: list[int] = list(await self.client.request("GET", base, query or None) or [])
        if len(ids) > MAX_RECORDS:
            raise Refused(
                f"the zone holds {len(ids)} matching records, more than {MAX_RECORDS}: "
                "narrow with field_type and sub_domain"
            )
        gate = asyncio.Semaphore(FETCH_AT_ONCE)

        async def one(record_id: int) -> Record:
            async with gate:
                return from_ovh(await self.client.request("GET", f"{base}/{int(record_id)}"))

        return sorted(
            await asyncio.gather(*(one(i) for i in ids)), key=lambda r: (r["sub_domain"], r["field_type"], r["id"])
        )

    async def list_records(
        self, zone: str, field_type: str | None = None, sub_domain: str | None = None
    ) -> dict[str, Any]:
        name = self._zone(zone)
        records = await self._records(name, field_type, sub_domain)
        return {"zone": name, "count": len(records), "records": records}

    async def get_record(self, zone: str, record_id: int) -> dict[str, Any]:
        raw = await self.client.request("GET", self._path(zone, f"/record/{int(record_id)}"))
        return {"zone": self._zone(zone), "record": from_ovh(raw)}

    async def export_zone(self, zone: str) -> dict[str, Any]:
        text = await self.client.request("GET", self._path(zone, "/export"))
        text = text if isinstance(text, str) else ""
        return {
            "zone": self._zone(zone),
            "format": "BIND zone file",
            "truncated": len(text) > MAX_EXPORT,
            "text": text[:MAX_EXPORT],
        }

    async def list_zone_history(self, zone: str, limit: int = 10) -> dict[str, Any]:
        """The zone's restore points, newest first. Each carries the URL OVHcloud gives for the saved zone
        file; the server never fetches it (it is not an egress host) and offers no restore."""
        base = self._path(zone, "/history")
        dates: list[str] = sorted(await self.client.request("GET", base) or [], reverse=True)
        points = []
        for date in dates[: max(1, min(limit, 30))]:
            point = await self.client.request("GET", f"{base}/{date}")
            points.append({"created": point.get("creationDate", date), "zone_file_url": point.get("zoneFileUrl")})
        return {"zone": self._zone(zone), "total": len(dates), "restore_points": points}

    async def plan_changes(self, zone: str, desired: list[Desired], clear: list[Key] | None = None) -> dict[str, Any]:
        name = self._zone(zone)
        if not desired and not clear:
            raise Refused("nothing to plan: give `desired` records, or `clear` names")
        live = await self._records(name)
        status = await self.client.request("GET", f"/domain/zone/{name}/status")
        result = plan(live, desired, clear or [])
        result["zone"] = name
        if status and status.get("isDeployed") is False:
            result["warnings"].append(
                "the zone reports it is not deployed: changes already waiting (or in flight) are part of the live "
                "records read here and will be published by the next refresh too"
            )
        return result

    async def audit_zone(self, zone: str) -> dict[str, Any]:
        name = self._zone(zone)
        records = await self._records(name)
        status = await self.client.request("GET", f"/domain/zone/{name}/status")
        return audit_zone(name, records, {"is_deployed": (status or {}).get("isDeployed")})

    # ---- writes ---------------------------------------------------------------------------------

    async def create_record(
        self, zone: str, field_type: str, sub_domain: str, target: str, ttl: int = 0, action_key: str | None = None
    ) -> dict[str, Any]:
        """Creates a record, unless an identical one exists (same type, name and target): then that record
        is returned and nothing is written, which makes a replayed action harmless (`action_key`)."""
        name = self._zone(zone)
        kind = field_type.strip().upper()
        sub = normalise_sub_domain(sub_domain)
        validate(kind, sub, target, ttl)
        here = await self._records(name, sub_domain=sub)
        for existing in here:
            if existing["field_type"] == kind and same_target(kind, existing["target"], target):
                return {
                    "zone": name,
                    "created": False,
                    "action_key": action_key,
                    "record_id": existing["id"],
                    **{k: existing[k] for k in ("field_type", "sub_domain", "target", "ttl")},
                    "needs_refresh": False,
                    "note": "an identical record already exists: nothing was written",
                }
        candidate: Record = {"id": 0, "field_type": kind, "sub_domain": sub, "target": target.strip(), "ttl": ttl}
        problems = cname_problems([*here, candidate])
        if sub in problems:
            raise Refused(problems[sub])
        body: dict[str, Any] = {"fieldType": kind, "subDomain": sub, "target": target.strip()}
        if ttl:
            body["ttl"] = ttl
        created = from_ovh(await self.client.request("POST", self._path(name, "/record"), body=body))
        return {
            "zone": name,
            "created": True,
            "action_key": action_key,
            "record_id": created["id"],
            **{k: created[k] for k in ("field_type", "sub_domain", "target", "ttl")},
            "needs_refresh": True,
        }

    async def update_record(
        self,
        zone: str,
        record_id: int,
        target: str | None = None,
        ttl: int | None = None,
        sub_domain: str | None = None,
    ) -> dict[str, Any]:
        """Changes a record's target, TTL or sub-domain (never its type). `previous` is what the declared
        compensation writes back. ttl 0 asks for the zone's default: it is sent as null, which OVHcloud's
        `domain.zone.RecordUpdate` documents as allowed for `ttl` (the meaning of an explicit 0 is not)."""
        name = self._zone(zone)
        base = self._path(name, f"/record/{int(record_id)}")
        before = from_ovh(await self.client.request("GET", base))
        new_sub: str = before["sub_domain"] if sub_domain is None else normalise_sub_domain(sub_domain)
        new_target: str = before["target"] if target is None else target.strip()
        new_ttl: int = before["ttl"] if ttl is None else int(ttl)
        after: dict[str, Any] = {"sub_domain": new_sub, "target": new_target, "ttl": new_ttl}
        validate(before["field_type"], new_sub, new_target, new_ttl)
        if after == {k: before[k] for k in after}:
            return {
                "zone": name,
                "record_id": before["id"],
                "changed": False,
                "previous": _fields(before),
                **after,
                "needs_refresh": False,
            }
        if new_sub != before["sub_domain"] or before["field_type"] == "CNAME":
            siblings = [r for r in await self._records(name, sub_domain=new_sub) if r["id"] != before["id"]]
            problems = cname_problems([*siblings, {**before, **after}])
            if new_sub in problems:
                raise Refused(problems[new_sub])
        await self.client.request(
            "PUT",
            base,
            body={"subDomain": after["sub_domain"], "target": after["target"], "ttl": after["ttl"] or None},
        )
        return {
            "zone": name,
            "record_id": before["id"],
            "field_type": before["field_type"],
            "changed": True,
            "previous": _fields(before),
            **after,
            "needs_refresh": True,
        }

    async def delete_record(self, zone: str, record_id: int) -> dict[str, Any]:
        """Deletes one record. The result holds the record's own fields: the declared compensation recreates it."""
        name = self._zone(zone)
        base = self._path(name, f"/record/{int(record_id)}")
        before = from_ovh(await self.client.request("GET", base))
        await self.client.request("DELETE", base)
        return {"zone": name, "deleted": True, "record_id": before["id"], **_fields(before), "needs_refresh": True}

    async def refresh_zone(self, zone: str) -> dict[str, Any]:
        """Publishes the zone's pending changes: `POST /domain/zone/{zone}/refresh`."""
        name = self._zone(zone)
        await self.client.request("POST", self._path(name, "/refresh"))
        try:
            status = await self.client.request("GET", self._path(name, "/status"))
        except OvhError:
            status = None
        return {"zone": name, "refreshed": True, "is_deployed": (status or {}).get("isDeployed")}


def _fields(record: Record) -> dict[str, Any]:
    return {k: record[k] for k in ("field_type", "sub_domain", "target", "ttl")}
