# SPDX-License-Identifier: Apache-2.0
"""The plan: the desired set of records against the live zone. A pure function.

PROVIDER-NEUTRAL (see `records.py`): the tools read the zone, hand its records here, and answer the result.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .errors import Refused
from .records import (
    Record,
    cname_problems,
    host_without_final_dot,
    normalise_name,
    same_value,
    validate,
)
from .rules import RULES

LONG_TTL = 300


class Desired(BaseModel):
    """One record the zone must hold. `ttl` 0 leaves the TTL alone (the provider's default for a new record)."""

    type: str
    name: str = ""
    value: str
    ttl: int = Field(default=0, ge=0)


class Key(BaseModel):
    """A name and a type whose records must all go."""

    type: str
    name: str = ""


def _brief(record: Record) -> dict[str, Any]:
    return {k: record[k] for k in ("id", "zone", "name", "type", "ttl", "value")}


def plan(
    zone: str, live: list[Record], desired: list[Desired], clear: list[Key], *, stages_changes: bool
) -> dict[str, Any]:
    """The diff that makes the zone hold `desired` at every (type, name) it names, and nothing at `clear`.

    Records at other (type, name) pairs are never touched. Within a pair the live and desired sets are
    matched by value (a TTL difference is an update); when exactly one record is left on each side it is
    updated in place, else the extra live ones are deleted and the extra desired ones created.
    """
    errors: list[str] = []
    warnings: list[str] = []
    wanted: dict[tuple[str, str], list[Desired]] = {}
    for index, item in enumerate(desired):
        try:
            name = normalise_name(item.name)
            kind = item.type.strip().upper()
            validate(kind, name, item.value, item.ttl)
        except Refused as refused:
            errors.append(f"desired[{index}] ({item.type} {item.name or '@'}): {refused}")
            continue
        entry = Desired(type=kind, name=name, value=item.value.strip(), ttl=item.ttl)
        bucket = wanted.setdefault((kind, name), [])
        if any(same_value(kind, entry.value, other.value) for other in bucket):
            warnings.append(f"desired[{index}] repeats {kind} {name or '@'} {entry.value}: counted once")
            continue
        bucket.append(entry)
        if host_without_final_dot(kind, entry.value):
            warnings.append(
                f"{kind} {name or '@'}: value {entry.value!r} has no trailing dot; {RULES.provider} may read it as "
                "relative to the zone. Write an absolute name with its final dot."
            )
    cleared: set[tuple[str, str]] = set()
    for index, key in enumerate(clear):
        try:
            pair = (key.type.strip().upper(), normalise_name(key.name))
            if pair[0] not in RULES.types:
                raise Refused(f"type {key.type!r} is not one of {', '.join(RULES.types)}")
        except Refused as refused:
            errors.append(f"clear[{index}] ({key.type} {key.name or '@'}): {refused}")
            continue
        if pair[0] == "NS" and pair[1] == "":
            errors.append(f"clear[{index}]: the zone's own NS records are never cleared by this server")
            continue
        if pair in wanted:
            errors.append(f"clear[{index}] and desired both name {pair[0]} {pair[1] or '@'}: say one or the other")
            continue
        cleared.add(pair)

    creates: list[Desired] = []
    updates: list[dict[str, Any]] = []
    deletes: list[Record] = []
    unchanged: list[Record] = []
    for pair in sorted(set(wanted) | cleared):
        here = sorted((r for r in live if (r["type"], r["name"]) == pair), key=lambda r: str(r["id"]))
        free = list(here)
        rest: list[Desired] = []
        for want in wanted.get(pair, []):
            match = next((r for r in free if same_value(pair[0], r["value"], want.value)), None)
            if match is None:
                rest.append(want)
                continue
            free.remove(match)
            if want.ttl and want.ttl != match["ttl"]:
                updates.append({"record": match, "value": match["value"], "ttl": want.ttl})
            else:
                unchanged.append(match)
        if len(free) == 1 and len(rest) == 1:
            old, new = free[0], rest[0]
            updates.append({"record": old, "value": new.value, "ttl": new.ttl or old["ttl"]})
        else:
            deletes.extend(free)
            creates.extend(rest)

    # The final state of every touched name, for the CNAME rules.
    touched = {pair[1] for pair in set(wanted) | cleared}
    removed = {r["id"] for r in deletes}
    changed = {u["record"]["id"]: u for u in updates}
    final: list[Record] = []
    written: set[str] = set()
    for record in live:
        if record["name"] not in touched or record["id"] in removed:
            continue
        if record["id"] in changed:
            record = {**record, "value": changed[record["id"]]["value"]}
            written.add(record["id"])
        final.append(record)
    for index, new in enumerate(creates):
        final.append({"id": f"new-{index}", "zone": zone, **new.model_dump()})
        written.add(f"new-{index}")
    for name, why in cname_problems(final).items():
        touched_here = [r for r in final if r["name"] == name and r["id"] in written]
        (errors if touched_here else warnings).append(
            why + (" (already so in the live zone)" if not touched_here else "")
        )

    for record in [*deletes, *(u["record"] for u in updates)]:
        if record["ttl"] > LONG_TTL:
            warnings.append(
                f"{record['type']} {record['name'] or '@'} (id {record['id']}) has a TTL of {record['ttl']} s: once "
                "the change is public, resolvers may keep the old answer that long"
            )
    propagation = max([r["ttl"] for r in deletes] + [u["record"]["ttl"] for u in updates] + [0])
    changes = bool(creates or updates or deletes)

    calls: list[dict[str, Any]] = []
    if not errors:
        calls += [{"tool": "delete_record", "arguments": {"zone": zone, "id": r["id"]}} for r in deletes]
        for u in updates:
            arguments: dict[str, Any] = {"zone": zone, "id": u["record"]["id"]}
            if not same_value(u["record"]["type"], u["record"]["value"], u["value"]):
                arguments["value"] = u["value"]
            if u["ttl"] != u["record"]["ttl"]:
                arguments["ttl"] = u["ttl"]
            calls.append({"tool": "update_record", "arguments": arguments})
        calls += [
            {
                "tool": "create_record",
                "arguments": {"zone": zone, "name": c.name, "type": c.type, "value": c.value, "ttl": c.ttl},
            }
            for c in creates
        ]
        if calls and stages_changes:
            calls.append({"tool": "publish", "arguments": {"zone": zone}})
    return {
        "zone": zone,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "create": len(creates),
            "update": len(updates),
            "delete": len(deletes),
            "unchanged": len(unchanged),
        },
        "creates": [c.model_dump() for c in creates],
        "updates": [
            {
                "id": u["record"]["id"],
                "type": u["record"]["type"],
                "name": u["record"]["name"],
                "before": {"value": u["record"]["value"], "ttl": u["record"]["ttl"]},
                "after": {"value": u["value"], "ttl": u["ttl"]},
            }
            for u in updates
        ],
        "deletes": [_brief(r) for r in deletes],
        "needs_publish": changes and stages_changes,
        "propagation_seconds_after_change": propagation,
        "proposed_calls": calls,
    }
