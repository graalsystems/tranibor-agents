# SPDX-License-Identifier: Apache-2.0
"""What THIS provider accepts. `records.py` and `plan.py` are provider-neutral (the same file serves every
implementation of the `dns` profile) and read their limits from here."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rules:
    provider: str
    types: tuple[str, ...]
    text_types: frozenset[str]
    host_types: frozenset[str]
    min_ttl: int  #: the smallest explicit TTL the provider takes (0 is always "the provider's default")
    max_ttl: int
    #: Whether a host name written without its final dot is worth a warning (the provider may read it as relative).
    warn_relative_host: bool


#: The record types of GoDaddy's Domains v3 DNS routes (developer.godaddy.com, "DNS records"). SOA is listed by
#: GoDaddy but never writable (409 `dns_record_not_mutable`), as is NS at the apex: the profile's tools never
#: write either, so neither is offered here. TTL 600-86400 is the documented range of a record.
RULES = Rules(
    provider="GoDaddy",
    types=("A", "AAAA", "CAA", "CNAME", "MX", "TXT"),
    text_types=frozenset({"TXT"}),
    host_types=frozenset({"CNAME"}),
    min_ttl=600,
    max_ttl=86_400,
    warn_relative_host=False,
)
