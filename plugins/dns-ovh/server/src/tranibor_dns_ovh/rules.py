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


#: `domain.zone.RecordTypeEnum` of https://eu.api.ovh.com/1.0/domain.json.
RULES = Rules(
    provider="OVHcloud",
    types=(
        "A",
        "AAAA",
        "CAA",
        "CNAME",
        "DKIM",
        "DMARC",
        "DNAME",
        "HTTPS",
        "LOC",
        "MX",
        "NAPTR",
        "NS",
        "PTR",
        "RP",
        "SPF",
        "SRV",
        "SSHFP",
        "SVCB",
        "TLSA",
        "TXT",
    ),
    text_types=frozenset({"TXT", "SPF", "DKIM", "DMARC"}),
    host_types=frozenset({"CNAME", "NS", "DNAME", "PTR"}),
    min_ttl=1,
    max_ttl=604_800,  # a sanity bound of this server (7 days), not an OVHcloud rule
    warn_relative_host=True,
)
