"""Quartz-compatible cron expression parser.

Supports 6- or 7-field Quartz cron expressions::

    second minute hour day-of-month month day-of-week [year]

Features: ``*``, ``?``, ranges (``N-M``), steps (``N/S``, ``*/S``),
lists (``N,M``), named days (``SUN``–``SAT``) and months
(``JAN``–``DEC``), last day (``L``), nearest weekday (``W``),
Nth weekday of month (``#``).

See Also:
    ADR-032 — Cron scheduling and wall-clock sleep.
    http://www.quartz-scheduler.org/documentation/quartz-2.3.0/tutorials/tutorial-lesson-06.html
"""

from __future__ import annotations

import calendar
import datetime
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Named lookups
# ---------------------------------------------------------------------------

_MONTH_NAMES: dict[str, int] = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

# Quartz: 1=SUN … 7=SAT
_DOW_NAMES: dict[str, int] = {
    "SUN": 1,
    "MON": 2,
    "TUE": 3,
    "WED": 4,
    "THU": 5,
    "FRI": 6,
    "SAT": 7,
}

# ---------------------------------------------------------------------------
# Field parsing helpers
# ---------------------------------------------------------------------------

_HASH_RE = re.compile(r"^(\d+|[A-Z]{3})#(\d+)$", re.IGNORECASE)
_L_DOW_RE = re.compile(r"^(\d+|[A-Z]{3})L$", re.IGNORECASE)
_L_OFFSET_RE = re.compile(r"^L-(\d+)$", re.IGNORECASE)
_W_RE = re.compile(r"^(\d+)W$", re.IGNORECASE)


def _replace_names(token: str, names: dict[str, int]) -> str:
    """Replace named tokens (JAN, MON, …) with their numeric equivalents."""
    upper = token.upper()
    for name, val in names.items():
        upper = upper.replace(name, str(val))
    return upper


def _validate_bounds(value: int, lo: int, hi: int, token: str) -> None:
    """Raise if *value* is outside ``[lo, hi]``."""
    if value < lo or value > hi:
        msg = f"Value {value} out of range [{lo}-{hi}] in {token!r}"
        raise ValueError(msg)


def _parse_step_field(token: str, lo: int, hi: int) -> set[int]:
    """Parse a step expression like ``N/S``, ``*/S``, or ``N-M/S``."""
    base_str, step_str = token.split("/", 1)
    step = int(step_str)
    if step <= 0:
        msg = f"Step must be positive, got {step}"
        raise ValueError(msg)
    if base_str == "*":
        start = lo
    elif "-" in base_str:
        rng = base_str.split("-", 1)
        start = int(rng[0])
        end = int(rng[1])
        _validate_bounds(start, lo, hi, token)
        _validate_bounds(end, lo, hi, token)
        hi = end  # override upper bound for range/step
    else:
        start = int(base_str)
        _validate_bounds(start, lo, hi, token)
    return set(range(start, hi + 1, step))


def _parse_simple_field(
    token: str,
    lo: int,
    hi: int,
    names: dict[str, int] | None = None,
) -> set[int]:
    """Parse a simple cron field token into a set of matching values.

    Handles: ``*``, ``?``, ``N``, ``N-M``, ``N/S``, ``*/S``, ``N-M/S``,
    ``N,M,…``, and named values.
    """
    if names:
        token = _replace_names(token, names)

    if token in ("*", "?"):
        return set(range(lo, hi + 1))

    # list (must come before range/step to split correctly)
    if "," in token:
        result: set[int] = set()
        for part in token.split(","):
            result |= _parse_simple_field(part, lo, hi)
        return result

    if "/" in token:
        return _parse_step_field(token, lo, hi)

    # range
    if "-" in token:
        parts = token.split("-", 1)
        a, b = int(parts[0]), int(parts[1])
        if a > b:
            msg = f"Invalid range {a}-{b}"
            raise ValueError(msg)
        _validate_bounds(a, lo, hi, token)
        _validate_bounds(b, lo, hi, token)
        return set(range(a, b + 1))

    # single value
    val = int(token)
    _validate_bounds(val, lo, hi, token)
    return {val}


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
# Field-level parser — returns set[int] or a special descriptor
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
# Day-of-month special resolution
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


# ---------------------------------------------------------------------------
# CronSchedule
# ---------------------------------------------------------------------------

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
        assert py_dows is not None  # set when _dow is set[int]
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

    def __repr__(self) -> str:
        return f"CronSchedule({self._expression!r})"

    def __eq__(self, other: object) -> bool:
        """Check equality based on the raw expression string.

        Note: Semantically equivalent expressions with different syntax
        (e.g. ``"0 0 12 * * MON"`` vs ``"0 0 12 * * 2"``) compare
        unequal because comparison uses the unparsed expression text.
        """
        if not isinstance(other, CronSchedule):
            return NotImplemented
        return self._expression == other._expression

    def __hash__(self) -> int:
        return hash(self._expression)
