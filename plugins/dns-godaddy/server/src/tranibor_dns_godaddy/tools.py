# SPDX-License-Identifier: Apache-2.0
"""The `dns/dns@1` tools over GoDaddy's Domains API v3: allow-list first, then GoDaddy.

Every call that names a zone passes `_zone` before anything is sent: a zone outside `DNS_GODADDY_ZONES` is
refused, on a read as on a write. The record routes are `/v3/domains/zones/{zone}/dns-records[/{recordId}]`.
GoDaddy applies every change at once: there is no publish step, so `needs_publish` is always false and
`publish` is a no-op that says so.

A record is read whole (every page) and filtered here, rather than trusting GoDaddy's `type` and `name` query
filters, whose treatment of the apex is not documented. SOA, NS and SRV records are returned (reading them is
useful) and never written: this server refuses to update or delete them (GoDaddy itself answers 409
`dns_record_not_mutable` for SOA and NS; SRV needs service/protocol fields whose mapping is not confirmed).

Writes return what the platform needs to undo them (`previous`, or the deleted record's own fields), as the
declared compensations in `servers/dns-godaddy.yaml` read it. Every record, TXT value and zone text returned
is third-party data.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from .client import GoDaddyClient
from .config import ZONE_NAME, Settings, normalise_zone
from .errors import GoDaddyError, Refused
from .plan import Desired, Key, plan
from .records import Record, cname_problems, normalise_name, same_value, validate
from .rules import RULES

MAX_RECORDS = 1000
MAX_EXPORT = 200_000
PAGE_SIZE = 100  #: GoDaddy's documented maximum `pageSize`
PROVIDER = "godaddy"
#: What GoDaddy documents as the default TTL (one hour); not confirmed by a call. Sent when the profile's `ttl` is 0.
DEFAULT_TTL = 3600
READ_ONLY_TYPES = frozenset({"NS", "SOA", "SRV"})
CAA_VALUE = re.compile(r'^(\d{1,3})\s+(issue|issuewild|iodef)\s+"?([^"]+)"?$')


def from_godaddy(zone: str, raw: dict[str, Any]) -> Record:
    """GoDaddy's DNS record as the profile's: `@` is the apex, MX and CAA fold their extra fields into the value."""
    kind = str(raw["type"]).upper()
    data = str(raw.get("data", ""))
    value = data
    if kind == "MX":
        value = f"{int(raw.get('priority') or 0)} {data}"
    elif kind == "CAA":
        value = f'{int(raw.get("flag") or 0)} {raw.get("tag", "")} "{data}"'
    elif kind == "SRV":
        value = f"{int(raw.get('priority') or 0)} {int(raw.get('weight') or 0)} {int(raw.get('port') or 0)} {data}"
    name = str(raw.get("name") or "")
    return {
        "id": str(raw["recordId"]),
        "zone": zone,
        "name": "" if name == "@" else name.lower(),
        "type": kind,
        "ttl": int(raw.get("ttl") or 0),
        "value": value,
    }


def to_godaddy(kind: str, name: str, value: str, ttl: int) -> dict[str, Any]:
    """The body GoDaddy takes for a record of the profile (a full record: its PUT replaces)."""
    text = value.strip()
    body: dict[str, Any] = {"name": name or "@", "type": kind, "ttl": ttl or DEFAULT_TTL}
    if kind == "MX":
        priority, host = text.split(None, 1)
        body.update({"data": host, "priority": int(priority)})
    elif kind == "CAA":
        match = CAA_VALUE.fullmatch(text)
        if match is None:
            raise Refused(f"a CAA value is '<flag> <tag> \"<value>\"' (tag issue, issuewild or iodef), not {text!r}")
        body.update({"data": match.group(3), "flag": int(match.group(1)), "tag": match.group(2)})
    elif kind == "TXT" and len(text) >= 2 and text[0] == text[-1] == '"':
        body["data"] = text[1:-1]
    else:
        body["data"] = text
    return body


class GoDaddyDnsTools:
    def __init__(self, client: GoDaddyClient, settings: Settings) -> None:
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
        return f"/v3/domains/zones/{self._zone(zone)}/dns-records{tail}"

    @staticmethod
    def _record_id(record_id: str) -> str:
        text = str(record_id).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", text):
            raise Refused(f"id {record_id!r} is not a record id of this provider")
        return text

    # ---- reads ----------------------------------------------------------------------------------

    async def get_provider(self) -> dict[str, Any]:
        return {"instance": self.settings.instance, "provider": PROVIDER, "zones": list(self.allowed)}

    async def _all(self, zone: str) -> list[Record]:
        """Every record of the zone, page after page, sorted."""
        zone = self._zone(zone)
        found: list[Record] = []
        page = 1
        while True:
            rows = await self.client.request("GET", self._path(zone), {"page": str(page), "pageSize": str(PAGE_SIZE)})
            rows = rows if isinstance(rows, list) else []
            found += [from_godaddy(zone, r) for r in rows]
            if len(rows) < PAGE_SIZE:
                break
            if len(found) >= MAX_RECORDS:
                raise Refused(f"the zone holds {MAX_RECORDS} records or more: this server reads at most that")
            page += 1
        return sorted(found, key=lambda r: (r["name"], r["type"], r["id"]))

    async def list_zones(self) -> dict[str, Any]:
        async def exists(zone: str) -> bool:
            try:
                await self.client.request("GET", self._path(zone), {"page": "1", "pageSize": "1"})
            except GoDaddyError as error:
                if error.status == 404:
                    return False
                raise
            return True

        flags = await asyncio.gather(*(exists(z) for z in self.allowed))
        return {"zones": [z for z, here in zip(self.allowed, flags, strict=True) if here]}

    async def get_zone(self, zone: str) -> dict[str, Any]:
        name = self._zone(zone)
        records = await self._all(name)
        servers = sorted(r["value"] for r in records if r["type"] == "NS" and r["name"] == "")
        # GoDaddy applies every change at once: nothing is ever waiting.
        return {"zone": name, "name_servers": servers, "pending_changes": False}

    async def list_records(self, zone: str, type: str | None = None, name: str | None = None) -> dict[str, Any]:
        zone = self._zone(zone)
        wanted = type.strip().upper() if type else None
        if wanted and wanted not in (*RULES.types, *READ_ONLY_TYPES):
            raise Refused(f"type {type!r} is not one of {', '.join((*RULES.types, *sorted(READ_ONLY_TYPES)))}")
        sub = normalise_name(name) if name is not None else None
        records = [
            r
            for r in await self._all(zone)
            if (wanted is None or r["type"] == wanted) and (sub is None or r["name"] == sub)
        ]
        return {"zone": zone, "count": len(records), "records": records}

    async def _one(self, zone: str, record_id: str) -> Record:
        wanted = self._record_id(record_id)
        for record in await self._all(zone):
            if record["id"] == wanted:
                return record
        raise Refused(f"record {record_id!r} not found in {zone}")

    async def get_record(self, zone: str, id: str) -> dict[str, Any]:
        zone = self._zone(zone)
        return {"zone": zone, "record": await self._one(zone, id)}

    async def export_zone(self, zone: str) -> dict[str, Any]:
        """GoDaddy has no zone-file export: the text is built from the records (a rendering, not GoDaddy's own)."""
        zone = self._zone(zone)
        lines = [f"$ORIGIN {zone}."]
        for r in await self._all(zone):
            lines.append(f"{r['name'] or '@'}\t{r['ttl'] or DEFAULT_TTL}\tIN {r['type']}\t{r['value']}")
        text = "\n".join(lines) + "\n"
        return {
            "zone": zone,
            "format": "zone-file text built from the records",
            "truncated": len(text) > MAX_EXPORT,
            "text": text[:MAX_EXPORT],
        }

    async def plan_changes(self, zone: str, desired: list[Desired] | None, clear: list[Key] | None) -> dict[str, Any]:
        name = self._zone(zone)
        if not desired and not clear:
            raise Refused("nothing to plan: give `desired` records, or `clear` names")
        live = await self._all(name)
        return plan(name, live, desired or [], clear or [], stages_changes=False)

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
        body = to_godaddy(kind, sub, value, ttl)  # refuses a malformed MX/CAA value before any call
        here = [r for r in await self._all(zone) if r["name"] == sub]
        for existing in here:
            if existing["type"] == kind and same_value(kind, existing["value"], value):
                return self._created(existing, created=False)
        candidate: Record = {"id": "new", "zone": zone, "name": sub, "type": kind, "ttl": ttl, "value": value.strip()}
        problems = cname_problems([*here, candidate])
        if sub in problems:
            raise Refused(problems[sub])
        # POST takes an ARRAY of records (documented); the new record's id is then read back, since the
        # answer's shape is not documented.
        await self.client.request("POST", self._path(zone), body=[body])
        for record in await self._all(zone):
            if record["name"] == sub and record["type"] == kind and same_value(kind, record["value"], value):
                return self._created(record, created=True)
        raise GoDaddyError(0, "GoDaddy accepted the record but it is not in the zone's records")

    @staticmethod
    def _created(record: Record, *, created: bool) -> dict[str, Any]:
        return {
            "zone": record["zone"],
            "created": created,
            "id": record["id"],
            "name": record["name"],
            "type": record["type"],
            "value": record["value"],
            "ttl": record["ttl"],
            "needs_publish": False,
        }

    @staticmethod
    def _writable(record: Record) -> None:
        if record["type"] in READ_ONLY_TYPES:
            raise Refused(f"{record['type']} records are read-only through this server (id {record['id']})")

    async def update_record(
        self, zone: str, id: str, value: str | None = None, ttl: int | None = None
    ) -> dict[str, Any]:
        """Changes a record's value and/or TTL (never its name or type). GoDaddy's PUT replaces the whole
        record, so the full record is sent. `previous` is what the declared compensation writes back."""
        zone = self._zone(zone)
        before = await self._one(zone, id)
        self._writable(before)
        new_value = before["value"] if value is None else value.strip()
        new_ttl = before["ttl"] if ttl is None else int(ttl)
        validate(before["type"], before["name"], new_value, new_ttl)
        body = to_godaddy(before["type"], before["name"], new_value, new_ttl)
        previous = {"value": before["value"], "ttl": before["ttl"]}
        changed = (new_value, new_ttl) != (before["value"], before["ttl"])
        if changed:
            await self.client.request("PUT", self._path(zone, f"/{before['id']}"), body=body)
        return {
            "zone": zone,
            "id": before["id"],
            "changed": changed,
            "name": before["name"],
            "type": before["type"],
            "value": new_value,
            "ttl": body["ttl"] if changed else new_ttl,
            "previous": previous,
            "needs_publish": False,
        }

    async def delete_record(self, zone: str, id: str) -> dict[str, Any]:
        """Deletes one record. The result holds the record's own fields: the declared compensation recreates it."""
        zone = self._zone(zone)
        before = await self._one(zone, id)
        self._writable(before)
        await self.client.request("DELETE", self._path(zone, f"/{before['id']}"))
        return {
            "zone": zone,
            "deleted": True,
            "id": before["id"],
            "name": before["name"],
            "type": before["type"],
            "value": before["value"],
            "ttl": before["ttl"],
            "needs_publish": False,
        }

    async def publish(self, zone: str) -> dict[str, Any]:
        """GoDaddy applies every change at once: nothing to publish, and the answer says so."""
        return {
            "zone": self._zone(zone),
            "published": False,
            "pending": False,
            "note": "GoDaddy applies each change as soon as it is written; there is nothing to publish",
        }
