# SPDX-License-Identifier: Apache-2.0
"""The tracker profile (Decision 3): incidents, request items and changes as Tranibor work items.

The shapes are specified in `TRACKER_PROFILE.md`; the core's `tracker/plugin` adapter is written
against that page. An item's key is its number (`INC0010001`, `RITM0010001`, `CHG0010001`).

Everything Tranibor writes here is a WORK NOTE (internal): a customer-visible comment goes through
`add_comment`, which a person approves.

A change request's state moves through the Change Management API, only to a state its change model
reaches from where it stands (graalsystems/tranibor-agents#24): nothing walks it past its approvals.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import structlog

from .client import MAX_ROWS, ServiceNowClient
from .errors import Refused
from .query import Filter, Op, Query, eq
from .tables import TRACKER_TABLES, UPDATABLE_FIELDS, is_sys_id, table_of_number
from .tools import ServiceNowTools, marker_of, text_key, unkept
from .webhook import CLOSED_STATES, parse, verify

log = structlog.get_logger("choregos_servicenow.tracker")

_STATUS = re.compile(r"\[choregos-status:([A-Za-z0-9._:-]{1,80})\]")
_ANY_MARKER = re.compile(r"\[choregos(?:-status)?:([A-Za-z0-9._:-]{1,80})\]")
_CREATE_FIELDS = UPDATABLE_FIELDS | {"caller_id"}
#: How each table is closed: the state label, and the close code ServiceNow asks for.
_CLOSE: dict[str, tuple[str, str | None]] = {
    "incident": ("Resolved", "Solution provided"),
    "sc_req_item": ("Closed Complete", None),
    "change_request": ("Closed", "successful"),
}
#: The most keys `tracker_list_candidates` answers (`plugin-tracker-profile.schema.json` of the core:
#: `limit` at most 200, `keys` at most 200 items).
CANDIDATES_BOUND = 200


def iso(value: Any) -> str | None:
    """`2026-10-27 08:00:00` (ServiceNow, UTC) → `2026-10-27T08:00:00Z`."""
    text = str(value or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", text):
        return None
    return text.replace(" ", "T") + "Z"


class Tracker:
    def __init__(self, client: ServiceNowClient, tools: ServiceNowTools) -> None:
        self.client = client
        self.tools = tools

    async def _item(self, key: str) -> tuple[str, dict[str, Any]]:
        table = table_of_number(key)
        row = await self.client.find_one(table, Query(table).where(eq("number", key)))
        if row is None:
            raise Refused(f"no {table} numbered {key}")
        return table, row

    async def fetch_item(self, key: str) -> dict[str, Any]:
        table, row = await self._item(key)
        f, d = row["fields"], row["display"]
        journal = await self.tools.journal_entries(table, f["sys_id"], ("work_notes", "comments"), MAX_ROWS)
        comments = []
        for entry in reversed(journal):
            found = _ANY_MARKER.search(str(entry["value"]))
            comments.append(
                {
                    "id": entry["sys_id"],
                    "author": entry["created_by"],
                    "body": entry["value"],
                    "ts": iso(entry["created_on"]),
                    "marker": found.group(1) if found else None,
                    "visibility": "internal" if entry["element"] == "work_notes" else "customer",
                }
            )
        person = "caller_id" if table == "incident" else "requested_for" if table == "sc_req_item" else None
        return {
            "key": key,
            "title": f.get("short_description") or "",
            "body": f.get("description") or "",
            "url": f"{self.client.settings.instance_url}/nav_to.do?uri={table}.do?sys_id={f['sys_id']}",
            "state": d.get("state", f.get("state")),
            "labels": [],
            "item_type": table,
            "assignees": [d["assigned_to"]] if d.get("assigned_to") else [],
            "author": d.get(person) if person else None,
            "created_at": iso(f.get("sys_created_on")),
            "updated_at": iso(f.get("sys_updated_on")),
            "closed_at": iso(f.get("closed_at")),
            "fields": {
                "table": table,
                "sys_id": f["sys_id"],
                "state_value": f.get("state"),
                "active": f.get("active"),
                **{
                    name: {"value": f.get(name), "display": d.get(name, f.get(name))}
                    for name in (
                        "priority",
                        "impact",
                        "urgency",
                        "category",
                        "subcategory",
                        "assignment_group",
                        "assigned_to",
                        "cmdb_ci",
                    )
                    if name in f
                },
                "caller_vip": f.get("caller_id.vip") == "true" if table == "incident" else None,
            },
            "comments": comments,
        }

    async def create_item(
        self, title: str, body: str, fields: Mapping[str, str] | None = None, action_key: str | None = None
    ) -> dict[str, Any]:
        """Files an incident (the only table the Table API should create work items in)."""
        if not title.strip():
            raise Refused("a work item needs a title")
        extra = dict(fields or {})
        if "priority" in extra:
            raise Refused("priority is never written: ServiceNow computes it from impact and urgency")
        refused = sorted(set(extra) - _CREATE_FIELDS)
        if refused:
            raise Refused(f"fields not accepted at creation: {', '.join(refused)}")
        for name, value in extra.items():
            if name in ("assignment_group", "assigned_to", "cmdb_ci", "caller_id") and not is_sys_id(value):
                raise Refused(f"{name} takes a sys_id")
        key = action_key or text_key(title, body)
        marker_of(key)
        correlation = f"choregos:{key}"
        existing = await self.client.find_one("incident", Query("incident").where(eq("correlation_id", correlation)))
        if existing is not None:
            return {"key": existing["fields"]["number"], "sys_id": existing["sys_id"], "created": False}
        values = {"short_description": title, "description": body, "correlation_id": correlation, **extra}
        record = await self.client.create("incident", values)
        number, sys_id = record["fields"]["number"], record["sys_id"]
        return {"key": number, "sys_id": sys_id, "created": True, **unkept(record, values)}

    async def set_state(
        self,
        key: str,
        status: str | None = None,
        label: str | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        table, row = await self._item(key)
        ignored = (["label"] if label else []) + (["remove_labels"] if remove_labels else [])
        if not status:
            return {"key": key, "changed": False, "state": row["display"].get("state"), "ignored": ignored}
        choices = await self.client.choices(table)
        by_label = {lab.lower(): value for value, lab in choices.items()}
        value = by_label.get(status.lower())
        if value is None:
            raise Refused(f"{status!r} is not a state of {table}; states: {', '.join(choices.values())}")
        if value in CLOSED_STATES[table]:
            raise Refused(f"{status!r} closes the item: use tracker_close")
        current = str(row["fields"].get("state"))
        if current in CLOSED_STATES[table]:
            # A closed item stays closed: a state move would reopen it, and on an incident ServiceNow then
            # clears its resolution and counts a reopening. Reopening is `reopen_incident`'s, a write a
            # person approves. The platform mirrors its own states here, and an item may have been closed
            # between its read and this move (graalsystems/tranibor-agents#66): the answer says it, unmoved.
            log.warning("state_move_on_closed_item", table=table, key=key, state=current, asked=value)
            return {
                "key": key,
                "changed": False,
                "state": row["display"].get("state", current),
                "closed": True,
                "ignored": ignored,
            }
        if current == value:
            return {"key": key, "changed": False, "state": choices[value], "ignored": ignored}
        if table == "change_request":
            await self._move_change(row, value, choices[value], {})
        else:
            await self.client.update(table, row["sys_id"], {"state": value})
        return {"key": key, "changed": True, "state": choices[value], "ignored": ignored}

    async def _move_change(self, row: dict[str, Any], value: str, label: str, also: dict[str, Any]) -> None:
        """Through the Change Management API, to a state the change's model reaches from its current one;
        any other is refused before anything is written, naming those it reaches."""
        current = str(row["fields"].get("state"))
        reachable = await self.client.change_next_states(row["sys_id"], current)
        if value not in reachable:
            here = row["display"].get("state", current)
            said = ", ".join(reachable.values()) or "none"
            raise Refused(f"{label!r} is not reachable from {here!r} on this change's model; it reaches: {said}")
        await self.client.update_change(row["sys_id"], {"state": value, **also})

    async def comment(self, key: str, markdown: str, action_key: str | None = None) -> dict[str, Any]:
        table, row = await self._item(key)
        result = await self.tools.add_work_note(table, row["sys_id"], markdown, action_key=action_key)
        return {"key": key, "id": result["marker"], "written": result["written"]}

    async def upsert_status(self, key: str, markdown: str, marker: str) -> dict[str, Any]:
        """Journals are append-only: a new work note is added only when the status text changed."""
        marker_of(marker)
        table, row = await self._item(key)
        tag = f"[choregos-status:{marker}]"
        journal = await self.tools.journal_entries(table, row["sys_id"], ("work_notes",), MAX_ROWS)
        latest = next((e for e in journal if tag in str(e["value"])), None)
        if latest is not None and str(latest["value"]).replace(tag, "").strip() == markdown.strip():
            return {"key": key, "written": False}
        await self.client.update(table, row["sys_id"], {"work_notes": f"{markdown}\n\n{tag}"})
        return {"key": key, "written": True}

    async def close(self, key: str, close_notes: str | None = None, close_code: str | None = None) -> dict[str, Any]:
        table, row = await self._item(key)
        if str(row["fields"].get("state")) in CLOSED_STATES[table]:
            return {"key": key, "closed": False, "state": row["display"].get("state")}
        label, default_code = _CLOSE[table]
        value = await self.client.state_value(table, label)
        if value is None:
            raise Refused(f"{table} has no state {label!r} on this instance")
        values: dict[str, Any] = {"state": value}
        notes = close_notes or "Closed by Tranibor: the work item was delivered."
        if default_code is not None:
            values["close_code"] = close_code or default_code
            values["close_notes"] = notes
        else:
            values["work_notes"] = notes
        if table == "change_request":
            choices = await self.client.choices(table)
            state = values.pop("state")
            await self._move_change(row, state, choices.get(state, label), values)
            return {"key": key, "closed": True, "state": choices.get(state, label)}
        updated = await self.client.update(table, row["sys_id"], values)
        return {"key": key, "closed": True, "state": updated["display"].get("state", value)}

    async def list_candidates(self, tables: list[str] | None = None, limit: int = MAX_ROWS) -> dict[str, Any]:
        """Open items in the assignment queue (`SERVICENOW_ASSIGNMENT_GROUPS`), the most recently updated
        first across the tables, at most `limit` (bounded by `CANDIDATES_BOUND`).

        Each table is read in pages up to that bound, newest first, so a new item is polled whatever waits
        behind it (graalsystems/tranibor-agents#25); `total` is how many the queue holds, and `truncated`
        says the bound left some out."""
        groups = self.client.settings.assignment_groups
        if not groups:
            return {"keys": [], "reason": "no assignment group configured"}
        bound = max(1, min(limit, CANDIDATES_BOUND))
        ids = [g for g in groups if is_sys_id(g)]
        names = [g for g in groups if not is_sys_id(g)]
        found: list[dict[str, Any]] = []
        total: int | None = 0
        truncated = False
        for table in tables or list(TRACKER_TABLES):
            if table not in TRACKER_TABLES:
                raise Refused(f"{table} is not a tracker table ({', '.join(TRACKER_TABLES)})")
            alternatives = []
            if ids:
                alternatives.append(Filter(field="assignment_group", op=Op.in_, value=ids))
            if names:
                alternatives.append(Filter(field="assignment_group.name", op=Op.in_, value=names))
            # An incident stays active once resolved: the closing states are left out by the query, so
            # neither a page nor the total counts them.
            open_only = [Filter(field="state", op=Op.ne, value=v) for v in sorted(CLOSED_STATES[table])]
            query = Query(table).where(eq("active", "true"), *open_only).any_of(*alternatives)
            query.order("sys_updated_on", descending=True)
            read = await self.client.read(table, query, fields=["number", "sys_updated_on"], bound=bound)
            found.extend(read.rows)
            truncated = truncated or read.truncated
            total = None if total is None or read.total is None else total + read.total
        found.sort(key=lambda r: str(r["fields"].get("sys_updated_on") or ""), reverse=True)
        keys = [str(r["fields"]["number"]) for r in found[:bound]]
        truncated = truncated or len(found) > bound
        answer: dict[str, Any] = {"keys": keys, "truncated": truncated}
        if total is not None:
            answer["total"] = total
        if truncated:
            log.warning("candidates_truncated", keys=len(keys), total=total, bound=bound)
        return answer

    def parse_event(self, headers: Mapping[str, str], body: str) -> dict[str, Any]:
        secret = self.client.settings.webhook_secret
        if not verify(headers, secret.get_secret_value() if secret else None):
            return {"verified": False, "events": []}
        return {"verified": True, "events": parse(body.encode())}
