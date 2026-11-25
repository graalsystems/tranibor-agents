# SPDX-License-Identifier: Apache-2.0
"""Pure functions over a zone's records: the plan (desired set against the live zone) and the audit.

Nothing here talks to OVHcloud: `plan_changes` and `audit_zone` read the zone through the tools and hand the
records to these.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from pydantic import BaseModel, Field

from .errors import Refused
from .records import (
    HOST_TAIL_TYPES,
    RECORD_TYPES,
    Record,
    cname_problems,
    host_without_final_dot,
    norm_target,
    normalise_sub_domain,
    same_target,
    validate,
)

LONG_TTL = 300
BIG_TTL = 86_400


class Desired(BaseModel):
    """One record the zone must hold. `ttl` 0 leaves the TTL alone (the zone's default for a new record)."""

    field_type: str
    sub_domain: str = ""
    target: str
    ttl: int = Field(default=0, ge=0)


class Key(BaseModel):
    """A name and a type whose records must all go."""

    field_type: str
    sub_domain: str = ""


def _brief(record: Record) -> dict[str, Any]:
    return {"record_id": record["id"], **{k: record[k] for k in ("field_type", "sub_domain", "target", "ttl")}}


def plan(live: list[Record], desired: list[Desired], clear: list[Key]) -> dict[str, Any]:
    """The diff that makes the zone hold `desired` at every (type, name) it names, and nothing at `clear`.

    Records at other (type, name) pairs are never touched. Within a pair the live and desired sets are
    matched by target (a TTL difference is an update); when exactly one record is left on each side it
    is updated in place, else the extra live ones are deleted and the extra desired ones created.
    """
    errors: list[str] = []
    warnings: list[str] = []
    wanted: dict[tuple[str, str], list[Desired]] = {}
    for index, item in enumerate(desired):
        try:
            sub = normalise_sub_domain(item.sub_domain)
            field_type = item.field_type.strip().upper()
            validate(field_type, sub, item.target, item.ttl)
        except Refused as refused:
            errors.append(f"desired[{index}] ({item.field_type} {item.sub_domain or '@'}): {refused}")
            continue
        entry = Desired(field_type=field_type, sub_domain=sub, target=item.target.strip(), ttl=item.ttl)
        bucket = wanted.setdefault((field_type, sub), [])
        if any(same_target(field_type, entry.target, other.target) for other in bucket):
            warnings.append(f"desired[{index}] repeats {field_type} {sub or '@'} {entry.target}: counted once")
            continue
        bucket.append(entry)
        if host_without_final_dot(field_type, entry.target):
            warnings.append(
                f"{field_type} {sub or '@'}: target {entry.target!r} has no trailing dot; OVHcloud may read it as "
                "relative to the zone. Write an absolute name with its final dot."
            )
    cleared: set[tuple[str, str]] = set()
    for index, key in enumerate(clear):
        try:
            pair = (key.field_type.strip().upper(), normalise_sub_domain(key.sub_domain))
            if pair[0] not in RECORD_TYPES:
                raise Refused(f"field_type {key.field_type!r} is not one of {', '.join(RECORD_TYPES)}")
        except Refused as refused:
            errors.append(f"clear[{index}] ({key.field_type} {key.sub_domain or '@'}): {refused}")
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
        here = sorted((r for r in live if (r["field_type"], r["sub_domain"]) == pair), key=lambda r: r["id"])
        free = list(here)
        rest: list[Desired] = []
        for want in wanted.get(pair, []):
            match = next((r for r in free if same_target(pair[0], r["target"], want.target)), None)
            if match is None:
                rest.append(want)
                continue
            free.remove(match)
            if want.ttl and want.ttl != match["ttl"]:
                updates.append({"record": match, "target": match["target"], "ttl": want.ttl})
            else:
                unchanged.append(match)
        if len(free) == 1 and len(rest) == 1:
            old, new = free[0], rest[0]
            updates.append({"record": old, "target": new.target, "ttl": new.ttl or old["ttl"]})
        else:
            deletes.extend(free)
            creates.extend(rest)

    # The final state of every touched name, for the CNAME rules.
    touched = {pair[1] for pair in set(wanted) | cleared}
    removed = {r["id"] for r in deletes}
    changed = {u["record"]["id"]: u for u in updates}
    final: list[Record] = []
    written: set[int] = set()
    for record in live:
        if record["sub_domain"] not in touched or record["id"] in removed:
            continue
        if record["id"] in changed:
            record = {**record, "target": changed[record["id"]]["target"]}
            written.add(record["id"])
        final.append(record)
    for index, new in enumerate(creates):
        final.append({"id": -1 - index, **new.model_dump()})
        written.add(-1 - index)
    for name, why in cname_problems(final).items():
        touched_here = [r for r in final if r["sub_domain"] == name and r["id"] in written]
        (errors if touched_here else warnings).append(
            why + (" (already so in the live zone)" if not touched_here else "")
        )

    for record in [*deletes, *(u["record"] for u in updates)]:
        if record["ttl"] > LONG_TTL:
            warnings.append(
                f"{record['field_type']} {record['sub_domain'] or '@'} (id {record['id']}) has a TTL of "
                f"{record['ttl']} s: after the refresh, resolvers may keep the old answer that long"
            )
    propagation = max([r["ttl"] for r in deletes] + [u["record"]["ttl"] for u in updates] + [0])

    calls: list[dict[str, Any]] = []
    if not errors:
        calls += [{"tool": "delete_record", "arguments": {"record_id": r["id"]}} for r in deletes]
        for u in updates:
            arguments: dict[str, Any] = {"record_id": u["record"]["id"]}
            if not same_target(u["record"]["field_type"], u["record"]["target"], u["target"]):
                arguments["target"] = u["target"]
            if u["ttl"] != u["record"]["ttl"]:
                arguments["ttl"] = u["ttl"]
            calls.append({"tool": "update_record", "arguments": arguments})
        calls += [
            {
                "tool": "create_record",
                "arguments": {"field_type": c.field_type, "sub_domain": c.sub_domain, "target": c.target, "ttl": c.ttl},
            }
            for c in creates
        ]
        if calls:
            calls.append({"tool": "refresh_zone", "arguments": {}})
    return {
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
                **{k: u["record"][k] for k in ("field_type", "sub_domain")},
                "record_id": u["record"]["id"],
                "before": {"target": u["record"]["target"], "ttl": u["record"]["ttl"]},
                "after": {"target": u["target"], "ttl": u["ttl"]},
            }
            for u in updates
        ],
        "deletes": [_brief(r) for r in deletes],
        "unchanged": [_brief(r) for r in unchanged],
        "needs_refresh": bool(creates or updates or deletes),
        "propagation_seconds_after_refresh": propagation,
        "proposed_calls": calls,
    }


def _in_zone(zone: str, host: str) -> str | None:
    """The sub-domain an absolute host name denotes inside `zone`, or None when it is outside it."""
    name = host.strip().lower()
    if not name.endswith("."):
        return None  # relative or unknown: not judged
    name = name.rstrip(".")
    if name == zone:
        return ""
    return name.removesuffix("." + zone) if name.endswith("." + zone) else None


def audit_zone(zone: str, records: list[Record], status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Hygiene findings from the zone's own records. Candidates, not verdicts: the zone cannot say whether a
    target OUTSIDE it exists, nor whether an address is still in use."""
    findings: list[dict[str, str]] = []

    def add(severity: str, code: str, name: str, detail: str) -> None:
        findings.append({"severity": severity, "code": code, "name": name or "@", "detail": detail})

    names = {r["sub_domain"] for r in records}
    for name, why in cname_problems(records).items():
        add("error", "cname_rule", name, why)
    seen: dict[tuple[str, str, str], int] = {}
    for r in records:
        key = (r["field_type"], r["sub_domain"], norm_target(r["field_type"], r["target"]))
        if key in seen:
            add("warning", "duplicate", r["sub_domain"], f"{r['field_type']} id {r['id']} repeats id {seen[key]}")
        seen[key] = r["id"]
        if r["field_type"] in HOST_TAIL_TYPES:
            host = r["target"].strip().split()[-1] if r["target"].strip() else ""
            inside = _in_zone(zone, host)
            exists = inside is not None and (
                inside in names or any(n.startswith("*.") and inside.endswith(n[1:]) for n in names)
            )
            if inside is not None and not exists and r["field_type"] != "NS":
                add(
                    "warning",
                    "dangling_candidate",
                    r["sub_domain"],
                    f"{r['field_type']} id {r['id']} points to {host}, a name inside this zone that holds no record",
                )
            if host_without_final_dot(r["field_type"], r["target"]):
                add("info", "relative_target", r["sub_domain"], f"id {r['id']}: {host!r} has no trailing dot")
        if r["ttl"] >= BIG_TTL:
            add("info", "long_ttl", r["sub_domain"], f"{r['field_type']} id {r['id']} has a TTL of {r['ttl']} s")
    spf = [
        r for r in records if r["sub_domain"] == "" and norm_target(r["field_type"], r["target"]).startswith("v=spf1")
    ]
    if len(spf) > 1:
        add(
            "error",
            "multiple_spf",
            "",
            f"{len(spf)} SPF records at the apex (ids {[r['id'] for r in spf]}); only one may exist",
        )
    mail = any(r["field_type"] == "MX" and r["sub_domain"] == "" for r in records)
    if mail and not spf:
        add("warning", "no_spf", "", "the apex receives mail (MX) but holds no SPF record")
    if mail and not any(r["sub_domain"] == "_dmarc" for r in records):
        add("warning", "no_dmarc", "_dmarc", "the apex receives mail (MX) but the zone holds no _dmarc record")
    if not any(r["field_type"] == "CAA" and r["sub_domain"] == "" for r in records):
        add("info", "no_caa", "", "no CAA record at the apex: any certificate authority may issue for the zone")
    addresses: dict[str, list[str]] = {}
    for r in records:
        if r["field_type"] in ("A", "AAAA"):
            try:
                ip = str(ipaddress.ip_address(r["target"].strip()))
            except ValueError:
                add("error", "bad_address", r["sub_domain"], f"id {r['id']}: {r['target']!r} is not an address")
                continue
            addresses.setdefault(ip, []).append(r["sub_domain"] or "@")
    for ip, hosts in sorted(addresses.items()):
        add(
            "info",
            "address_inventory",
            ",".join(sorted(hosts)),
            f"{ip} is the target of: {', '.join(sorted(hosts))} (its use cannot be known from the zone)",
        )
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["code"], f["name"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in order}
    return {
        "zone": zone,
        "records": len(records),
        "counts": counts,
        "findings": findings,
        "status": status,
        "limits": "Candidates from the zone's own records only: a target outside the zone, or an address's use, "
        "cannot be known from it.",
    }
