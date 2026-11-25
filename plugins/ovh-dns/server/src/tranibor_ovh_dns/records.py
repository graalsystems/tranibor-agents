# SPDX-License-Identifier: Apache-2.0
"""Record shapes: validation of what is written, and normalisation of what is compared.

What OVHcloud accepts is its own business and it answers an error; this module refuses early what is
wrong in DNS itself (a CNAME at the apex, an address that is not one) and what no agent should write
(the zone's own NS at the apex). The comparison form of a target is OUR convention: OVHcloud's exact
storage of a TXT value (quotes) or of a relative host name (no trailing dot) is NOT confirmed in its
documentation, so both spellings are treated as equal here and the plan warns about a host name without a
trailing dot.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from .errors import Refused

#: `domain.zone.RecordTypeEnum` of https://eu.api.ovh.com/1.0/domain.json.
RECORD_TYPES = (
    "A", "AAAA", "CAA", "CNAME", "DKIM", "DMARC", "DNAME", "HTTPS", "LOC", "MX", "NAPTR",
    "NS", "PTR", "RP", "SPF", "SRV", "SSHFP", "SVCB", "TLSA", "TXT",
)  # fmt: skip
TEXT_TYPES = {"TXT", "SPF", "DKIM", "DMARC"}
HOST_TYPES = {"CNAME", "NS", "DNAME", "PTR"}
#: Types whose target ends with a host name.
HOST_TAIL_TYPES = HOST_TYPES | {"MX", "SRV"}
MAX_TTL = 604_800  # a sanity bound of this server (7 days), not an OVHcloud rule
MAX_TARGET = 4096
LABEL = re.compile(r"^[A-Za-z0-9_*]([A-Za-z0-9_*-]{0,61}[A-Za-z0-9_*])?$")

Record = dict[str, Any]


def normalise_sub_domain(sub_domain: str) -> str:
    """`""` is the apex; lower case; no dots at either end."""
    value = sub_domain.strip().lower().strip(".")
    if value == "@":
        return ""
    if value and (len(value) > 253 or not all(LABEL.fullmatch(label) for label in value.split("."))):
        raise Refused(f"sub_domain {sub_domain!r} is not a valid name (labels of letters, digits, _ and -; * allowed)")
    return value


def norm_target(field_type: str, target: str) -> str:
    """The comparison form of a target."""
    value = target.strip()
    if field_type in TEXT_TYPES:
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        return value
    if field_type == "AAAA":
        try:
            return ipaddress.IPv6Address(value).compressed
        except ValueError:
            return value.lower()
    if field_type in HOST_TYPES:
        return value.lower().rstrip(".")
    if field_type in ("MX", "SRV"):
        parts = value.split()
        if parts:
            parts[-1] = parts[-1].lower().rstrip(".")
        return " ".join(parts)
    return " ".join(value.split())


def host_without_final_dot(field_type: str, target: str) -> bool:
    """A host name written without its trailing dot: OVHcloud may read it as relative to the zone
    (unconfirmed in the API documentation)."""
    if field_type not in HOST_TAIL_TYPES:
        return False
    tail = target.strip().split()[-1] if target.strip() else ""
    return bool(tail) and not tail.endswith(".") and not _is_ip(tail)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def validate(field_type: str, sub_domain: str, target: str, ttl: int) -> None:
    """Refuses a record that is wrong in DNS itself. `sub_domain` is already normalised."""
    if field_type not in RECORD_TYPES:
        raise Refused(f"field_type {field_type!r} is not one of {', '.join(RECORD_TYPES)}")
    if not 0 <= ttl <= MAX_TTL:
        raise Refused(f"ttl {ttl} is outside 0..{MAX_TTL} (0 leaves the zone's default TTL)")
    value = target.strip()
    if not value or len(value) > MAX_TARGET or any(ord(c) < 32 for c in value):
        raise Refused("target is empty, too long, or holds a control character")
    if field_type == "CNAME" and sub_domain == "":
        raise Refused("a CNAME at the apex is forbidden (it cannot coexist with the zone's SOA and NS records)")
    if field_type == "NS" and sub_domain == "":
        raise Refused("the zone's own NS records belong to the registrar's delegation, not to this server")
    if field_type == "A":
        try:
            ipaddress.IPv4Address(value)
        except ValueError:
            raise Refused(f"target {value!r} is not an IPv4 address") from None
    elif field_type == "AAAA":
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            raise Refused(f"target {value!r} is not an IPv6 address") from None
    elif field_type in HOST_TYPES and len(value.split()) != 1:
        raise Refused(f"a {field_type} target is one host name, not {value!r}")
    elif field_type == "MX":
        parts = value.split()
        if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) > 65535:
            raise Refused(f"an MX target is '<priority> <host>', not {value!r}")


def same_target(field_type: str, left: str, right: str) -> bool:
    return norm_target(field_type, left) == norm_target(field_type, right)


def from_ovh(raw: dict[str, Any]) -> Record:
    """OVHcloud's `domain.zone.Record` as this server returns it (ttl 0 when the zone's default applies)."""
    return {
        "id": int(raw["id"]),
        "field_type": str(raw["fieldType"]),
        "sub_domain": str(raw.get("subDomain") or ""),
        "target": str(raw["target"]),
        "ttl": int(raw.get("ttl") or 0),
    }


def cname_problems(records: list[Record]) -> dict[str, str]:
    """Per name, why its final set breaks the CNAME rules (empty when it does not)."""
    by_name: dict[str, list[Record]] = {}
    for record in records:
        by_name.setdefault(record["sub_domain"], []).append(record)
    problems: dict[str, str] = {}
    for name, group in by_name.items():
        cnames = [r for r in group if r["field_type"] == "CNAME"]
        if not cnames:
            continue
        label = name or "the apex"
        if name == "":
            problems[name] = "a CNAME at the apex is forbidden"
        elif len(cnames) > 1:
            problems[name] = f"{label} would hold {len(cnames)} CNAME records; a name has at most one"
        elif len(group) > 1:
            others = sorted({r["field_type"] for r in group if r["field_type"] != "CNAME"})
            problems[name] = f"{label} would hold a CNAME beside {', '.join(others)}; a CNAME is alone at its name"
    return problems
