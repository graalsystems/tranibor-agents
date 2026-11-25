# SPDX-License-Identifier: Apache-2.0
"""Text and time helpers: what another system wrote is clipped and stripped of control characters before it is
carried, and times are UTC to the second."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

MAX_TEXT = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clip(value: object, limit: int = MAX_TEXT) -> str:
    """Text from another system, made safe to carry: control characters out, length bounded."""
    text = _CONTROL.sub(" ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_time(value: object) -> datetime | None:
    """An RFC 3339 time or a naive UTC one, as an aware UTC datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def days_until(moment: datetime | None, now: datetime) -> int | None:
    """Whole days from `now` to `moment`, negative once past (floor: 0 means less than a day left)."""
    return None if moment is None else math.floor((moment - now).total_seconds() / 86400)
