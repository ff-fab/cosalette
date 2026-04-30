"""Special day descriptors and DOM/DOW field parsers for Quartz cron."""

from __future__ import annotations

import calendar
import datetime
from dataclasses import dataclass

from cosalette._cron._fields import (
    _DOW_NAMES,
    _HASH_RE,
    _L_DOW_RE,
    _L_OFFSET_RE,
    _W_RE,
    _parse_simple_field,
)

# ---------------------------------------------------------------------------
# Special field descriptors for day-of-month and day-of-week
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LastDay:
    """``L`` in day-of-month: last day, optionally offset (``L-N``)."""

    offset: int = 0

    def resolve(self, year: int, month: int) -> set[int]:
        """Return the concrete day(s) for this spec."""
        last = _last_day_of_month(year, month)
        day = last - self.offset
        return {day} if 1 <= day <= last else set()


@dataclass(frozen=True, slots=True)
class _NearestWeekday:
    """``W`` in day-of-month (e.g. ``15W``): nearest weekday to day N."""

    day: int

    def resolve(self, year: int, month: int) -> set[int]:
        """Return the concrete day(s) for this spec."""
        last = _last_day_of_month(year, month)
        target = min(self.day, last)
        d = datetime.date(year, month, target)
        wd = d.weekday()
        if wd == 5:  # Saturday
            return {target - 1} if target > 1 else {target + 2}
        if wd == 6:  # Sunday
            return {target + 1} if target < last else {target - 2}
        return {target}


@dataclass(frozen=True, slots=True)
class _LastWeekday:
    """``LW`` in day-of-month: last weekday of the month."""

    def resolve(self, year: int, month: int) -> set[int]:
        """Return the concrete day(s) for this spec."""
        last = _last_day_of_month(year, month)
        d = datetime.date(year, month, last)
        wd = d.weekday()  # 0=Mon … 6=Sun
        if wd == 5:  # Saturday → Friday
            d -= datetime.timedelta(days=1)
        elif wd == 6:  # Sunday → Friday
            d -= datetime.timedelta(days=2)
        return {d.day}


@dataclass(frozen=True, slots=True)
class _LastDow:
    """``NL`` in day-of-week: last occurrence of weekday N in month."""

    dow: int  # 1-7, Quartz encoding


@dataclass(frozen=True, slots=True)
class _NthDow:
    """``N#M`` in day-of-week: Mth occurrence of weekday N in month."""

    dow: int  # 1-7, Quartz encoding
    nth: int


# Type alias for day-special descriptors
type _DomSpecial = _LastDay | _NearestWeekday | _LastWeekday
type _DowSpecial = _LastDow | _NthDow


# ---------------------------------------------------------------------------
# Field-level parsers — return set[int] or a special descriptor
# ---------------------------------------------------------------------------


def _parse_dom_field(
    token: str,
) -> set[int] | _DomSpecial:
    """Parse the day-of-month field, handling ``L``, ``W``, ``LW``."""
    upper = token.upper().strip()

    if upper == "LW":
        return _LastWeekday()

    if upper == "L":
        return _LastDay()

    m = _L_OFFSET_RE.match(upper)
    if m:
        return _LastDay(offset=int(m.group(1)))

    m = _W_RE.match(upper)
    if m:
        return _NearestWeekday(day=int(m.group(1)))

    return _parse_simple_field(token, 1, 31)


def _parse_dow_field(
    token: str,
) -> set[int] | _DowSpecial:
    """Parse the day-of-week field, handling ``L``, ``#``, named days."""
    upper = token.upper().strip()

    # N#M — Nth weekday of month
    m = _HASH_RE.match(upper)
    if m:
        dow_raw = m.group(1)
        dow = _DOW_NAMES.get(dow_raw.upper())
        if dow is None:
            dow = int(dow_raw)
        nth = int(m.group(2))
        return _NthDow(dow=dow, nth=nth)

    # NL — last weekday of month
    m = _L_DOW_RE.match(upper)
    if m:
        dow_raw = m.group(1)
        dow = _DOW_NAMES.get(dow_raw.upper())
        if dow is None:
            dow = int(dow_raw)
        return _LastDow(dow=dow)

    if upper == "L":
        return {7}  # L alone = SAT

    return _parse_simple_field(token, 1, 7, _DOW_NAMES)


# ---------------------------------------------------------------------------
# Quartz DOW → Python DOW conversion
# ---------------------------------------------------------------------------


def _quartz_to_python_dow(q: int) -> int:
    """Convert Quartz day-of-week (1=SUN … 7=SAT) to Python (0=MON … 6=SUN)."""
    # Quartz: 1=SUN, 2=MON, 3=TUE, 4=WED, 5=THU, 6=FRI, 7=SAT
    # Python:         0=MON, 1=TUE, 2=WED, 3=THU, 4=FRI, 5=SAT, 6=SUN
    return (q - 2) % 7


# ---------------------------------------------------------------------------
# Day-of-month helpers
# ---------------------------------------------------------------------------


def _last_day_of_month(year: int, month: int) -> int:
    """Return the last day of the given month."""
    return calendar.monthrange(year, month)[1]


def _resolve_dom_special(
    spec: _DomSpecial,
    year: int,
    month: int,
) -> set[int]:
    """Resolve a special day-of-month descriptor to concrete day(s)."""
    return spec.resolve(year, month)


def _resolve_dow_special(
    spec: _DowSpecial,
    year: int,
    month: int,
) -> set[int]:
    """Resolve a special day-of-week descriptor to concrete day(s)."""
    py_dow = _quartz_to_python_dow(spec.dow)
    last = _last_day_of_month(year, month)

    if isinstance(spec, _LastDow):
        # Walk backwards from the last day of the month
        d = datetime.date(year, month, last)
        while d.weekday() != py_dow:
            d -= datetime.timedelta(days=1)
        return {d.day}

    if isinstance(spec, _NthDow):
        # Find the Nth occurrence of the weekday
        count = 0
        for day in range(1, last + 1):
            if datetime.date(year, month, day).weekday() == py_dow:
                count += 1
                if count == spec.nth:
                    return {day}
        return set()  # Nth occurrence doesn't exist this month

    msg = f"Unknown day-of-week special: {spec!r}"  # pragma: no cover
    raise TypeError(msg)  # pragma: no cover
