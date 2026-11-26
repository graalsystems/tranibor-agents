# SPDX-License-Identifier: Apache-2.0
"""The fake instance's seed, read from a file (`SERVICENOW_FAKE_SEED=<path>`).

The built-in seed (`fake.seed`) stays the default. With a file, the instance holds exactly what the file
says, so a demo shows its own incidents. The file is JSON; every section is optional and every name is
checked before the server starts (a typo is a `SeedError` naming where, never an empty instance):

```json
{
  "choices":   [{"name": "incident", "element": "state", "value": "1", "label": "New"}],
  "users":     [{"user_name": "ada.byron", "name": "Ada Byron", "vip": false}],
  "groups":    [{"name": "Service Desk", "description": "First line", "manager": "ada.byron"}],
  "cis":       [{"name": "mail-01", "class": "cmdb_ci_email_server", "group": "Service Desk",
                 "description": "Mail server"}],
  "ci_relations": [{"parent": "app-01", "child": "mail-01", "type": "Depends on::Used by"}],
  "knowledge": [{"number": "KB0000001", "title": "…", "text": "…", "state": "published"}],
  "incidents": [{"number": "INC0000001", "title": "…", "description": "…", "state": "1", "impact": "3",
                 "urgency": "3", "group": "Service Desk", "caller": "ada.byron", "ci": "mail-01",
                 "category": "software", "created_days_ago": 0.5, "resolved_days_ago": null,
                 "close_notes": "",
                 "journal": [{"element": "work_notes", "value": "…", "days_ago": 0.4, "author": "ada.byron"}]}]
}
```

`choices` replaces the built-in `sys_choice` rows (states, impacts, urgencies, priorities) when present;
absent, the built-in ones are used. A group's `manager`, an incident's `caller`, `group` and `ci`, a CI's
`group` and a relation's `parent` and `child` name rows of the same file. Dates are days before now, so a
seed never goes stale. `similar_incidents` looks at incidents resolved in the last 90 days.
`server/seeds/sample-company.json` is a complete example (the image carries it at `/app/seeds/`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .fake import _PRIORITY, _ago, incident_writer, seed_choices, sys_id_of

if TYPE_CHECKING:
    from .fake import FakeServiceNow

SECTIONS = frozenset({"choices", "users", "groups", "cis", "ci_relations", "knowledge", "incidents"})
_KB_STATES = ("draft", "review", "published", "pending_retirement", "retired", "outdated")
_INCIDENT_KEYS = frozenset(
    {
        "number", "title", "description", "state", "impact", "urgency", "group", "caller", "ci", "category",
        "created_days_ago", "resolved_days_ago", "close_notes", "journal",
    }
)  # fmt: skip


class SeedError(ValueError):
    """The seed file cannot be loaded; the message says which file and which entry."""


def _text(entry: Mapping[str, Any], key: str, where: str, *, required: bool = True, default: str = "") -> str:
    value = entry.get(key, None)
    if value is None:
        if required:
            raise SeedError(f"{where}: `{key}` is required")
        return default
    if not isinstance(value, str) or (required and not value.strip()):
        raise SeedError(f"{where}: `{key}` must be a non-empty string")
    return value


def _days(entry: Mapping[str, Any], key: str, where: str, default: float | None) -> float | None:
    value = entry.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise SeedError(f"{where}: `{key}` must be a number of days, zero or more")
    return float(value)


def _section(doc: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    rows = doc.get(name, [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SeedError(f"`{name}` must be a list of objects")
    return rows


def _unique(names: list[str], section: str) -> set[str]:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise SeedError(f"`{section}`: `{name}` appears twice")
        seen.add(name)
    return seen


def _known(value: str, known: set[str], where: str, key: str, section: str) -> None:
    if value and value not in known:
        raise SeedError(f"{where}: `{key}` names `{value}`, which is not in `{section}`")


def read_seed_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    """The file's JSON document, or a `SeedError`."""
    file = Path(path)
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SeedError(f"SERVICENOW_FAKE_SEED={path}: cannot be read ({exc.strerror or exc})") from exc
    except ValueError as exc:
        raise SeedError(f"SERVICENOW_FAKE_SEED={path}: not valid JSON ({exc})") from exc
    if not isinstance(doc, dict):
        raise SeedError(f"SERVICENOW_FAKE_SEED={path}: the document must be a JSON object")
    unknown = sorted(set(doc) - SECTIONS)
    if unknown:
        raise SeedError(f"SERVICENOW_FAKE_SEED={path}: unknown section(s) {', '.join(unknown)}; known: "
                        + ", ".join(sorted(SECTIONS)))  # fmt: skip
    return doc


def load_seed_file(fake: FakeServiceNow, path: str | os.PathLike[str]) -> None:
    """Fills `fake` from the file; nothing of the built-in seed is added."""
    try:
        apply_seed(fake, read_seed_file(path))
    except SeedError as exc:
        if str(exc).startswith("SERVICENOW_FAKE_SEED="):
            raise
        raise SeedError(f"SERVICENOW_FAKE_SEED={path}: {exc}") from exc


def apply_seed(fake: FakeServiceNow, doc: Mapping[str, Any]) -> None:
    ins = fake.insert
    # A real instance answers 400 on a table it does not have: every table the tools read exists, empty or not.
    for table in ("sys_choice", "sys_user", "sys_user_group", "cmdb_ci", "cmdb_rel_type", "cmdb_rel_ci",
                  "kb_knowledge", "incident", "sc_req_item", "change_request", "problem",
                  "sys_journal_field"):  # fmt: skip
        fake.tables.setdefault(table, {})

    choices = _section(doc, "choices")
    if choices:
        for i, row in enumerate(choices):
            where = f"choices[{i}]"
            name = _text(row, "name", where)
            element = _text(row, "element", where)
            value = _text(row, "value", where)
            label = _text(row, "label", where)
            ins("sys_choice", {"sys_id": sys_id_of(f"choice:{name}:{element}:{value}"), "name": name,
                               "element": element, "value": value, "label": label,
                               "sequence": str(row.get("sequence", i)), "inactive": "false",
                               "language": "en"})  # fmt: skip
    else:
        seed_choices(fake)

    users = _section(doc, "users")
    user_names = _unique([_text(u, "user_name", f"users[{i}]") for i, u in enumerate(users)], "users")
    groups = _section(doc, "groups")
    group_names = _unique([_text(g, "name", f"groups[{i}]") for i, g in enumerate(groups)], "groups")
    cis = _section(doc, "cis")
    ci_names = _unique([_text(c, "name", f"cis[{i}]") for i, c in enumerate(cis)], "cis")

    for i, u in enumerate(users):
        where = f"users[{i}]"
        vip = u.get("vip", False)
        if not isinstance(vip, bool):
            raise SeedError(f"{where}: `vip` must be true or false")
        user = _text(u, "user_name", where)
        ins("sys_user", {"sys_id": sys_id_of(f"user:{user}"), "user_name": user,
                         "name": _text(u, "name", where, required=False, default=user),
                         "vip": "true" if vip else "false"})  # fmt: skip
    for i, g in enumerate(groups):
        where = f"groups[{i}]"
        manager = _text(g, "manager", where, required=False)
        _known(manager, user_names, where, "manager", "users")
        ins("sys_user_group", {"sys_id": sys_id_of(f"group:{g['name']}"), "name": g["name"],
                               "description": _text(g, "description", where, required=False),
                               "active": "true",
                               "manager": sys_id_of(f"user:{manager}") if manager else ""})  # fmt: skip
    for i, c in enumerate(cis):
        where = f"cis[{i}]"
        group = _text(c, "group", where, required=False)
        _known(group, group_names, where, "group", "groups")
        ins("cmdb_ci", {"sys_id": sys_id_of(f"ci:{c['name']}"), "name": c["name"],
                        "sys_class_name": _text(c, "class", where, required=False, default="cmdb_ci"),
                        "operational_status": "1", "install_status": "1", "ip_address": "",
                        "fqdn": f"{c['name']}.example.test",
                        "support_group": sys_id_of(f"group:{group}") if group else "", "owned_by": "",
                        "location": "",
                        "short_description": _text(c, "description", where, required=False)})  # fmt: skip

    relations = _section(doc, "ci_relations")
    rel_types: set[str] = set()
    for i, r in enumerate(relations):
        where = f"ci_relations[{i}]"
        parent, child = _text(r, "parent", where), _text(r, "child", where)
        rel_type = _text(r, "type", where, required=False, default="Depends on::Used by")
        _known(parent, ci_names, where, "parent", "cis")
        _known(child, ci_names, where, "child", "cis")
        if rel_type not in rel_types:
            rel_types.add(rel_type)
            ins("cmdb_rel_type", {"sys_id": sys_id_of(f"reltype:{rel_type}"), "name": rel_type})
        ins("cmdb_rel_ci", {"sys_id": sys_id_of(f"rel:{parent}:{child}"), "parent": sys_id_of(f"ci:{parent}"),
                            "child": sys_id_of(f"ci:{child}"), "type": sys_id_of(f"reltype:{rel_type}")})  # fmt: skip

    articles = _section(doc, "knowledge")
    _unique([_text(a, "number", f"knowledge[{i}]") for i, a in enumerate(articles)], "knowledge")
    for i, a in enumerate(articles):
        where = f"knowledge[{i}]"
        state = _text(a, "state", where, required=False, default="published")
        if state not in _KB_STATES:
            raise SeedError(f"{where}: `state` must be one of {', '.join(_KB_STATES)}")
        ins("kb_knowledge", {"sys_id": sys_id_of(a["number"]), "number": a["number"],
                             "short_description": _text(a, "title", where), "text": _text(a, "text", where),
                             "workflow_state": state, "kb_category": "", "valid_to": "2100-01-01",
                             "active": "true", "sys_updated_on": _ago(60)})  # fmt: skip

    incidents = _section(doc, "incidents")
    numbers = _unique([_text(r, "number", f"incidents[{i}]") for i, r in enumerate(incidents)], "incidents")
    if numbers & {a["number"] for a in articles}:
        raise SeedError("`incidents` and `knowledge` share a number")
    write: Callable[..., None] = incident_writer(fake)
    for i, row in enumerate(incidents):
        where = f"incidents[{i}] ({row['number']})"
        unknown = sorted(set(row) - _INCIDENT_KEYS)
        if unknown:
            raise SeedError(f"{where}: unknown key(s) {', '.join(unknown)}")
        group = _text(row, "group", where, required=False, default=sorted(group_names)[0] if group_names else "")
        caller = _text(row, "caller", where, required=False)
        ci = _text(row, "ci", where, required=False)
        _known(group, group_names, where, "group", "groups")
        _known(caller, user_names, where, "caller", "users")
        _known(ci, ci_names, where, "ci", "cis")
        impact = _text(row, "impact", where, required=False, default="3")
        urgency = _text(row, "urgency", where, required=False, default="3")
        if (impact, urgency) not in _PRIORITY:
            raise SeedError(f"{where}: `impact` and `urgency` must each be 1, 2 or 3")
        write(
            row["number"],
            _text(row, "title", where),
            _text(row, "description", where, required=False),
            state=_text(row, "state", where, required=False, default="1"),
            impact=impact,
            urgency=urgency,
            group=group,
            caller=caller,
            ci=ci or None,
            category=_text(row, "category", where, required=False, default="inquiry"),
            created_days=_days(row, "created_days_ago", where, 1),
            resolved_days=_days(row, "resolved_days_ago", where, None),
            close_notes=_text(row, "close_notes", where, required=False),
        )
        entries = row.get("journal", [])
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise SeedError(f"{where}: `journal` must be a list of objects")
        for seq, entry in enumerate(entries, start=1):
            at = f"{where} journal[{seq - 1}]"
            element = _text(entry, "element", at)
            if element not in ("work_notes", "comments"):
                raise SeedError(f"{at}: `element` must be work_notes or comments")
            ins("sys_journal_field", {"sys_id": sys_id_of(f"journal:{row['number']}:{seq}"),
                                      "element_id": sys_id_of(row["number"]), "name": "incident",
                                      "element": element, "value": _text(entry, "value", at),
                                      "sys_created_on": _ago(_days(entry, "days_ago", at, 0) or 0),
                                      "sys_created_by": _text(entry, "author", at, required=False)})  # fmt: skip
