"""Unit tests for the opt-in event-loop stall watchdog (ADR-088).

With ``COSALETTE_LOOP_STALL_TIMEOUT`` set, a daemon thread exits the process
with code 6 once the event loop has not run for that many seconds.  These
tests inject the clock and the stall action, so nothing really exits; the
subprocess test in ``tests/integration/test_loop_stall_integration.py``
covers the real exit.

Test Techniques Used:
    - Boundary Value Analysis: stall time just under, at and just over the
      timeout; beat interval at the 5 s cap; timeout 0 and just above
    - Equivalence Partitioning: environment values (unset/blank, valid,
      non-numeric, non-positive, non-finite)
    - State Transition Testing: armed -> stopped, idempotent stop
    - Specification-based Testing: faulthandler backstop arming, exit-code
      mapping of an invalid value, arming only after startup
    - Mock-based Isolation: injected clock, on_stall and faulthandler
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar

import pytest

from cosalette import App
from cosalette._cli import _run_app
from cosalette._constants import EXIT_CONFIG_ERROR
from cosalette._context import AppContext
from cosalette._health._loop_stall import (
    BACKSTOP_FACTOR,
    LOOP_STALL_ENV,
    MAX_BEAT_INTERVAL,
    LoopStallConfigError,
    LoopStallWatchdog,
    loop_stall_timeout_from_env,
)
from cosalette._wiring._task_lifecycle import start_loop_stall_watchdog
from cosalette.testing import AppHarness, ManualClock, make_settings

pytestmark = pytest.mark.unit

TIMEOUT = 10.0
EPS = 1e-6


class _Clock:
    """A settable monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def stalls() -> list[tuple[float, float]]:
    return []


@pytest.fixture
def watchdog(clock: _Clock, stalls: list[tuple[float, float]]) -> LoopStallWatchdog:
    dog = LoopStallWatchdog(
        TIMEOUT,
        clock=clock,
        on_stall=lambda stalled_for, timeout: stalls.append((stalled_for, timeout)),
        backstop=False,
    )
    dog.beat()
    return dog


class TestTimeoutFromEnv:
    """``COSALETTE_LOOP_STALL_TIMEOUT`` parsing."""

    def test_unset_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(LOOP_STALL_ENV, raising=False)

        assert loop_stall_timeout_from_env() is None

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_is_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(LOOP_STALL_ENV, value)

        assert loop_stall_timeout_from_env() is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("300", 300.0), (" 0.5 ", 0.5), ("0.001", 0.001), ("1e3", 1000.0)],
    )
    def test_positive_number_is_the_timeout(
        self, monkeypatch: pytest.MonkeyPatch, value: str, expected: float
    ) -> None:
        monkeypatch.setenv(LOOP_STALL_ENV, value)

        assert loop_stall_timeout_from_env() == expected

    @pytest.mark.parametrize(
        "value",
        [
            "0",
            "0.0",
            "-0.001",
            "-1",
            "off",
            "5s",
            "nan",
            "inf",
            "-inf",
            "1e10",
            "1e308",
        ],
    )
    def test_invalid_value_raises(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """Technique: Boundary Value Analysis — 0 is the first invalid value."""
        monkeypatch.setenv(LOOP_STALL_ENV, value)

        with pytest.raises(LoopStallConfigError, match=LOOP_STALL_ENV):
            loop_stall_timeout_from_env()

    def test_run_app_exits_with_config_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv(LOOP_STALL_ENV, "-5")
        app = App("x")

        # Act
        with pytest.raises(SystemExit) as exc_info:
            _run_app(app, make_settings())

        # Assert
        assert exc_info.value.code == EXIT_CONFIG_ERROR


class TestInterval:
    """Beat and check interval: a quarter of the timeout, capped at 5 s."""

    @pytest.mark.parametrize(
        ("timeout", "expected"),
        [
            (0.2, 0.05),
            (4 * MAX_BEAT_INTERVAL - 0.4, MAX_BEAT_INTERVAL - 0.1),
            (4 * MAX_BEAT_INTERVAL, MAX_BEAT_INTERVAL),
            (300.0, MAX_BEAT_INTERVAL),
        ],
    )
    def test_interval(self, timeout: float, expected: float) -> None:
        assert LoopStallWatchdog(timeout).interval == pytest.approx(expected)


class TestCheck:
    """Stall detection boundaries around the timeout."""

    @pytest.mark.parametrize("elapsed", [0.0, TIMEOUT - EPS, TIMEOUT])
    def test_no_stall_up_to_the_timeout(
        self,
        watchdog: LoopStallWatchdog,
        clock: _Clock,
        stalls: list[tuple[float, float]],
        elapsed: float,
    ) -> None:
        clock.now = elapsed

        assert watchdog.check() is False
        assert stalls == []

    def test_stall_just_over_the_timeout(
        self,
        watchdog: LoopStallWatchdog,
        clock: _Clock,
        stalls: list[tuple[float, float]],
    ) -> None:
        clock.now = TIMEOUT + EPS

        assert watchdog.check() is True
        assert stalls == [(pytest.approx(TIMEOUT + EPS), TIMEOUT)]

    def test_beat_restarts_the_count(
        self,
        watchdog: LoopStallWatchdog,
        clock: _Clock,
        stalls: list[tuple[float, float]],
    ) -> None:
        # Arrange
        clock.now = TIMEOUT
        watchdog.beat()

        # Act
        clock.now = 2 * TIMEOUT

        # Assert
        assert watchdog.check() is False
        assert stalls == []

    def test_stop_disarms(
        self,
        watchdog: LoopStallWatchdog,
        clock: _Clock,
        stalls: list[tuple[float, float]],
    ) -> None:
        """Technique: State Transition — armed -> stopped, stop is idempotent."""
        # Act
        watchdog.stop()
        watchdog.stop()
        clock.now = 10 * TIMEOUT

        # Assert
        assert watchdog.check() is False
        assert stalls == []


class TestBackstop:
    """The faulthandler backstop is armed by each beat and cancelled by stop."""

    @pytest.fixture
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
        calls: list[tuple[str, Any]] = []
        monkeypatch.setattr(
            faulthandler,
            "dump_traceback_later",
            lambda timeout, **kwargs: calls.append(("arm", (timeout, kwargs))),
        )
        monkeypatch.setattr(
            faulthandler,
            "cancel_dump_traceback_later",
            lambda: calls.append(("cancel", None)),
        )
        return calls

    def test_beat_arms_and_stop_cancels(self, calls: list[tuple[str, Any]]) -> None:
        # Arrange
        dog = LoopStallWatchdog(TIMEOUT)

        # Act
        dog.beat()
        dog.stop()

        # Assert
        assert calls == [
            ("arm", (TIMEOUT * BACKSTOP_FACTOR, {"exit": True, "file": 2})),
            ("cancel", None),
        ]

    def test_disabled_backstop_leaves_faulthandler_alone(
        self, calls: list[tuple[str, Any]]
    ) -> None:
        dog = LoopStallWatchdog(TIMEOUT, backstop=False)

        dog.beat()
        dog.stop()

        assert calls == []


class TestOnTheLoop:
    """The watchdog with a real loop and thread, and a harmless stall action."""

    async def test_running_loop_does_not_stall(self) -> None:
        # Arrange
        stalled = threading.Event()
        dog = LoopStallWatchdog(0.2, on_stall=lambda *_: stalled.set(), backstop=False)

        # Act
        dog.start()
        await asyncio.sleep(0.5)
        dog.stop()

        # Assert
        assert not stalled.is_set()

    async def test_blocked_loop_is_detected(self) -> None:
        # Arrange
        stalled = threading.Event()
        dog = LoopStallWatchdog(0.1, on_stall=lambda *_: stalled.set(), backstop=False)
        dog.start()

        # Act: block the loop itself
        time.sleep(0.5)
        dog.stop()

        # Assert
        assert stalled.is_set()

    async def test_off_when_no_timeout(self) -> None:
        assert start_loop_stall_watchdog(None) is None

    async def test_arming_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("INFO", logger="cosalette._wiring"):
            dog = start_loop_stall_watchdog(30.0)
        assert dog is not None
        dog.stop()

        assert "exit code 6 after 30s" in caplog.text


@dataclass
class _RecordingWatchdog(LoopStallWatchdog):
    """Records every instance; no backstop so the test process is safe."""

    backstop: bool = False
    instances: ClassVar[list[_RecordingWatchdog]] = []

    def __post_init__(self) -> None:
        self.instances.append(self)


class TestAppLifecycle:
    """The app arms the watchdog after startup and disarms it on shutdown."""

    async def test_armed_after_lifespan_and_disarmed_on_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        monkeypatch.setenv(LOOP_STALL_ENV, "30")
        _RecordingWatchdog.instances = []
        monkeypatch.setattr(
            "cosalette._wiring._task_lifecycle.LoopStallWatchdog", _RecordingWatchdog
        )
        armed_in_lifespan: list[int] = []
        stopped_at_teardown: list[bool] = []

        @contextlib.asynccontextmanager
        async def lifespan(_: AppContext) -> AsyncIterator[None]:
            armed_in_lifespan.append(len(_RecordingWatchdog.instances))
            try:
                yield
            finally:
                # Disarm must precede teardown: a blocking __aexit__ must
                # never trigger exit 6 (ADR-088).
                stopped_at_teardown.extend(
                    d._stopped.is_set() for d in _RecordingWatchdog.instances
                )

        harness = AppHarness.create(clock=ManualClock(), lifespan=lifespan)

        @harness.app.telemetry("sensor", interval=10)
        async def sensor() -> dict[str, int]:
            return {"value": 1}

        # Act
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count("testapp/sensor/availability", 1)
        (dog,) = _RecordingWatchdog.instances
        armed_during_run = not dog._stopped.is_set()
        harness.trigger_shutdown()
        await task

        # Assert
        assert armed_in_lifespan == [0]
        assert dog.timeout == 30.0
        assert armed_during_run
        assert stopped_at_teardown == [True]
        assert dog._stopped.is_set()
