# SPDX-License-Identifier: Apache-2.0
"""Next fire times of a Velero schedule's cron expression.

Velero parses `spec.schedule` with the robfig/cron standard parser: five fields (minute hour day-of-month
month day-of-week), `*`, lists, ranges, steps, month and weekday names, and the descriptors `@hourly`,
`@daily`, `@weekly`, `@monthly`, `@yearly` and `@every <duration>`. Velero schedules run in UTC unless the
server's time zone says otherwise; this module computes in UTC. Anything it does not understand yields
None, never a guess. (Velero's own docs, https://velero.io/docs/main/api-types/schedule/, give the format
only as "a cron expression"; the descriptor list is robfig/cron's, not confirmed in Velero's docs.)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_DESCRIPTORS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
_MONTHS = {
    m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])
}
_DAYS = {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
_EVERY = re.compile(r"^@every\s+((?:\d+(?:\.\d+)?(?:h|m|s)){1,3})$")
_PART = re.compile(r"(\d+(?:\.\d+)?)(h|m|s)")


def _value(token: str, names: dict[str, int]) -> int:
    low = token.lower()
    return names[low[:3]] if low[:3] in names and not low.isdigit() else int(token)


def _field(text: str, low: int, high: int, names: dict[str, int] | None = None) -> set[int] | None:
    out: set[int] = set()
    for part in text.split(","):
        rng, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if step < 1:
            raise ValueError(part)
        if rng in ("*", "?"):
            start, end = low, high
        elif "-" in rng:
            a, b = rng.split("-", 1)
            start, end = _value(a, names or {}), _value(b, names or {})
        else:
            start = _value(rng, names or {})
            end = high if step_text else start
        if start < low or end > high or start > end:
            raise ValueError(part)
        out.update(range(start, end + 1, step))
    if text in ("*", "?"):
        return None  # unrestricted: matters for the day-of-month / day-of-week rule
    return out


class Cron:
    """A parsed expression: `next_after(t)` gives the next fire time strictly after `t`."""

    def __init__(self, expression: str) -> None:
        self.every: timedelta | None = None
        text = expression.strip()
        if match := _EVERY.match(text):
            seconds = sum(float(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in _PART.findall(match.group(1)))
            if seconds < 60:
                raise ValueError("an interval under a minute")
            self.every = timedelta(seconds=seconds)
            return
        text = _DESCRIPTORS.get(text.lower(), text)
        fields = text.split()
        if len(fields) != 5:
            raise ValueError(f"not a five-field cron expression: {expression!r}")
        self.minutes = _field(fields[0], 0, 59) or set(range(60))
        self.hours = _field(fields[1], 0, 23) or set(range(24))
        self.dom = _field(fields[2], 1, 31)
        self.months = _field(fields[3], 1, 12, _MONTHS) or set(range(1, 13))
        dow = _field(fields[4], 0, 7, _DAYS)
        self.dow = {d % 7 for d in dow} if dow is not None else None

    def _day_matches(self, day: datetime) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = self.dom is None or day.day in self.dom
        dow_ok = self.dow is None or (day.isoweekday() % 7) in self.dow
        if self.dom is not None and self.dow is not None:  # both restricted: either one (cron's rule)
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def next_after(self, moment: datetime) -> datetime | None:
        if self.every is not None:
            return moment + self.every
        start = moment.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for offset in range(0, 5 * 366):
            day = (start + timedelta(days=offset)).replace(hour=0, minute=0)
            if not self._day_matches(day):
                continue
            for hour in sorted(self.hours):
                for minute in sorted(self.minutes):
                    candidate = day.replace(hour=hour, minute=minute)
                    if candidate >= start:
                        return candidate
        return None


def next_fire(expression: str, after: datetime) -> datetime | None:
    """The next fire time after `after`, or None when the expression is not understood."""
    try:
        return Cron(expression).next_after(after)
    except (ValueError, KeyError):
        return None


def period_hours(expression: str, after: datetime) -> float | None:
    """The gap between the next two fire times, in hours (the schedule's current period), or None."""
    try:
        cron = Cron(expression)
        first = cron.next_after(after)
        second = cron.next_after(first) if first else None
    except (ValueError, KeyError):
        return None
    if first is None or second is None:
        return None
    return (second - first).total_seconds() / 3600
