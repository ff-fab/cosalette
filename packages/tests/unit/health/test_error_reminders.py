"""Unit tests for persistent telemetry error reminders and the recovery log.

ADR-082: the first error of each type is logged and published; while it
persists, the 2nd, 4th, 8th, ... failure within the first
``error_reminder_interval`` and then one failure per interval are logged at
WARNING and republished with ``details.count`` and ``details.first_seen``.
Recovery logs one INFO line with the outage duration and cycle count.

Test Techniques Used:
    - State Transition Testing: healthy -> failing (onset) -> reminders ->
      recovered, replayed on a FakeClock incident timeline
    - Boundary Value Analysis: a failure exactly at and just before the
      interval boundary; a poll gap longer than the interval
    - Decision Table Testing: reminder interval set vs. ``None``
    - Equivalence Partitioning: valid vs. invalid ``error_reminder_interval``
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from cosalette._app import App
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._health._reporter import format_duration
from cosalette._registration import _TelemetryRegistration
from cosalette._runners._telemetry_runner import TelemetryRunner
from cosalette.testing import FakeClock, MockMqttClient

pytestmark = pytest.mark.unit

PREFIX = "myapp"
HOUR = 3600.0
POLL = 300.0  # a 5-minute poller, as in the airthings2mqtt incidents
RUNNER_LOGGER = "cosalette._runners._telemetry_runner"


class SensorError(Exception):
    """Stand-in for a downstream adapter error such as BleakError."""


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(0.0)


@pytest.fixture
def mock_mqtt() -> MockMqttClient:
    return MockMqttClient()


def _reporter(
    mock_mqtt: MockMqttClient, clock: FakeClock, interval: float | None = HOUR
) -> HealthReporter:
    reporter = HealthReporter(
        mqtt=mock_mqtt,
        topic_prefix=PREFIX,
        version="1.0.0",
        clock=clock,
        error_reminder_interval=interval,
    )
    reporter.set_device_status("sensor")
    reporter.track_freshness("sensor", None)
    return reporter


async def _never_called() -> dict[str, object] | None:
    """Handler stub: these tests drive the runner helpers directly."""
    return None  # pragma: no cover


def _reg() -> _TelemetryRegistration:
    return _TelemetryRegistration(
        name="sensor", func=_never_called, injection_plan=[], interval=POLL
    )


def _remind_counts(
    reporter: HealthReporter, clock: FakeClock, failures: int, step: float
) -> list[int]:
    """Fail *failures* cycles *step* apart; return the counts due a reminder."""
    due = []
    for _ in range(failures):
        streak = reporter.record_failure("sensor", SensorError("gone"))
        assert streak is not None
        if streak.remind:
            due.append(streak.count)
        clock.advance(step)
    return due


def _error_payloads(mock_mqtt: MockMqttClient) -> list[dict[str, Any]]:
    return [json.loads(p) for p, *_ in mock_mqtt.get_messages_for(f"{PREFIX}/error")]


# ---------------------------------------------------------------------------
# Reminder schedule (HealthReporter)
# ---------------------------------------------------------------------------


class TestReminderSchedule:
    """When a failure streak is due a reminder.

    Technique: State Transition Testing on a FakeClock timeline.
    """

    def test_doubling_in_first_interval_then_hourly(
        self, mock_mqtt: MockMqttClient, clock: FakeClock
    ) -> None:
        """An 11-hour outage of a 5-minute poller: 2, 4, 8, then hourly."""
        # Arrange
        reporter = _reporter(mock_mqtt, clock)

        # Act — 133 failures span t=0 .. t=11h
        due = _remind_counts(reporter, clock, failures=133, step=POLL)

        # Assert — failure 12m+1 is the first one at m hours
        assert due == [2, 4, 8, *(12 * m + 1 for m in range(1, 12))]

    def test_failure_exactly_at_interval_is_due(
        self, mock_mqtt: MockMqttClient, clock: FakeClock
    ) -> None:
        """Technique: Boundary Value Analysis — age == interval reminds."""
        reporter = _reporter(mock_mqtt, clock, interval=100.0)
        reporter.record_failure("sensor", SensorError())
        clock.advance(99.0)
        early = reporter.record_failure("sensor", SensorError())  # count 2
        reporter.record_failure("sensor", SensorError())  # count 3
        clock.advance(1.0)

        at_boundary = reporter.record_failure("sensor", SensorError())

        assert early is not None and early.remind is True
        assert at_boundary is not None
        assert (at_boundary.count, at_boundary.remind) == (4, True)

    def test_power_of_two_after_first_interval_is_not_due(
        self, mock_mqtt: MockMqttClient, clock: FakeClock
    ) -> None:
        """Technique: Boundary Value Analysis — doubling stops at the interval."""
        reporter = _reporter(mock_mqtt, clock, interval=100.0)
        due = _remind_counts(reporter, clock, failures=16, step=10.0)

        # 2, 4, 8 by t=70; failure 11 at t=100; failure 16 (t=150) is not
        # due because the doubling phase has ended.
        assert due == [2, 4, 8, 11]

    def test_poll_gap_longer_than_interval_reminds_each_failure_once(
        self, mock_mqtt: MockMqttClient, clock: FakeClock
    ) -> None:
        """A slow poller reminds every cycle without a catch-up burst."""
        reporter = _reporter(mock_mqtt, clock)

        due = _remind_counts(reporter, clock, failures=4, step=2 * HOUR)

        assert due == [2, 3, 4]

    def test_none_interval_never_reminds(
        self, mock_mqtt: MockMqttClient, clock: FakeClock
    ) -> None:
        """Technique: Decision Table Testing — reminders disabled."""
        reporter = _reporter(mock_mqtt, clock, interval=None)

        assert _remind_counts(reporter, clock, failures=50, step=POLL) == []

    def test_untracked_entity_has_no_streak(
        self, mock_mqtt: MockMqttClient, clock: FakeClock
    ) -> None:
        reporter = _reporter(mock_mqtt, clock)

        assert reporter.record_failure("other", SensorError()) is None

    async def test_success_returns_and_resets_the_streak(
        self, mock_mqtt: MockMqttClient, clock: FakeClock
    ) -> None:
        reporter = _reporter(mock_mqtt, clock)
        _remind_counts(reporter, clock, failures=3, step=POLL)

        ended = await reporter.record_success("sensor")
        again = await reporter.record_success("sensor")

        assert ended is not None
        assert (ended.count, ended.duration) == (3, 3 * POLL)
        assert again is None


class TestHeartbeatStreak:
    """The heartbeat exposes the current streak's error and start.

    Technique: State Transition Testing — failing -> recovered.
    """

    async def test_streak_fields_set_while_failing_and_cleared_on_recovery(
        self, mock_mqtt: MockMqttClient, clock: FakeClock
    ) -> None:
        reporter = _reporter(mock_mqtt, clock)
        reporter.record_failure("sensor", SensorError())
        await reporter.publish_heartbeat()
        await reporter.record_success("sensor")
        await reporter.publish_heartbeat()

        failing, recovered = (
            json.loads(p)["devices"]["sensor"]
            for p, *_ in mock_mqtt.get_messages_for(f"{PREFIX}/status")
        )

        assert failing["last_error"] == "SensorError"
        assert failing["failing_since"].endswith("+00:00")
        assert (recovered["last_error"], recovered["failing_since"]) == (None, None)


# ---------------------------------------------------------------------------
# Runner: publish, log and recover (incident replay)
# ---------------------------------------------------------------------------


async def _replay_incident(
    reporter: HealthReporter,
    clock: FakeClock,
    mock_mqtt: MockMqttClient,
    failures: int,
) -> None:
    """Fail *failures* cycles of a 5-minute poller, then recover."""
    runner = TelemetryRunner(None)
    publisher = ErrorPublisher(mqtt=mock_mqtt, topic_prefix=PREFIX)
    last: type[Exception] | None = None
    for _ in range(failures):
        last = await runner._handle_telemetry_error(
            _reg(), SensorError("gone"), last, publisher, reporter
        )
        clock.advance(POLL)
    await runner._clear_telemetry_error("sensor", last, reporter)


class TestRunnerReminders:
    """The runner republishes and logs reminders and logs the recovery.

    Technique: State Transition Testing — incident replay on a FakeClock.
    """

    async def test_incident_replay_publishes_onset_and_reminders(
        self,
        mock_mqtt: MockMqttClient,
        clock: FakeClock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Arrange
        reporter = _reporter(mock_mqtt, clock)
        caplog.set_level(logging.INFO, logger=RUNNER_LOGGER)

        # Act — 26 failures: t=0 .. 2h05m, recovery at 2h10m
        await _replay_incident(reporter, clock, mock_mqtt, failures=26)

        # Assert
        payloads = _error_payloads(mock_mqtt)
        assert [p["details"]["count"] for p in payloads] == [1, 2, 4, 8, 13, 25]
        assert {p["details"]["first_seen"] for p in payloads} == {
            payloads[0]["details"]["first_seen"]
        }
        warnings = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING and r.name == RUNNER_LOGGER
        ]
        assert warnings[-1] == (
            "Telemetry 'sensor' still failing after 2h00m00s "
            "(25 consecutive failures): gone"
        )
        assert caplog.records[-1].getMessage() == (
            "Telemetry 'sensor' recovered after 2h10m00s (26 failed cycles)"
        )

    async def test_none_interval_logs_only_onset_and_recovery(
        self,
        mock_mqtt: MockMqttClient,
        clock: FakeClock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Technique: Decision Table Testing — reminders disabled."""
        reporter = _reporter(mock_mqtt, clock, interval=None)
        caplog.set_level(logging.INFO, logger=RUNNER_LOGGER)

        await _replay_incident(reporter, clock, mock_mqtt, failures=26)

        assert [p["details"]["count"] for p in _error_payloads(mock_mqtt)] == [1]
        runner_lines = [
            (r.levelname, r.getMessage())
            for r in caplog.records
            if r.name == RUNNER_LOGGER
        ]
        assert runner_lines == [
            ("ERROR", "Telemetry 'sensor' error: gone"),
            ("INFO", "Telemetry 'sensor' recovered after 2h10m00s (26 failed cycles)"),
        ]


# ---------------------------------------------------------------------------
# Configuration and formatting
# ---------------------------------------------------------------------------


class TestErrorReminderInterval:
    """``App(error_reminder_interval=)`` validation.

    Technique: Equivalence Partitioning — positive / ``None`` / non-positive.
    """

    @pytest.mark.parametrize("value", [0, -1.0])
    def test_non_positive_rejected(self, value: float) -> None:
        with pytest.raises(ValueError, match="error_reminder_interval"):
            App(name="testapp", error_reminder_interval=value)

    @pytest.mark.parametrize("value", [None, 0.5, HOUR])
    def test_positive_or_none_accepted(self, value: float | None) -> None:
        App(name="testapp", error_reminder_interval=value)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "0s"),
        (59.9, "59s"),
        (60.0, "1m00s"),
        (3599.0, "59m59s"),
        (3600.0, "1h00m00s"),
        (39_900.0, "11h05m00s"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    """Technique: Boundary Value Analysis — unit roll-overs."""
    assert format_duration(seconds) == expected
