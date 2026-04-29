"""Unit tests for schedule= parameter on telemetry registration and runner dispatch.

Test Techniques Used:
- Equivalence Partitioning: schedule as string vs CronSchedule instance
- Decision Table: interval/schedule mutual exclusivity matrix
- Branch Coverage: _sleep_seconds dispatch branches
- Error Guessing: both or neither parameter provided
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cosalette._cron import CronSchedule
from cosalette._registration import _TelemetryRegistration
from cosalette._runners._telemetry_runner import _sleep_seconds

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _dummy() -> dict[str, object] | None:
    return {"ok": True}


def _make_reg(
    *,
    interval: float = 0.0,
    schedule: CronSchedule | None = None,
) -> _TelemetryRegistration:
    return _TelemetryRegistration(
        name="t",
        func=_dummy,
        injection_plan=[],
        interval=interval,
        schedule=schedule,
    )


# ---------------------------------------------------------------------------
# Registration — decorator path
# ---------------------------------------------------------------------------


class TestTelemetryDecoratorSchedule:
    """Registration via @app.telemetry()."""

    def test_telemetry_schedule_string_stores_cron_schedule(self, app) -> None:
        """String schedule is parsed into a CronSchedule instance.

        Technique: Equivalence Partitioning — string input class.
        """

        @app.telemetry("t", schedule="0 */5 * * * ?")
        async def handler() -> dict[str, object]:
            return {}

        reg = app._telemetry[-1]
        assert isinstance(reg.schedule, CronSchedule)

    def test_telemetry_schedule_instance_stores_cron_schedule(self, app) -> None:
        """Pre-built CronSchedule is stored as-is.

        Technique: Equivalence Partitioning — CronSchedule input class.
        """
        sched = CronSchedule("0 */5 * * * ?")

        @app.telemetry("t", schedule=sched)
        async def handler() -> dict[str, object]:
            return {}

        reg = app._telemetry[-1]
        assert reg.schedule is sched

    def test_telemetry_schedule_sets_sentinel_interval(self, app) -> None:
        """When schedule= is set, interval defaults to 0.0 sentinel.

        Technique: Decision Table — schedule-only row.
        """

        @app.telemetry("t", schedule="0 0 * * * ?")
        async def handler() -> dict[str, object]:
            return {}

        reg = app._telemetry[-1]
        assert reg.interval == 0.0

    def test_telemetry_interval_and_schedule_raises(self, app) -> None:
        """Providing both interval= and schedule= is an error.

        Technique: Decision Table — both-present row.
        """
        with pytest.raises(ValueError, match="mutually exclusive"):

            @app.telemetry("t", interval=10, schedule="0 0 * * * ?")
            async def handler() -> dict[str, object]:
                return {}

    def test_telemetry_neither_interval_nor_schedule_raises(self, app) -> None:
        """Providing neither interval= nor schedule= is an error.

        Technique: Decision Table — neither-present row.
        """
        with pytest.raises(ValueError, match="required"):

            @app.telemetry("t")
            async def handler() -> dict[str, object]:
                return {}

    def test_telemetry_schedule_with_group_raises(self, app) -> None:
        """schedule= and group= are mutually exclusive.

        Technique: Decision Table — schedule+group row.
        """
        with pytest.raises(ValueError, match="schedule.*group|group.*schedule"):

            @app.telemetry("t", schedule="0 */5 * * * ?", group="sensors")
            async def t() -> dict[str, object]:
                return {}


# ---------------------------------------------------------------------------
# Registration — imperative path
# ---------------------------------------------------------------------------


class TestAddTelemetrySchedule:
    """Registration via app.add_telemetry()."""

    def test_add_telemetry_schedule_string_parses(self, app) -> None:
        """String schedule is parsed into CronSchedule on imperative path.

        Technique: Equivalence Partitioning — string input class.
        """
        app.add_telemetry("t", _dummy, schedule="0 */5 * * * ?")
        reg = app._telemetry[-1]
        assert isinstance(reg.schedule, CronSchedule)

    def test_add_telemetry_interval_and_schedule_raises(self, app) -> None:
        """Providing both interval= and schedule= is an error.

        Technique: Decision Table — both-present row (imperative).
        """
        with pytest.raises(ValueError, match="mutually exclusive"):
            app.add_telemetry("t", _dummy, interval=10, schedule="0 0 * * * ?")


# ---------------------------------------------------------------------------
# Runner dispatch — _sleep_seconds
# ---------------------------------------------------------------------------


class TestSleepSecondsDispatch:
    """_sleep_seconds branches on schedule vs interval."""

    def test_sleep_seconds_with_schedule_uses_cron(self) -> None:
        """When schedule is set, delegates to _seconds_until_next_fire.

        Technique: Branch Coverage — schedule-present branch.
        """
        sched = CronSchedule("0 0 * * * ?")
        reg = _make_reg(schedule=sched)

        with patch(
            "cosalette._runners._telemetry_runner._seconds_until_next_fire",
            return_value=42.5,
        ) as mock_fn:
            result = _sleep_seconds(reg)

        mock_fn.assert_called_once_with(sched)
        assert result == 42.5

    def test_sleep_seconds_without_schedule_uses_interval(self) -> None:
        """When schedule is None, falls through to _resolved_interval.

        Technique: Branch Coverage — schedule-absent branch.
        """
        reg = _make_reg(interval=30.0)

        with patch(
            "cosalette._runners._telemetry_runner._resolved_interval",
            return_value=30.0,
        ) as mock_fn:
            result = _sleep_seconds(reg)

        mock_fn.assert_called_once_with(reg)
        assert result == 30.0
