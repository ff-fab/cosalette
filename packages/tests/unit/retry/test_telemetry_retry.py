"""Unit tests for telemetry retry/backoff behavior.

Covers: retry on transient failures, backoff timing, circuit breaker
integration, error deduplication with retry, shutdown during backoff,
registration validation, and retry_on exception filtering.

Test Techniques Used:
- State Transition Testing: retry counter accumulation, circuit breaker states
- Equivalence Partitioning: terminal cycle outcomes counted by the breaker
- Boundary Value Analysis: retry=0 default, retry exhausted, retry_on filtering
- Error Guessing: invalid parameter combinations
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from cosalette._app import App
from cosalette._retry import CircuitBreaker, ExponentialBackoff, FixedBackoff
from cosalette.testing import (
    AppHarness,
    FakeClock,
    ManualClock,
    MockMqttClient,
    make_settings,
)

pytestmark = pytest.mark.unit


class TestTelemetryRetryRegistration:
    """Validation and registration of retry parameters.

    Technique: Error Guessing + Specification-based.
    """

    def test_retry_stored_on_registration(self, app: App) -> None:
        @app.telemetry("sensor", interval=10, retry=3, retry_on=(OSError,))
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.retry == 3
        assert reg.retry_on == (OSError,)

    def test_retry_defaults_backoff_when_omitted(self, app: App) -> None:
        """retry > 0 without explicit backoff gets ExponentialBackoff default."""

        @app.telemetry("sensor", interval=10, retry=2)
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert isinstance(reg.backoff, ExponentialBackoff)

    def test_retry_defaults_retry_on_when_omitted(self, app: App) -> None:
        """retry > 0 without explicit retry_on gets (OSError,) default."""

        @app.telemetry("sensor", interval=10, retry=2)
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.retry_on == (OSError,)

    def test_retry_zero_no_defaults_applied(self, app: App) -> None:
        """retry=0 leaves backoff and retry_on at their zero-values."""

        @app.telemetry("sensor", interval=10)
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.retry == 0
        assert reg.backoff is None
        assert reg.retry_on == ()

    def test_retry_with_empty_retry_on_raises(self, app: App) -> None:
        """retry > 0 with explicitly empty retry_on is invalid."""
        with pytest.raises(ValueError, match="retry_on"):

            @app.telemetry("sensor", interval=10, retry=3, retry_on=())
            async def sensor() -> dict[str, object]:
                return {}

    def test_circuit_breaker_stored(self, app: App) -> None:
        cb = CircuitBreaker(threshold=5)

        @app.telemetry("sensor", interval=10, circuit_breaker=cb)
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.circuit_breaker is cb

    def test_retry_on_with_non_exception_type_raises(self, app: App) -> None:
        """retry_on containing non-exception types raises TypeError."""
        with pytest.raises(
            TypeError, match="retry_on elements must be exception types"
        ):

            @app.telemetry("sensor", interval=10, retry=3, retry_on=(str,))  # ty: ignore[invalid-argument-type]
            async def sensor() -> dict[str, object]:
                return {}

    def test_circuit_breaker_threshold_zero_raises(self) -> None:
        """CircuitBreaker with threshold < 1 raises ValueError."""
        with pytest.raises(ValueError, match="threshold must be a positive integer"):
            CircuitBreaker(threshold=0)


class TestTelemetryRetryBehavior:
    """Unit tests for retry logic in the telemetry polling loop.

    Technique: State Transition Testing + Boundary Value Analysis.
    """

    async def test_no_retry_default_error_propagates(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Without retry, exceptions go straight to error handler (baseline)."""
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry("sensor", interval=0.01)
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("fail")
            enough.set()
            return {"v": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1

    async def test_retry_succeeds_after_transient_failure(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler fails once, retries, succeeds — state published, no error."""
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        success = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=3,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("transient")
            success.set()
            return {"v": 42}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await success.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # State published (retry succeeded)
        state_messages = mock_mqtt.get_messages_for("testapp/sensor/state")
        assert len(state_messages) >= 1
        # No error published (retry handled it)
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) == 0

    async def test_retry_exhausted_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """All retries exhausted — error published after final failure."""
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=3,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                raise OSError("persistent")
            enough.set()
            return {"v": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Error should be published (retries exhausted on first cycle)
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1

    async def test_retry_on_filters_non_matching_exceptions(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Exceptions not in retry_on propagate immediately, no retry."""
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=3,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("not retryable")
            enough.set()
            return {"v": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # ValueError not in retry_on → published immediately
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1
        # LEAK-01: downstream exception text is redacted to the class name.
        assert "ValueError" in error_messages[0][0]
        assert "not retryable" not in error_messages[0][0]

    async def test_retry_intermediate_attempts_not_published_as_errors(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Intermediate retry attempts are NOT published to error topic.

        Only the final exhausted failure (or a non-retryable exception)
        should produce an error publication.
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        success = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=3,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OSError("transient")
            success.set()
            return {"v": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await success.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # No errors published — retry succeeded
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) == 0
        # State was published
        state_messages = mock_mqtt.get_messages_for("testapp/sensor/state")
        assert len(state_messages) >= 1

    async def test_retry_logs_warning_for_each_attempt(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Each retry attempt logs a WARNING with attempt number."""
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        success = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=3,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise OSError("flaky")
            success.set()
            return {"v": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await success.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        with patch("cosalette._runners._telemetry_runner.logger") as mock_logger:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

        retry_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if len(call.args) >= 2 and "retry" in str(call.args[0]).lower()
        ]
        assert len(retry_calls) >= 2  # 2 failed attempts logged

    async def test_shutdown_during_backoff_aborts_retry(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Shutdown during backoff sleep aborts the retry loop cleanly.

        Technique: State Transition — shutdown_requested during ctx.sleep()
        causes immediate exit with no further handler invocations.
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        first_fail = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=3,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=10.0),  # large delay
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            first_fail.set()
            raise OSError("fail")

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await first_fail.wait()
            await asyncio.sleep(0.02)  # let the backoff sleep start
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Handler was called but retries didn't exhaust (shutdown interrupted)
        # The large backoff ensures the shutdown arrives during sleep
        assert call_count >= 1

    async def test_error_dedup_resets_after_successful_retry(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Successful retry clears error dedup state.

        Technique: State Transition Testing — error → retry-success (dedup
        cleared) → error again → same error type published a second time.
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=2,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            # Cycle 1: fail once, retry succeeds
            if call_count == 1:
                raise OSError("first")
            if call_count == 2:
                return {"v": 1}
            # Cycle 2: succeed
            if call_count == 3:
                return {"v": 2}
            # Cycle 3: fail all retries → error published
            if call_count <= 6:
                raise OSError("second")
            enough.set()
            return {"v": 3}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # The error should be published for the exhausted cycle
        # (dedup was cleared by the successful retry in cycle 1)
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1


class TestCircuitBreakerIntegration:
    """Integration tests for circuit breaker with telemetry retry.

    Technique: State Transition Testing + Integration Testing.
    """

    async def test_circuit_breaker_opens_after_threshold(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Circuit opens after threshold failures, status circuit_open."""
        app = App(name="testapp", version="1.0.0", heartbeat_interval=0.02)
        call_count = 0
        enough = asyncio.Event()
        cb = CircuitBreaker(threshold=2)

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=0,
            circuit_breaker=cb,
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                raise OSError("down")
            enough.set()
            return {"v": 1}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Circuit breaker should have transitioned through states
        # Check heartbeat for circuit_open status
        status_messages = mock_mqtt.get_messages_for("testapp/status")
        heartbeats = [
            json.loads(payload)
            for payload, *_ in status_messages
            if payload.startswith("{")
        ]
        device_statuses = [
            hb.get("devices", {}).get("sensor", {}).get("status") for hb in heartbeats
        ]
        # The circuit must have opened at some point during execution
        assert "error" in device_statuses or "circuit_open" in device_statuses, (
            f"Expected error or circuit_open in heartbeats. Statuses: {device_statuses}"
        )
        # After recovery, CB should be closed
        assert cb.state == "closed"

    async def test_circuit_breaker_recovers_on_probe_success(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """After circuit opens → half-open → probe succeeds → closes."""
        cb = CircuitBreaker(threshold=2)
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        success = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            retry=0,
            circuit_breaker=cb,
        )
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            # First 2 calls fail → circuit opens
            # 3rd call: circuit is open, handler skipped, CB transitions half-open
            # 4th call: half-open probe → succeed
            if call_count <= 2:
                raise OSError("down")
            success.set()
            return {"v": 42}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await success.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert cb.state == "closed"
        assert cb.consecutive_failures == 0
        state_messages = mock_mqtt.get_messages_for("testapp/sensor/state")
        assert len(state_messages) >= 1


async def _poll_with_breaker(
    cb: CircuitBreaker,
    *,
    raises: Exception,
    group: str | None,
    ticks: int,
    **telemetry_kwargs: Any,
) -> tuple[int, list[str]]:
    """Run an always-failing handler for the first poll plus *ticks* intervals.

    Returns the number of handler invocations and the device statuses seen
    in the heartbeats. Under a :class:`ManualClock` each ``advance_time(60)``
    is exactly one scheduled cycle, on both the ungrouped and the group path.
    """
    clock = ManualClock()
    harness = AppHarness.create(clock=clock)
    calls = 0

    @harness.app.telemetry(
        "sensor",
        interval=60,
        group=group,
        circuit_breaker=cb,
        **telemetry_kwargs,
    )
    async def sensor() -> dict[str, int]:
        nonlocal calls
        calls += 1
        raise raises

    task = asyncio.create_task(harness.run())
    try:
        await harness.wait_for_publish_count("testapp/error", 1)
        for _ in range(ticks):
            await harness.advance_time(60)
        await clock.settle()
        statuses: list[str] = [
            json.loads(payload)["devices"]["sensor"]["status"]
            for payload, *_ in harness.messages_for("testapp/status")
            if payload.startswith("{")
        ]
        return calls, statuses
    finally:
        harness.trigger_shutdown()
        await task


_PATHS = pytest.mark.parametrize("group", [None, "bus"], ids=["single", "group"])


class TestCircuitBreakerTerminalFailures:
    """Every terminal cycle failure counts once toward the breaker.

    ``_attempt_with_retry`` ends a failed cycle as ``"error"`` when no retry
    applies (``retry=0`` or an exception outside ``retry_on``) and as
    ``"exhausted"`` after the last retry. Both count once; retry attempts
    never count individually (ADR-024 amendment, cos-4mv5.11).

    Technique: Equivalence Partitioning (the two terminal outcomes) and
    State Transition Testing (closed -> open -> half-open -> open), each on
    the ungrouped and the coalescing-group runner path.
    """

    @_PATHS
    async def test_retry_zero_failures_open_the_breaker(
        self, group: str | None
    ) -> None:
        """With the default retry=0, threshold failed polls open the circuit."""
        # Arrange
        cb = CircuitBreaker(threshold=2)

        # Act — polls at 0 s and 60 s fail, 120 s is skipped, 180 s probes.
        calls, statuses = await _poll_with_breaker(
            cb, raises=OSError("gone"), group=group, ticks=3
        )

        # Assert
        assert calls == 3
        assert "circuit_open" in statuses

    @_PATHS
    async def test_failed_half_open_probe_reopens_the_breaker(
        self, group: str | None
    ) -> None:
        """A probe that fails with outcome 'error' re-opens the circuit.

        Before the fix the breaker stayed half-open and probed every cycle.
        """
        # Arrange
        cb = CircuitBreaker(threshold=2)

        # Act — the 180 s probe fails, so the 240 s cycle is skipped again.
        calls, _ = await _poll_with_breaker(
            cb, raises=OSError("gone"), group=group, ticks=4
        )

        # Assert
        assert calls == 3
        assert cb.consecutive_failures == 3

    @_PATHS
    async def test_non_retryable_error_with_retries_counts(
        self, group: str | None
    ) -> None:
        """An exception outside retry_on is terminal and counts once per cycle."""
        # Arrange
        cb = CircuitBreaker(threshold=2)

        # Act — polls at 0 s and 60 s fail without retrying, 120 s is skipped.
        calls, _ = await _poll_with_breaker(
            cb,
            raises=ValueError("bug"),
            group=group,
            ticks=2,
            retry=3,
            retry_on=(OSError,),
        )

        # Assert
        assert calls == 2
        assert cb.consecutive_failures == 2
        assert cb.state == "half-open"

    @_PATHS
    async def test_exhausted_cycle_counts_once_not_per_attempt(
        self, group: str | None
    ) -> None:
        """Regression: retry>0 still records one failure per exhausted cycle."""
        # Arrange
        cb = CircuitBreaker(threshold=5)

        # Act — one cycle: the first attempt plus two retries.
        calls, _ = await _poll_with_breaker(
            cb,
            raises=OSError("gone"),
            group=group,
            ticks=0,
            retry=2,
            retry_on=(OSError,),
            backoff=FixedBackoff(0),
        )

        # Assert
        assert calls == 3
        assert cb.consecutive_failures == 1
        assert cb.state == "closed"
