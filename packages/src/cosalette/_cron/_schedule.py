"""Parsed Quartz-compatible CronSchedule."""

from __future__ import annotations

import datetime
from typing import override

from cosalette._cron._fields import _MONTH_NAMES, _parse_simple_field
from cosalette._cron._special import (
    _DomSpecial,
    _DowSpecial,
    _last_day_of_month,
    _LastDay,
    _LastDow,
    _LastWeekday,
    _NearestWeekday,
    _NthDow,
    _parse_dom_field,
    _parse_dow_field,
    _quartz_to_python_dow,
    _resolve_dom_special,
    _resolve_dow_special,
)

# Year range — Quartz supports 1970-2099
_MIN_YEAR = 1970
_MAX_YEAR = 2099

# Safety limit for next_fire_after iteration
_MAX_SCAN_YEARS = 4


class CronSchedule:
    """Parsed Quartz-compatible cron expression.

    Supports 6-field (second through day-of-week) and 7-field (with year)
    expressions.

    Example::

        sched = CronSchedule("0 30 10-13 ? * WED,FRI")
        next_dt = sched.next_fire_after(datetime.datetime.now())
    """

    __slots__ = (
        "_expression",
        "_seconds",
        "_minutes",
        "_hours",
        "_dom",
        "_dom_is_unspecified",
        "_months",
        "_dow",
        "_dow_is_unspecified",
        "_py_dows",
        "_years",
    )

    def __init__(self, expression: str) -> None:
        parts = expression.strip().split()
        if len(parts) not in (6, 7):
            msg = (
                f"Cron expression must have 6 or 7 fields, "
                f"got {len(parts)}: {expression!r}"
            )
            raise ValueError(msg)

        self._expression = expression
        self._seconds: set[int] = _parse_simple_field(parts[0], 0, 59)
        self._minutes: set[int] = _parse_simple_field(parts[1], 0, 59)
        self._hours: set[int] = _parse_simple_field(parts[2], 0, 23)
        self._dom: set[int] | _DomSpecial = _parse_dom_field(parts[3])
        self._dom_is_unspecified: bool = parts[3].strip() == "?"
        self._months: set[int] = _parse_simple_field(parts[4], 1, 12, _MONTH_NAMES)
        self._dow: set[int] | _DowSpecial = _parse_dow_field(parts[5])
        self._dow_is_unspecified: bool = parts[5].strip() == "?"
        self._py_dows: frozenset[int] | None = (
            frozenset(_quartz_to_python_dow(q) for q in self._dow)
            if isinstance(self._dow, set)
            else None
        )
        self._years: set[int] | None = (
            _parse_simple_field(parts[6], _MIN_YEAR, _MAX_YEAR)
            if len(parts) == 7
            else None
        )

        self._validate()

    def _validate(self) -> None:
        """Validate parsed field ranges."""
        checks: list[tuple[bool, str]] = [
            (not self._seconds, "Seconds field produced no matching values"),
            (not self._minutes, "Minutes field produced no matching values"),
            (not self._hours, "Hours field produced no matching values"),
            (
                isinstance(self._dom, set) and not self._dom,
                "Day-of-month field produced no matching values",
            ),
            (not self._months, "Month field produced no matching values"),
            (
                isinstance(self._dow, set) and not self._dow,
                "Day-of-week field produced no matching values",
            ),
            (
                self._years is not None and not self._years,
                "Year field produced no matching values",
            ),
        ]
        for failed, msg in checks:
            if failed:
                raise ValueError(msg)

    @property
    def expression(self) -> str:
        """The original cron expression string."""
        return self._expression

    def _resolve_dom_days(
        self,
        year: int,
        month: int,
        last: int,
    ) -> set[int]:
        """Return matching days for the DOM field."""
        if isinstance(self._dom, (_LastDay, _NearestWeekday, _LastWeekday)):
            return _resolve_dom_special(self._dom, year, month)
        return {d for d in self._dom if 1 <= d <= last}

    def _resolve_dow_days(
        self,
        year: int,
        month: int,
        last: int,
    ) -> set[int]:
        """Return matching days for the DOW field."""
        if isinstance(self._dow, (_LastDow, _NthDow)):
            return _resolve_dow_special(self._dow, year, month)
        # _py_dows is precomputed at init for plain set[int] DOW values
        py_dows = self._py_dows
        assert py_dows is not None  # set when _dow is set[int]  # noqa: S101
        return {
            d
            for d in range(1, last + 1)
            if datetime.date(year, month, d).weekday() in py_dows
        }

    def _matching_days(self, year: int, month: int) -> set[int]:
        """Return day-of-month values that match both DOM and DOW constraints.

        Quartz semantics: ``?`` means "unspecified" (defer to the other
        field).  ``*`` means "every value" but is still a concrete
        constraint.  When **both** DOM and DOW are specified (neither is
        ``?``), the match is the **union** (either condition matches).
        When one is ``?``, only the other is used.
        """
        last = _last_day_of_month(year, month)

        if self._dom_is_unspecified and self._dow_is_unspecified:
            # Both ? — match all days
            return set(range(1, last + 1))
        if self._dom_is_unspecified:
            # DOM is ?, only DOW matters
            return self._resolve_dow_days(year, month, last)
        if self._dow_is_unspecified:
            # DOW is ?, only DOM matters
            return self._resolve_dom_days(year, month, last)
        # Both specified — union (Quartz semantics)
        return self._resolve_dom_days(year, month, last) | self._resolve_dow_days(
            year, month, last
        )

    def next_fire_after(
        self,
        after: datetime.datetime,
    ) -> datetime.datetime:
        """Return the next datetime strictly after *after* that matches.

        The returned datetime preserves the timezone of *after*.

        Args:
            after: Reference datetime (exclusive — result is strictly after).

        Returns:
            Next matching datetime.

        Raises:
            ValueError: If no match is found within the scan limit
                (4 years).
        """
        tz = after.tzinfo

        # Start scanning from the next second
        dt = after.replace(microsecond=0) + datetime.timedelta(seconds=1)

        limit = after.year + _MAX_SCAN_YEARS
        checks = [
            self._advance_month,
            self._advance_day,
            self._advance_hour,
            self._advance_minute,
            self._advance_second,
        ]

        while dt.year <= limit:
            # Year check (handled separately — can terminate the scan)
            if self._years is not None and dt.year not in self._years:
                future = sorted(y for y in self._years if y > dt.year)
                if not future:
                    break
                dt = datetime.datetime(future[0], 1, 1, tzinfo=tz)
                continue

            for check in checks:
                result = check(dt, tz)
                if result is not None:
                    dt = result
                    break
            else:
                # All fields matched
                return dt

        msg = (
            f"No matching fire time found within {_MAX_SCAN_YEARS} years "
            f"for expression {self._expression!r}"
        )
        raise ValueError(msg)

    # -- field-advance helpers (return new dt if not matched, None if ok) --

    def _advance_month(
        self,
        dt: datetime.datetime,
        tz: datetime.tzinfo | None,
    ) -> datetime.datetime | None:
        if dt.month in self._months:
            return None
        nxt = sorted(m for m in self._months if m > dt.month)
        if nxt:
            return datetime.datetime(dt.year, nxt[0], 1, tzinfo=tz)
        return datetime.datetime(dt.year + 1, 1, 1, tzinfo=tz)

    def _advance_day(
        self,
        dt: datetime.datetime,
        tz: datetime.tzinfo | None,
    ) -> datetime.datetime | None:
        matching = self._matching_days(dt.year, dt.month)
        if dt.day in matching:
            return None
        nxt = sorted(d for d in matching if d > dt.day)
        if nxt:
            return datetime.datetime(dt.year, dt.month, nxt[0], tzinfo=tz)
        if dt.month == 12:
            return datetime.datetime(dt.year + 1, 1, 1, tzinfo=tz)
        return datetime.datetime(dt.year, dt.month + 1, 1, tzinfo=tz)

    def _advance_hour(
        self,
        dt: datetime.datetime,
        _tz: datetime.tzinfo | None,
    ) -> datetime.datetime | None:
        if dt.hour in self._hours:
            return None
        nxt = sorted(h for h in self._hours if h > dt.hour)
        if nxt:
            return dt.replace(hour=nxt[0], minute=0, second=0)
        return (dt + datetime.timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
        )

    def _advance_minute(
        self,
        dt: datetime.datetime,
        _tz: datetime.tzinfo | None,
    ) -> datetime.datetime | None:
        if dt.minute in self._minutes:
            return None
        nxt = sorted(m for m in self._minutes if m > dt.minute)
        if nxt:
            return dt.replace(minute=nxt[0], second=0)
        return (dt + datetime.timedelta(hours=1)).replace(
            minute=0,
            second=0,
        )

    def _advance_second(
        self,
        dt: datetime.datetime,
        _tz: datetime.tzinfo | None,
    ) -> datetime.datetime | None:
        if dt.second in self._seconds:
            return None
        nxt = sorted(s for s in self._seconds if s > dt.second)
        if nxt:
            return dt.replace(second=nxt[0])
        return (dt + datetime.timedelta(minutes=1)).replace(second=0)

    @override
    def __repr__(self) -> str:
        return f"CronSchedule({self._expression!r})"

    @override
    def __eq__(self, other: object) -> bool:
        """Check equality based on the raw expression string.

        Note: Semantically equivalent expressions with different syntax
        (e.g. ``"0 0 12 * * MON"`` vs ``"0 0 12 * * 2"``) compare
        unequal because comparison uses the unparsed expression text.
        """
        if not isinstance(other, CronSchedule):
            return NotImplemented
        return self._expression == other._expression

    @override
    def __hash__(self) -> int:
        return hash(self._expression)
