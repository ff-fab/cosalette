"""Unit tests for per-handler telemetry invocation timeout backstop (F-3 / ADR-024).

Covers: TimeoutError surfacing, retry composition (TimeoutError ⊂ OSError),
timeout=None disables backstop, auto-default = interval × factor, cron carve-out,
deferred/callable resolution, registration validation, and grouped handler timeout.

Test Techniques Used:
- Boundary Value Analysis: timeout > 0, == 0, < 0, =None; interval → auto-default
- State Transition Testing: timeout lifecycle: registration → resolution → enforcement
- Error Guessing: hung handler behavior, invalid timeout values at registration
- Specification-based: TimeoutError ⊆ OSError composition with retry machinery
- Decision Table: three timeout states (UNSET/None/float) × cron vs interval
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from cosalette._app import App
from cosalette._registration._model import _UNSET
from cosalette._retry import FixedBackoff
from cosalette.testing import FakeClock, MockMqttClient, make_settings


@dataclass
class _PerDevCfg:
    """Per-device config for per-device timeout resolution tests."""

    timeout_secs: float
    interval_secs: float = 5.0


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Registration / validation tests
# ---------------------------------------------------------------------------


class TestTelemetryTimeoutRegistration:
    """Validation and registration of the timeout parameter.

    Technique: Boundary Value Analysis + Error Guessing.
    """

    def test_timeout_float_stored_on_registration(self, app: App) -> None:
        """Explicit positive float timeout is stored on the registration."""

        @app.telemetry("sensor", interval=10, timeout=5.0)
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.timeout == 5.0

    def test_timeout_none_stored_on_registration(self, app: App) -> None:
        """Explicit None timeout (disabled backstop) is stored."""

        @app.telemetry("sensor", interval=10, timeout=None)
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.timeout is None

    def test_timeout_unset_default_stored_on_registration(self, app: App) -> None:
        """Omitting timeout stores the _UNSET sentinel (not yet resolved)."""

        @app.telemetry("sensor", interval=10)
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.timeout is _UNSET

    def test_timeout_zero_raises_at_registration(self, app: App) -> None:
        """Concrete timeout=0 raises ValueError at registration time."""
        with pytest.raises(ValueError, match="timeout"):

            @app.telemetry("sensor", interval=10, timeout=0)
            async def sensor() -> dict[str, object]:
                return {}

    def test_timeout_negative_raises_at_registration(self, app: App) -> None:
        """Concrete timeout=-1 raises ValueError at registration time."""
        with pytest.raises(ValueError, match="timeout"):

            @app.telemetry("sensor", interval=10, timeout=-1)
            async def sensor() -> dict[str, object]:
                return {}

    def test_timeout_callable_accepted_at_registration(self, app: App) -> None:
        """Callable timeout (deferred) is accepted without validation."""

        @app.telemetry("sensor", interval=10, timeout=lambda s: 5.0)
        async def sensor() -> dict[str, object]:
            return {}

        reg = app._telemetry[0]  # noqa: SLF001
        assert callable(reg.timeout)

    def test_timeout_add_telemetry_zero_raises(self, app: App) -> None:
        """app.add_telemetry with timeout=0 raises ValueError."""

        async def handler() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="timeout"):
            app.add_telemetry("sensor", handler, interval=10, timeout=0)

    def test_timeout_add_telemetry_positive_ok(self, app: App) -> None:
        """app.add_telemetry with positive timeout stores it."""

        async def handler() -> dict[str, object]:
            return {}

        app.add_telemetry("sensor", handler, interval=10, timeout=2.5)
        assert app._telemetry[0].timeout == 2.5  # noqa: SLF001

    def test_timeout_bool_raises_at_registration(self, app: App) -> None:
        """timeout=True (bool, subclass of int) is rejected at registration time."""
        with pytest.raises(ValueError, match="bool"):

            @app.telemetry("sensor", interval=10, timeout=True)  # type: ignore[arg-type]
            async def sensor() -> dict[str, object]:
                return {}


# ---------------------------------------------------------------------------
# Resolution tests
# ---------------------------------------------------------------------------


class TestTelemetryTimeoutResolution:
    """Timeout resolution at bootstrap: auto-default, cron carve-out, callable.

    Technique: Specification-based Testing + Boundary Value Analysis.
    """

    def test_auto_default_equals_interval(self, app: App) -> None:
        """UNSET timeout resolves to interval × 1.0 (no multiplier change)."""
        from cosalette._wiring import resolve_intervals, resolve_timeouts

        @app.telemetry("sensor", interval=5.0)
        async def sensor() -> dict[str, object]:
            return {}

        settings = make_settings()
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.timeout == 5.0

    def test_cron_carve_out_no_auto_default(self, app: App) -> None:
        """Cron-scheduled telemetry with UNSET timeout resolves to None."""
        from cosalette._wiring import resolve_intervals, resolve_timeouts

        @app.telemetry("sensor", schedule="0 * * * * ?")
        async def sensor() -> dict[str, object]:
            return {}

        settings = make_settings()
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.timeout is None

    def test_explicit_none_unchanged_after_resolution(self, app: App) -> None:
        """Explicit timeout=None stays None after resolve_timeouts."""
        from cosalette._wiring import resolve_intervals, resolve_timeouts

        @app.telemetry("sensor", interval=5.0, timeout=None)
        async def sensor() -> dict[str, object]:
            return {}

        settings = make_settings()
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.timeout is None

    def test_settings_callable_resolved(self, app: App) -> None:
        """Settings-callable timeout is resolved to a float."""
        from cosalette._wiring import resolve_intervals, resolve_timeouts

        @app.telemetry("sensor", interval=5.0, timeout=lambda s: 3.0)
        async def sensor() -> dict[str, object]:
            return {}

        settings = make_settings()
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.timeout == 3.0

    def test_callable_resolving_to_nonpositive_raises(self, app: App) -> None:
        """A callable timeout that resolves to ≤0 raises ValueError."""
        from cosalette._wiring import resolve_intervals, resolve_timeouts

        @app.telemetry("sensor", interval=5.0, timeout=lambda s: 0.0)
        async def sensor() -> dict[str, object]:
            return {}

        settings = make_settings()
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        with pytest.raises(ValueError, match="timeout"):
            resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

    def test_explicit_float_unchanged_after_resolution(self, app: App) -> None:
        """Explicit positive float timeout is unchanged after resolve_timeouts."""
        from cosalette._wiring import resolve_intervals, resolve_timeouts

        @app.telemetry("sensor", interval=5.0, timeout=2.0)
        async def sensor() -> dict[str, object]:
            return {}

        settings = make_settings()
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.timeout == 2.0

    def test_callable_interval_auto_default_uses_resolved_interval(
        self, app: App
    ) -> None:
        """Auto-default uses the RESOLVED interval (after resolve_intervals)."""
        from cosalette._wiring import resolve_intervals, resolve_timeouts

        @app.telemetry("sensor", interval=lambda s: 7.5)
        async def sensor() -> dict[str, object]:
            return {}

        settings = make_settings()
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.interval == 7.5
        assert reg.timeout == 7.5  # auto-default = 7.5 × 1.0


# ---------------------------------------------------------------------------
# Per-device callable timeout resolution tests
# ---------------------------------------------------------------------------


class TestTelemetryTimeoutPerDevice:
    """Per-device callable timeout resolution via dict-name telemetry.

    Mirrors ``TestPerDeviceInterval`` in ``test_dict_name.py``.
    Technique: Specification-based + Boundary Value Analysis + Error Guessing.
    """

    def test_per_device_callable_timeout_resolved_positive(self, app: App) -> None:
        """Dict-name with timeout=lambda cfg resolves to per-device floats."""
        from cosalette._wiring import (
            _expand_telemetry_names,
            resolve_intervals,
            resolve_timeouts,
        )

        @app.telemetry(
            name=lambda s: {
                "dev-a": _PerDevCfg(timeout_secs=3.0, interval_secs=5.0),
                "dev-b": _PerDevCfg(timeout_secs=7.0, interval_secs=5.0),
            },
            interval=lambda cfg: cfg.interval_secs,
            timeout=lambda cfg: cfg.timeout_secs,
        )
        async def handler() -> dict[str, object]:
            return {}

        settings = make_settings()
        _expand_telemetry_names(app._telemetry, settings)  # noqa: SLF001
        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

        assert len(app._telemetry) == 2  # noqa: SLF001
        by_name = {r.name: r for r in app._telemetry}  # noqa: SLF001
        assert by_name["dev-a"].timeout == 3.0
        assert by_name["dev-b"].timeout == 7.0

    def test_per_device_callable_timeout_nonpositive_raises(self, app: App) -> None:
        """Per-device callable timeout resolving to <=0 raises ValueError."""
        from cosalette._wiring import _expand_telemetry_names

        @app.telemetry(
            name=lambda s: {"bad": _PerDevCfg(timeout_secs=-1.0, interval_secs=5.0)},
            interval=lambda cfg: cfg.interval_secs,
            timeout=lambda cfg: cfg.timeout_secs,
        )
        async def handler() -> dict[str, object]:
            return {}

        settings = make_settings()
        with pytest.raises(ValueError, match="Per-device timeout"):
            _expand_telemetry_names(app._telemetry, settings)  # noqa: SLF001

    def test_per_device_unset_timeout_defaults_to_interval(self, app: App) -> None:
        """Multi-device reg with timeout omitted: _UNSET passes through expansion,
        then resolve_timeouts auto-defaults each per-device reg to its interval.
        """
        from cosalette._wiring import (
            _expand_telemetry_names,
            resolve_intervals,
            resolve_timeouts,
        )

        @app.telemetry(
            name=lambda s: {
                "dev-x": _PerDevCfg(timeout_secs=0.0, interval_secs=2.0),
                "dev-y": _PerDevCfg(timeout_secs=0.0, interval_secs=8.0),
            },
            interval=lambda cfg: cfg.interval_secs,
            # timeout omitted → _UNSET
        )
        async def handler() -> dict[str, object]:
            return {}

        settings = make_settings()
        _expand_telemetry_names(app._telemetry, settings)  # noqa: SLF001

        # _UNSET passes through expansion unchanged (timeout is not callable here)
        for reg in app._telemetry:  # noqa: SLF001
            assert reg.timeout is _UNSET

        resolve_intervals(app._telemetry, settings)  # noqa: SLF001
        resolve_timeouts(app._telemetry, settings)  # noqa: SLF001

        by_name = {r.name: r for r in app._telemetry}  # noqa: SLF001
        assert by_name["dev-x"].timeout == 2.0  # interval × 1.0
        assert by_name["dev-y"].timeout == 8.0


# ---------------------------------------------------------------------------
# Behavioral (runtime enforcement) tests
# ---------------------------------------------------------------------------


class TestTelemetryTimeoutEnforcement:
    """Runtime enforcement: hung handlers are timed out.

    Technique: Error Guessing + State Transition Testing.

    NOTE: asyncio.wait_for uses the REAL event-loop clock, so tiny real
    durations (0.01 s) are used here. FakeClock is retained for framework
    tick control; the handler-level timeout fires on real wall time.
    Each test is bounded by an outer wait_for(5 s) so suite hangs are
    impossible.
    """

    async def test_hung_handler_times_out_and_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A hung handler with timeout=0.01 causes a TimeoutError that is
        surfaced to the error publisher.
        """
        app = App(name="testapp", version="1.0.0")
        invoked = asyncio.Event()
        shutdown = asyncio.Event()

        @app.telemetry("sensor", interval=0.01, timeout=0.01)
        async def sensor() -> dict[str, object]:
            invoked.set()
            await asyncio.Event().wait()  # hangs forever
            return {}

        async def trigger_shutdown() -> None:
            await invoked.wait()
            # Give the timeout time to fire (real-time)
            await asyncio.sleep(0.25)
            shutdown.set()

        _task = asyncio.create_task(trigger_shutdown())
        try:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1

    async def test_hung_handler_logs_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A hung handler produces an error log entry."""
        app = App(name="testapp", version="1.0.0")
        invoked = asyncio.Event()
        shutdown = asyncio.Event()

        @app.telemetry("sensor", interval=0.01, timeout=0.01)
        async def sensor() -> dict[str, object]:
            invoked.set()
            await asyncio.Event().wait()
            return {}

        async def trigger_shutdown() -> None:
            await invoked.wait()
            await asyncio.sleep(0.25)
            shutdown.set()

        _task = asyncio.create_task(trigger_shutdown())
        try:
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
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        error_calls = [
            c for c in mock_logger.error.call_args_list if "sensor" in str(c)
        ]
        assert len(error_calls) >= 1

    async def test_timeout_none_does_not_cut_off_slow_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """timeout=None disables the backstop; a slow handler finishes without error."""
        app = App(name="testapp", version="1.0.0")
        done = asyncio.Event()
        shutdown = asyncio.Event()

        @app.telemetry("sensor", interval=0.01, timeout=None)
        async def sensor() -> dict[str, object]:
            # Sleep a tiny real time — no timeout should fire
            await asyncio.sleep(0.02)
            done.set()
            return {"v": 1}

        async def trigger_shutdown() -> None:
            await done.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        _task = asyncio.create_task(trigger_shutdown())
        try:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        # State was published (no timeout fired)
        state_messages = mock_mqtt.get_messages_for("testapp/sensor/state")
        assert len(state_messages) >= 1
        # No errors
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) == 0


class TestTelemetryTimeoutRetryComposition:
    """TimeoutError ⊂ OSError: timeout composes with retry machinery.

    Technique: Specification-based Testing.
    PEP 3151 establishes TimeoutError as a subclass of OSError.
    """

    async def test_hung_handler_retried_on_timeout(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """With retry=2, retry_on=(OSError,), a hung handler is retried —
        TimeoutError is an OSError so retry logic applies.
        """
        app = App(name="testapp", version="1.0.0")
        attempt_count = 0
        enough = asyncio.Event()
        shutdown = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            timeout=0.01,
            retry=2,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            nonlocal attempt_count
            attempt_count += 1
            enough.set()
            await asyncio.Event().wait()  # hang
            return {}

        async def trigger_shutdown() -> None:
            await enough.wait()
            # Wait long enough for retries to fire
            await asyncio.sleep(0.2)
            shutdown.set()

        _task = asyncio.create_task(trigger_shutdown())
        try:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        # Retries fired: at least initial attempt + at least one retry
        assert attempt_count >= 2

    async def test_timeout_retry_exhausted_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """After all retries exhausted on timeout, an error is published."""
        app = App(name="testapp", version="1.0.0")
        enough = asyncio.Event()
        shutdown = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            timeout=0.01,
            retry=2,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            enough.set()
            await asyncio.Event().wait()
            return {}

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.3)
            shutdown.set()

        _task = asyncio.create_task(trigger_shutdown())
        try:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1

    async def test_timeout_retry_logs_warning_per_attempt(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Each retry attempt on timeout logs a WARNING."""
        app = App(name="testapp", version="1.0.0")
        enough = asyncio.Event()
        shutdown = asyncio.Event()

        @app.telemetry(
            "sensor",
            interval=0.01,
            timeout=0.01,
            retry=2,
            retry_on=(OSError,),
            backoff=FixedBackoff(delay=0.001),
        )
        async def sensor() -> dict[str, object]:
            enough.set()
            await asyncio.Event().wait()
            return {}

        async def trigger_shutdown() -> None:
            await enough.wait()
            await asyncio.sleep(0.3)
            shutdown.set()

        _task = asyncio.create_task(trigger_shutdown())
        try:
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
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        retry_calls = [
            c
            for c in mock_logger.warning.call_args_list
            if "retry" in str(c).lower() and "sensor" in str(c)
        ]
        assert len(retry_calls) >= 1


# ---------------------------------------------------------------------------
# Grouped (coalescing) handler timeout
# ---------------------------------------------------------------------------


class TestTelemetryTimeoutGrouped:
    """Grouped path funnels through _attempt_with_retry → _try_invoke.

    Technique: State Transition Testing.
    """

    async def test_grouped_hung_handler_times_out(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A hung handler inside a coalescing group also times out."""
        app = App(name="testapp", version="1.0.0")
        invoked = asyncio.Event()
        shutdown = asyncio.Event()

        @app.telemetry("sensor_a", interval=0.01, group="grp", timeout=0.01)
        async def sensor_a() -> dict[str, object]:
            invoked.set()
            await asyncio.Event().wait()
            return {}

        async def trigger_shutdown() -> None:
            await invoked.wait()
            await asyncio.sleep(0.15)
            shutdown.set()

        _task = asyncio.create_task(trigger_shutdown())
        try:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1
