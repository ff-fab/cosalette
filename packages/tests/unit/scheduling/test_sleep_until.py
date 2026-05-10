"""Unit tests for _seconds_until() and DeviceContext.sleep_until().

Test Techniques Used:
- Equivalence Partitioning: single time vs sequence of times
- Boundary Value Analysis: target in the past wraps to next day
- Error Guessing: empty sequence raises ValueError
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, patch

import pytest

from cosalette._context import _seconds_until

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixed reference time: 2024-03-15 10:00:00
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime.datetime(2024, 3, 15, 10, 0, 0)  # noqa: DTZ001


def _patch_now():
    """Patch datetime.datetime.now inside cosalette._context._device_context."""
    mock = patch("cosalette._context._device_context.datetime")
    return mock


# ---------------------------------------------------------------------------
# _seconds_until — pure function tests
# ---------------------------------------------------------------------------


class TestSecondsUntil:
    """Pure-function tests for _seconds_until()."""

    def test_seconds_until_future_time_same_day(self) -> None:
        """Target 3 hours ahead → ~10800s.

        Technique: Equivalence Partitioning — future time today.
        """
        with _patch_now() as mock_dt:
            mock_dt.datetime.now.return_value = _FIXED_NOW
            mock_dt.timedelta = datetime.timedelta
            mock_dt.time = datetime.time
            result = _seconds_until(datetime.time(13, 0, 0))

        assert result == pytest.approx(10800.0)

    def test_seconds_until_past_time_wraps_to_next_day(self) -> None:
        """Target already passed today → wraps to tomorrow.

        Technique: Boundary Value Analysis — past target.
        """
        with _patch_now() as mock_dt:
            mock_dt.datetime.now.return_value = _FIXED_NOW
            mock_dt.timedelta = datetime.timedelta
            mock_dt.time = datetime.time
            # 09:00 is 1 hour ago → should wrap to next day (23h away)
            result = _seconds_until(datetime.time(9, 0, 0))

        assert result == pytest.approx(23 * 3600.0)

    def test_seconds_until_multiple_targets_picks_nearest(self) -> None:
        """Multiple targets → picks the nearest upcoming one.

        Technique: Equivalence Partitioning — sequence input.
        """
        with _patch_now() as mock_dt:
            mock_dt.datetime.now.return_value = _FIXED_NOW
            mock_dt.timedelta = datetime.timedelta
            mock_dt.time = datetime.time
            targets = [datetime.time(12, 0, 0), datetime.time(15, 0, 0)]
            result = _seconds_until(targets)

        # 12:00 is 2h away, 15:00 is 5h away → picks 2h
        assert result == pytest.approx(2 * 3600.0)

    def test_seconds_until_empty_sequence_raises(self) -> None:
        """Empty sequence raises ValueError.

        Technique: Error Guessing — degenerate input.
        """
        with _patch_now() as mock_dt:
            mock_dt.datetime.now.return_value = _FIXED_NOW
            mock_dt.timedelta = datetime.timedelta
            mock_dt.time = datetime.time
            with pytest.raises(ValueError, match="At least one target time"):
                _seconds_until([])

    def test_seconds_until_single_time_argument(self) -> None:
        """A bare datetime.time (not in a list) works.

        Technique: Equivalence Partitioning — scalar input class.
        """
        with _patch_now() as mock_dt:
            mock_dt.datetime.now.return_value = _FIXED_NOW
            mock_dt.timedelta = datetime.timedelta
            mock_dt.time = datetime.time
            result = _seconds_until(datetime.time(10, 30, 0))

        assert result == pytest.approx(1800.0)


# ---------------------------------------------------------------------------
# DeviceContext.sleep_until — integration smoke test
# ---------------------------------------------------------------------------


class TestSleepUntil:
    """Verify sleep_until delegates correctly."""

    async def test_sleep_until_delegates_to_sleep(self) -> None:
        """sleep_until computes seconds via _seconds_until then calls sleep.

        Technique: Branch Coverage — verify wiring.
        """
        with patch(
            "cosalette._context._device_context._seconds_until", return_value=600.0
        ):
            from cosalette._context import DeviceContext

            ctx = object.__new__(DeviceContext)
            ctx.sleep = AsyncMock()
            await ctx.sleep_until(datetime.time(12, 0, 0))

        ctx.sleep.assert_awaited_once_with(600.0)
