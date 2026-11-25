# SPDX-License-Identifier: Apache-2.0
"""Record shapes of the `dns` profile: validation of what is written, normalisation of what is compared.

PROVIDER-NEUTRAL: this file and `plan.py` are identical in every implementation of the profile and read
what differs from `rules.py`. A record is {id, zone, name, type, ttl, value}: ONE value at one name, `name`
relative to the zone ("" is the apex), `ttl` 0 when the provider's default applies.

What the provider accepts is its own business and it answers an error; this module refuses early what is
wrong in DNS itself (a CNAME at the apex, an address that is not one) and what no agent should write (the
zone's own NS at the apex). The comparison form of a value is OUR convention: whether a provider stores a TXT
value with its quotes, or a host name with its final dot, is not always documented, so both spellings are
treated as equal here and the plan warns about a host name without a trailing dot.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from .errors import Refused
from .rules import RULES

#: Types whose value ends with a host name.
HOST_TAIL_TYPES = RULES.host_types | {"MX", "SRV"}
MAX_VALUE = 4096
LABEL = re.compile(r"^[A-Za-z0-9_*]([A-Za-z0-9_*-]{0,61}[A-Za-z0-9_*])?$")

Record = dict[str, Any]


def normalise_name(name: str) -> str:
    """`""` is the apex; lower case; no dots at either end."""
    value = name.strip().lower().strip(".")
    if value == "@":
        return ""
    if value and (len(value) > 253 or not all(LABEL.fullmatch(label) for label in value.split("."))):
        raise Refused(f"name {name!r} is not a valid name (labels of letters, digits, _ and -; * allowed)")
    return value


def norm_value(type: str, value: str) -> str:
    """The comparison form of a value."""
    text = value.strip()
    if type in RULES.text_types:
        if len(text) >= 2 and text[0] == text[-1] == '"':
            text = text[1:-1]
        return text
    if type == "AAAA":
        try:
            return ipaddress.IPv6Address(text).compressed
        except ValueError:
            return text.lower()
    if type in RULES.host_types:
        return text.lower().rstrip(".")
    if type in ("MX", "SRV"):
        parts = text.split()
        if parts:
            parts[-1] = parts[-1].lower().rstrip(".")
        return " ".join(parts)
    return " ".join(text.split())


def host_without_final_dot(type: str, value: str) -> bool:
    """A host name written without its trailing dot, where the provider may read it as relative to the zone."""
    if not RULES.warn_relative_host or type not in HOST_TAIL_TYPES:
        return False
    tail = value.strip().split()[-1] if value.strip() else ""
    return bool(tail) and not tail.endswith(".") and not _is_ip(tail)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def validate(type: str, name: str, value: str, ttl: int) -> None:
    """Refuses a record that is wrong in DNS itself. `name` is already normalised, `type` upper-cased."""
    if type not in RULES.types:
        raise Refused(f"type {type!r} is not one of {', '.join(RULES.types)}")
    if ttl != 0 and not RULES.min_ttl <= ttl <= RULES.max_ttl:
        raise Refused(
            f"ttl {ttl} is outside {RULES.min_ttl}..{RULES.max_ttl} (0 leaves {RULES.provider}'s default TTL)"
        )
    text = value.strip()
    if not text or len(text) > MAX_VALUE or any(ord(c) < 32 for c in text):
        raise Refused("value is empty, too long, or holds a control character")
    if type == "CNAME" and name == "":
        raise Refused("a CNAME at the apex is forbidden (it cannot coexist with the zone's SOA and NS records)")
    if type == "NS" and name == "":
        raise Refused("the zone's own NS records belong to the registrar's delegation, not to this server")
    if type == "A":
        try:
            ipaddress.IPv4Address(text)
        except ValueError:
            raise Refused(f"value {text!r} is not an IPv4 address") from None
    elif type == "AAAA":
        try:
            ipaddress.IPv6Address(text)
        except ValueError:
            raise Refused(f"value {text!r} is not an IPv6 address") from None
    elif type in RULES.host_types and len(text.split()) != 1:
        raise Refused(f"a {type} value is one host name, not {text!r}")
    elif type == "MX":
        parts = text.split()
        if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) > 65535:
            raise Refused(f"an MX value is '<priority> <host>', not {text!r}")


def same_value(type: str, left: str, right: str) -> bool:
    return norm_value(type, left) == norm_value(type, right)


def cname_problems(records: list[Record]) -> dict[str, str]:
    """Per name, why its final set breaks the CNAME rules (empty when it does not)."""
    by_name: dict[str, list[Record]] = {}
    for record in records:
        by_name.setdefault(record["name"], []).append(record)
    problems: dict[str, str] = {}
    for name, group in by_name.items():
        cnames = [r for r in group if r["type"] == "CNAME"]
        if not cnames:
            continue
        label = name or "the apex"
        if name == "":
            problems[name] = "a CNAME at the apex is forbidden"
        elif len(cnames) > 1:
            problems[name] = f"{label} would hold {len(cnames)} CNAME records; a name has at most one"
        elif len(group) > 1:
            others = sorted({r["type"] for r in group if r["type"] != "CNAME"})
            problems[name] = f"{label} would hold a CNAME beside {', '.join(others)}; a CNAME is alone at its name"
    return problems
