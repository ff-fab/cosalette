"""Unit tests for cosalette._cron — Quartz-compatible cron parser.

Test Techniques Used:
- Equivalence Partitioning: field token types
  (wildcard, single, range, step, list, named)
- Boundary Value Analysis: range edges, month lengths, leap years
- Decision Table: DOM/DOW special resolution logic
- Error Guessing: invalid expressions, non-existent Nth weekday, step ≤ 0
"""

from __future__ import annotations

import datetime

import pytest

from cosalette._cron import (
    _DOW_NAMES,
    _MONTH_NAMES,
    CronSchedule,
    _LastDay,
    _LastDow,
    _LastWeekday,
    _NearestWeekday,
    _NthDow,
    _parse_dom_field,
    _parse_dow_field,
    _parse_simple_field,
    _quartz_to_python_dow,
    _resolve_dom_special,
    _resolve_dow_special,
)

pytestmark = pytest.mark.unit

# Reference datetimes — deterministic, no mocking needed
REF = datetime.datetime(2024, 3, 15, 10, 30, 0)  # Friday 2024-03-15 10:30:00


# ---------------------------------------------------------------------------
# _parse_simple_field
# ---------------------------------------------------------------------------


class TestParseSimpleField:
    """Equivalence Partitioning across token types."""

    @pytest.mark.parametrize(
        ("token", "lo", "hi", "expected"),
        [
            ("*", 0, 59, set(range(0, 60))),
            ("?", 0, 59, set(range(0, 60))),
            ("5", 0, 59, {5}),
            ("0", 0, 59, {0}),
            ("59", 0, 59, {59}),
            ("1-5", 1, 31, {1, 2, 3, 4, 5}),
            ("*/15", 0, 59, {0, 15, 30, 45}),
            ("10-20/5", 0, 59, {10, 15, 20}),
            ("1,5,10", 0, 59, {1, 5, 10}),
            ("0,30", 0, 59, {0, 30}),
            ("*/10", 0, 23, {0, 10, 20}),
        ],
        ids=[
            "star-full-range",
            "question-full-range",
            "single-mid",
            "single-lo",
            "single-hi",
            "range",
            "star-step",
            "range-step",
            "list",
            "list-with-zero",
            "star-step-hours",
        ],
    )
    def test_parse_simple_field_valid(
        self, token: str, lo: int, hi: int, expected: set[int]
    ) -> None:
        assert _parse_simple_field(token, lo, hi) == expected

    def test_parse_simple_field_named_dow(self) -> None:
        """Named day-of-week tokens resolve to Quartz numeric values."""
        result = _parse_simple_field("MON,WED,FRI", 1, 7, _DOW_NAMES)
        assert result == {2, 4, 6}

    def test_parse_simple_field_named_months(self) -> None:
        result = _parse_simple_field("JAN,JUN", 1, 12, _MONTH_NAMES)
        assert result == {1, 6}

    def test_parse_simple_field_named_range(self) -> None:
        result = _parse_simple_field("MON-FRI", 1, 7, _DOW_NAMES)
        assert result == {2, 3, 4, 5, 6}

    def test_parse_simple_field_step_zero_raises(self) -> None:
        """Error Guessing: step must be positive."""
        with pytest.raises(ValueError, match="Step must be positive"):
            _parse_simple_field("*/0", 0, 59)

    def test_parse_simple_field_invalid_range_raises(self) -> None:
        """Error Guessing: a > b in range."""
        with pytest.raises(ValueError, match="Invalid range"):
            _parse_simple_field("10-5", 0, 59)


# ---------------------------------------------------------------------------
# _parse_dom_field
# ---------------------------------------------------------------------------


class TestParseDomField:
    """Decision Table: DOM token → descriptor or set."""

    def test_parse_dom_l(self) -> None:
        assert _parse_dom_field("L") == _LastDay(offset=0)

    def test_parse_dom_l_offset(self) -> None:
        assert _parse_dom_field("L-3") == _LastDay(offset=3)

    def test_parse_dom_lw(self) -> None:
        assert _parse_dom_field("LW") == _LastWeekday()

    def test_parse_dom_nearest_weekday(self) -> None:
        assert _parse_dom_field("15W") == _NearestWeekday(day=15)

    def test_parse_dom_normal(self) -> None:
        assert _parse_dom_field("1,15") == {1, 15}

    def test_parse_dom_case_insensitive(self) -> None:
        assert _parse_dom_field("lw") == _LastWeekday()


# ---------------------------------------------------------------------------
# _parse_dow_field
# ---------------------------------------------------------------------------


class TestParseDowField:
    """Decision Table: DOW token → descriptor or set."""

    def test_parse_dow_hash_numeric(self) -> None:
        assert _parse_dow_field("6#3") == _NthDow(dow=6, nth=3)

    def test_parse_dow_hash_named(self) -> None:
        assert _parse_dow_field("FRI#3") == _NthDow(dow=6, nth=3)

    def test_parse_dow_last_numeric(self) -> None:
        assert _parse_dow_field("6L") == _LastDow(dow=6)

    def test_parse_dow_last_named(self) -> None:
        assert _parse_dow_field("FRIL") == _LastDow(dow=6)

    def test_parse_dow_l_alone(self) -> None:
        """L alone → Saturday ({7} in Quartz)."""
        assert _parse_dow_field("L") == {7}

    def test_parse_dow_named_range(self) -> None:
        assert _parse_dow_field("MON-FRI") == {2, 3, 4, 5, 6}

    def test_parse_dow_star(self) -> None:
        assert _parse_dow_field("?") == set(range(1, 8))


# ---------------------------------------------------------------------------
# _quartz_to_python_dow
# ---------------------------------------------------------------------------


class TestQuartzToPythonDow:
    """Specification-based: mapping table verification."""

    @pytest.mark.parametrize(
        ("quartz", "python_dow"),
        [
            (1, 6),  # SUN → 6
            (2, 0),  # MON → 0
            (3, 1),  # TUE → 1
            (4, 2),  # WED → 2
            (5, 3),  # THU → 3
            (6, 4),  # FRI → 4
            (7, 5),  # SAT → 5
        ],
        ids=["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"],
    )
    def test_quartz_to_python_dow(self, quartz: int, python_dow: int) -> None:
        assert _quartz_to_python_dow(quartz) == python_dow


# ---------------------------------------------------------------------------
# _resolve_dom_special
# ---------------------------------------------------------------------------


class TestResolveDomSpecial:
    """Boundary Value Analysis: edge-of-month and weekday boundaries."""

    def test_last_day_march(self) -> None:
        assert _resolve_dom_special(_LastDay(0), 2024, 3) == {31}

    def test_last_day_feb_non_leap(self) -> None:
        assert _resolve_dom_special(_LastDay(0), 2023, 2) == {28}

    def test_last_day_feb_leap(self) -> None:
        assert _resolve_dom_special(_LastDay(0), 2024, 2) == {29}

    def test_last_day_with_offset(self) -> None:
        # March has 31 days → L-3 = 28
        assert _resolve_dom_special(_LastDay(3), 2024, 3) == {28}

    def test_last_weekday_march_2024(self) -> None:
        # March 31, 2024 is Sunday → last weekday is Friday March 29
        assert _resolve_dom_special(_LastWeekday(), 2024, 3) == {29}

    def test_last_weekday_when_last_is_saturday(self) -> None:
        # Feb 2025: last day is Feb 28 (Friday) — already a weekday
        # Actually Feb 28, 2025 is a Friday, so result is 28
        assert _resolve_dom_special(_LastWeekday(), 2025, 2) == {28}

    def test_nearest_weekday_saturday(self) -> None:
        # March 2, 2024 is Saturday → nearest weekday is Monday March 4?
        # Wait, day=2, and March 2 2024 is Saturday.
        # Since target > 1, prefer Friday → March 1
        assert _resolve_dom_special(_NearestWeekday(day=2), 2024, 3) == {1}

    def test_nearest_weekday_sunday(self) -> None:
        # March 3, 2024 is Sunday → prefer Monday → March 4
        assert _resolve_dom_special(_NearestWeekday(day=3), 2024, 3) == {4}

    def test_nearest_weekday_first_is_saturday(self) -> None:
        # June 1, 2024 is Saturday → target=1, target not > 1 → Monday the 3rd
        assert _resolve_dom_special(_NearestWeekday(day=1), 2024, 6) == {3}

    def test_nearest_weekday_already_weekday(self) -> None:
        # March 15, 2024 is Friday → stays 15
        assert _resolve_dom_special(_NearestWeekday(day=15), 2024, 3) == {15}


# ---------------------------------------------------------------------------
# _resolve_dow_special
# ---------------------------------------------------------------------------


class TestResolveDowSpecial:
    """Decision Table: DOW specials resolved against specific months."""

    def test_last_friday_march_2024(self) -> None:
        # March 2024: last Friday is March 29
        assert _resolve_dow_special(_LastDow(dow=6), 2024, 3) == {29}

    def test_nth_friday_third_march_2024(self) -> None:
        # March 2024: Fridays are 1, 8, 15, 22, 29 → 3rd = 15
        assert _resolve_dow_special(_NthDow(dow=6, nth=3), 2024, 3) == {15}

    def test_nth_dow_nonexistent(self) -> None:
        """Error Guessing: 5th Monday may not exist."""
        # March 2024: Mondays are 4, 11, 18, 25 → only 4 Mondays, 5th doesn't exist
        assert _resolve_dow_special(_NthDow(dow=2, nth=5), 2024, 3) == set()

    def test_last_sunday_march_2024(self) -> None:
        # March 2024: last Sunday is March 31
        assert _resolve_dow_special(_LastDow(dow=1), 2024, 3) == {31}


# ---------------------------------------------------------------------------
# CronSchedule.__init__ — parsing
# ---------------------------------------------------------------------------


class TestCronScheduleInit:
    """Specification-based: valid/invalid expression parsing."""

    def test_six_fields(self) -> None:
        sched = CronSchedule("0 0 12 * * ?")
        assert sched.expression == "0 0 12 * * ?"

    def test_seven_fields_with_year(self) -> None:
        sched = CronSchedule("0 0 12 * * ? 2025")
        assert sched.expression == "0 0 12 * * ? 2025"

    def test_five_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="6 or 7 fields"):
            CronSchedule("0 0 12 * *")

    def test_eight_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="6 or 7 fields"):
            CronSchedule("0 0 12 * * ? 2025 extra")

    def test_empty_expression_raises(self) -> None:
        with pytest.raises(ValueError, match="6 or 7 fields"):
            CronSchedule("")


# ---------------------------------------------------------------------------
# CronSchedule — equality, hash, repr
# ---------------------------------------------------------------------------


class TestCronScheduleIdentity:
    """Specification-based: equality, hash, repr contracts."""

    def test_equal_expressions(self) -> None:
        a = CronSchedule("0 0 12 * * ?")
        b = CronSchedule("0 0 12 * * ?")
        assert a == b

    def test_different_expressions(self) -> None:
        a = CronSchedule("0 0 12 * * ?")
        b = CronSchedule("0 0 13 * * ?")
        assert a != b

    def test_hash_equal(self) -> None:
        a = CronSchedule("0 0 12 * * ?")
        b = CronSchedule("0 0 12 * * ?")
        assert hash(a) == hash(b)

    def test_repr(self) -> None:
        sched = CronSchedule("0 0 12 * * ?")
        assert repr(sched) == "CronSchedule('0 0 12 * * ?')"

    def test_not_equal_to_other_type(self) -> None:
        assert CronSchedule("0 0 12 * * ?") != "0 0 12 * * ?"


# ---------------------------------------------------------------------------
# CronSchedule.next_fire_after
# ---------------------------------------------------------------------------


class TestCronScheduleNextFire:
    """Decision Table + Boundary Value Analysis: scheduling logic.

    All tests use deterministic reference datetime REF = 2024-03-15 10:30:00 (Friday).
    """

    def test_every_5_minutes(self) -> None:
        sched = CronSchedule("0 */5 * * * ?")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 15, 10, 35, 0)

    def test_specific_time_same_day(self) -> None:
        sched = CronSchedule("0 0 12 * * ?")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 15, 12, 0, 0)

    def test_specific_time_next_day(self) -> None:
        """When time has passed today, next occurrence is tomorrow."""
        sched = CronSchedule("0 0 9 * * ?")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 16, 9, 0, 0)

    def test_weekday_schedule_friday_past(self) -> None:
        """Friday 10:30, MON-FRI at 10:00 → next Monday 10:00."""
        sched = CronSchedule("0 0 10 ? * MON-FRI")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 18, 10, 0, 0)

    def test_weekday_schedule_friday_future(self) -> None:
        """Friday 10:30, MON-FRI at 14:00 → same Friday 14:00."""
        sched = CronSchedule("0 0 14 ? * MON-FRI")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 15, 14, 0, 0)

    def test_last_day_of_month(self) -> None:
        sched = CronSchedule("0 0 12 L * ?")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 31, 12, 0, 0)

    def test_last_weekday_of_month(self) -> None:
        """March 2024: last weekday is Friday March 29."""
        sched = CronSchedule("0 0 12 LW * ?")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 29, 12, 0, 0)

    def test_nth_weekday_same_month(self) -> None:
        """3rd Friday of March 2024 is the 15th — REF is 10:30 on the 15th."""
        sched = CronSchedule("0 0 12 ? * FRI#3")

        result = sched.next_fire_after(REF)

        # 15th at 12:00 is still ahead of 10:30
        assert result == datetime.datetime(2024, 3, 15, 12, 0, 0)

    def test_nth_weekday_next_month(self) -> None:
        """After 3rd Friday at 12:00, next is April's 3rd Friday."""
        after = datetime.datetime(2024, 3, 15, 12, 0, 0)
        sched = CronSchedule("0 0 12 ? * FRI#3")

        result = sched.next_fire_after(after)

        # April 2024: Fridays are 5, 12, 19, 26 → 3rd = 19
        assert result == datetime.datetime(2024, 4, 19, 12, 0, 0)

    def test_year_field_skips_to_matching_year(self) -> None:
        sched = CronSchedule("0 0 12 * * ? 2025")

        result = sched.next_fire_after(REF)

        assert result.year == 2025
        assert result == datetime.datetime(2025, 1, 1, 12, 0, 0)

    def test_no_match_raises_value_error(self) -> None:
        """Year in the past → no future match within scan window."""
        sched = CronSchedule("0 0 12 * * ? 2020")

        with pytest.raises(ValueError, match="No matching fire time"):
            sched.next_fire_after(REF)

    def test_specific_second(self) -> None:
        sched = CronSchedule("30 0 12 * * ?")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 15, 12, 0, 30)

    def test_every_minute(self) -> None:
        sched = CronSchedule("0 * * * * ?")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 3, 15, 10, 31, 0)

    def test_specific_month(self) -> None:
        """Expression for June only — skips ahead from March."""
        sched = CronSchedule("0 0 12 * JUN ?")

        result = sched.next_fire_after(REF)

        assert result == datetime.datetime(2024, 6, 1, 12, 0, 0)

    def test_preserves_timezone(self) -> None:
        tz = datetime.UTC
        after = REF.replace(tzinfo=tz)
        sched = CronSchedule("0 0 12 * * ?")

        result = sched.next_fire_after(after)

        assert result.tzinfo is tz

    def test_strictly_after(self) -> None:
        """Result is strictly after the reference, even if ref matches."""
        ref = datetime.datetime(2024, 3, 15, 12, 0, 0)
        sched = CronSchedule("0 0 12 * * ?")

        result = sched.next_fire_after(ref)

        assert result > ref
        assert result == datetime.datetime(2024, 3, 16, 12, 0, 0)

    def test_last_day_february_leap(self) -> None:
        """L in February of a leap year → 29th."""
        ref = datetime.datetime(2024, 2, 1, 0, 0, 0)
        sched = CronSchedule("0 0 12 L * ?")

        result = sched.next_fire_after(ref)

        assert result == datetime.datetime(2024, 2, 29, 12, 0, 0)
