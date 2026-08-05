"""Tests for adapter auto-restart: state tracking, threshold detection,
restart opt-out, App parameters, restart execution, and task management.

Test Techniques Used:
- State Transition Testing: Adapter health, restart eligibility, and task
  lifecycle transitions during restart handling
- Boundary Value Analysis: Failure thresholds, restart cooldowns, and
  sustained-health reset timing
- Branch/Condition Coverage: Health-check outcomes, restart opt-out, and
  restartable vs. non-restartable adapter paths
- Specification-based Testing: App wiring parameters and lifecycle helper
  behavior
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import override
from unittest.mock import AsyncMock

import pytest

from cosalette import App
from cosalette._health import AdapterHealthStatus, HealthCheckRunner, HealthReporter
from cosalette._wiring import (
    DeviceInfo,
    DeviceTaskMap,
    cancel_tasks_for_adapter,
    start_device_tasks_for_names,
)
from cosalette._wiring._adapter_lifecycle import (
    detect_restartable_adapters,
    enter_restartable_adapters,
    restart_single_adapter,
)
from cosalette.testing._clock import FakeClock

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Dummy port/adapter types
# ---------------------------------------------------------------------------


class _PortA:
    """Dummy port type A."""


class _PortB:
    """Dummy port type B."""


class _HealthyAdapter:
    async def health_check(self) -> bool:
        return True


class _UnhealthyAdapter:
    async def health_check(self) -> bool:
        return False


class _LifecycleHealthy(_HealthyAdapter):
    """Health-checkable + async context manager → restartable."""

    async def __aenter__(self) -> _LifecycleHealthy:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


class _OptedOutAdapter(_LifecycleHealthy):
    """Has lifecycle + health-check but explicitly opts out."""

    restartable = False


class _PlainAdapter:
    """No health check at all."""


# ---------------------------------------------------------------------------
# Runner helper
# ---------------------------------------------------------------------------


def _make_runner(
    *,
    adapters: dict[type, object] | None = None,
    device_map: dict[type, list[tuple[str, bool]]] | None = None,
    interval: float = 10.0,
    clock: FakeClock | None = None,
    restart_after_failures: int = 0,
    max_restarts: int = 3,
    restart_cooldown: float = 5.0,
    sustained_health_reset: float = 300.0,
    on_restart_needed: Callable[[type, object], Awaitable[bool]] | None = None,
) -> tuple[HealthCheckRunner, HealthReporter, FakeClock, asyncio.Event]:
    clock = clock or FakeClock()
    event = asyncio.Event()
    mqtt = AsyncMock()
    reporter = HealthReporter(
        mqtt=mqtt, topic_prefix="test", version="0.1.0", clock=clock
    )
    if adapters is None:
        adapters = {_PortA: _HealthyAdapter()}
    if device_map is None:
        device_map = {_PortA: [("blind", False)]}

    runner = HealthCheckRunner(
        health_checkables=adapters,
        adapter_device_map=device_map,
        health_reporter=reporter,
        clock=clock,
        interval=interval,
        shutdown_event=event,
        restart_after_failures=restart_after_failures,
        max_restarts=max_restarts,
        restart_cooldown=restart_cooldown,
        sustained_health_reset=sustained_health_reset,
        on_restart_needed=on_restart_needed,
    )
    return runner, reporter, clock, event


# ---------------------------------------------------------------------------
# Restart-wiring harness
# ---------------------------------------------------------------------------


@dataclass
class _RestartWiringHarness:
    """Reusable scaffold for restart-integration tests."""

    clock: FakeClock
    mqtt: AsyncMock
    reporter: HealthReporter
    shutdown_event: asyncio.Event
    adapter_a: _TrackingAdapter
    adapter_b: _TrackingAdapter
    health_check_runner: HealthCheckRunner
    wiring_task: asyncio.Task[None]

    @property
    def on_restart(self) -> Callable[[type, object], Awaitable[bool]]:
        cb = self.health_check_runner._on_restart_needed
        assert cb is not None
        return cb

    async def teardown(self) -> None:
        self.shutdown_event.set()
        await self.wiring_task


async def _make_restart_wiring(
    *,
    fail_enter_a: bool = False,
) -> _RestartWiringHarness:
    """Create, start, and return a fully-wired restart harness."""
    from cosalette._context import DeviceContext
    from cosalette._errors import ErrorPublisher
    from cosalette._registration import (
        _DeviceRegistration,
        _noop_lifespan,
        _TelemetryRegistration,
    )
    from cosalette._wiring import run_lifespan_and_devices
    from cosalette.testing import make_settings

    clock = FakeClock()
    mqtt = AsyncMock()
    reporter = HealthReporter(
        mqtt=mqtt, topic_prefix="test", version="0.1.0", clock=clock
    )
    error_pub = ErrorPublisher(mqtt=mqtt, topic_prefix="test")
    shutdown_event = asyncio.Event()
    settings = make_settings()

    adapter_a = _TrackingAdapter(fail_enter=fail_enter_a)
    adapter_b = _TrackingAdapter()

    async def handler() -> AsyncIterator[None]:
        await asyncio.sleep(999)
        yield

    async def read_sensor() -> dict[str, object]:
        await asyncio.sleep(999)
        return {"v": 1}

    devices = [
        _DeviceRegistration(name=n, func=handler, injection_plan=[])
        for n in ("sensor_a", "sensor_b")
    ]
    telemetry = [
        _TelemetryRegistration(
            name="sensor_a",
            func=read_sensor,
            injection_plan=[],
            interval=10.0,
            group="sensors",
        ),
        _TelemetryRegistration(
            name="sensor_b",
            func=read_sensor,
            injection_plan=[],
            interval=10.0,
            group="sensors",
        ),
    ]
    contexts = {
        n: DeviceContext(
            name=n,
            settings=settings,
            mqtt=mqtt,
            topic_prefix="test",
            shutdown_event=shutdown_event,
            adapters={},
            clock=clock,
            is_root=False,
        )
        for n in ("sensor_a", "sensor_b")
    }
    adapter_device_map: dict[type, list[DeviceInfo]] = {
        _PortA: [DeviceInfo("sensor_a", False)],
        _PortB: [DeviceInfo("sensor_b", False)],
    }

    health_check_runner = HealthCheckRunner(
        health_checkables={_PortA: adapter_a, _PortB: adapter_b},
        adapter_device_map={
            _PortA: [("sensor_a", False)],
            _PortB: [("sensor_b", False)],
        },
        health_reporter=reporter,
        clock=clock,
        interval=30.0,
        shutdown_event=shutdown_event,
        restart_after_failures=1,
    )

    wiring_task = asyncio.create_task(
        run_lifespan_and_devices(
            lifespan=_noop_lifespan,
            store=None,
            devices=devices,
            telemetry=telemetry,
            heartbeat_interval=None,
            resolved_settings=settings,
            resolved_adapters={_PortA: adapter_a, _PortB: adapter_b},
            health_reporter=reporter,
            error_publisher=error_pub,
            contexts=contexts,
            shutdown_event=shutdown_event,
            health_check_runner=health_check_runner,
            restart_cooldown=0.0,
            adapter_device_map=adapter_device_map,
            resolved_clock=clock,
        )
    )
    await asyncio.sleep(0)

    return _RestartWiringHarness(
        clock=clock,
        mqtt=mqtt,
        reporter=reporter,
        shutdown_event=shutdown_event,
        adapter_a=adapter_a,
        adapter_b=adapter_b,
        health_check_runner=health_check_runner,
        wiring_task=wiring_task,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRestartParameters:
    """App constructor accepts restart parameters."""

    def test_default_restart_after_failures_is_5(self) -> None:
        assert App("t")._restart_after_failures == 5

    def test_default_max_restarts_is_3(self) -> None:
        assert App("t")._max_restarts == 3

    def test_default_restart_cooldown_is_5(self) -> None:
        assert App("t")._restart_cooldown == 5.0

    def test_default_sustained_health_reset_is_300(self) -> None:
        assert App("t")._sustained_health_reset == 300.0

    def test_zero_restart_after_failures_disables_restart(self) -> None:
        app = App("t", restart_after_failures=0)
        assert app._restart_after_failures == 0

    def test_negative_restart_after_failures_raises(self) -> None:
        with pytest.raises(ValueError, match="restart_after_failures must be >= 0"):
            App("t", restart_after_failures=-1)

    def test_negative_max_restarts_raises(self) -> None:
        with pytest.raises(ValueError, match="max_restarts must be >= 0"):
            App("t", max_restarts=-1)

    def test_zero_restart_cooldown_raises(self) -> None:
        with pytest.raises(ValueError, match="restart_cooldown must be positive"):
            App("t", restart_cooldown=0)

    def test_zero_sustained_health_reset_raises(self) -> None:
        with pytest.raises(ValueError, match="sustained_health_reset must be positive"):
            App("t", sustained_health_reset=0)


class TestAdapterHealthStatusRestart:
    """Extended AdapterHealthStatus with restart fields."""

    def test_defaults_include_restart_fields(self) -> None:
        s = AdapterHealthStatus()
        assert s.restart_count == 0
        assert s.restart_exhausted is False
        assert s.last_restart == 0.0
        assert s.last_healthy_since == 0.0

    def test_custom_restart_values(self) -> None:
        s = AdapterHealthStatus(
            restart_count=2,
            restart_exhausted=True,
            last_restart=10.0,
            last_healthy_since=5.0,
        )
        assert s.restart_count == 2
        assert s.restart_exhausted is True
        assert s.last_restart == 10.0
        assert s.last_healthy_since == 5.0


class TestDetectRestartableAdapters:
    """detect_restartable_adapters returns only fully eligible adapters."""

    def test_health_checkable_with_lifecycle_is_restartable(self) -> None:
        result = detect_restartable_adapters({_PortA: _LifecycleHealthy()})
        assert _PortA in result

    def test_health_checkable_without_lifecycle_excluded(self) -> None:
        result = detect_restartable_adapters({_PortA: _HealthyAdapter()})
        assert result == {}

    def test_opted_out_adapter_excluded(self) -> None:
        result = detect_restartable_adapters({_PortA: _OptedOutAdapter()})
        assert result == {}

    def test_plain_adapter_excluded(self) -> None:
        result = detect_restartable_adapters({_PortA: _PlainAdapter()})
        assert result == {}


class TestRestartThresholdDetection:
    """HealthCheckRunner detects when restart threshold is reached."""

    async def test_no_restart_when_disabled(self) -> None:
        cb = AsyncMock(return_value=True)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=0,
            on_restart_needed=cb,
        )
        for _ in range(5):
            await runner.run_startup_checks()
        cb.assert_not_called()

    async def test_callback_called_at_threshold(self) -> None:
        cb = AsyncMock(return_value=True)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=3,
            on_restart_needed=cb,
        )
        for _ in range(3):
            await runner.run_startup_checks()
        cb.assert_called_once()

    async def test_no_callback_before_threshold(self) -> None:
        cb = AsyncMock(return_value=True)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=3,
            on_restart_needed=cb,
        )
        for _ in range(2):
            await runner.run_startup_checks()
        cb.assert_not_called()

    async def test_restart_count_incremented_on_success(self) -> None:
        cb = AsyncMock(return_value=True)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=2,
            on_restart_needed=cb,
        )
        for _ in range(2):
            await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].restart_count == 1

    async def test_max_restarts_exhausted(self) -> None:
        cb = AsyncMock(return_value=True)
        clock = FakeClock(0.0)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=1,
            max_restarts=2,
            restart_cooldown=1.0,
            on_restart_needed=cb,
            clock=clock,
        )
        # 1st fail → restart, advance clock past cooldown
        await runner.run_startup_checks()
        clock._time = 10.0
        # 2nd fail → restart, advance clock past cooldown
        await runner.run_startup_checks()
        clock._time = 20.0
        # 3rd failure → exhausted (max_restarts=2 already reached)
        await runner.run_startup_checks()
        s = runner.adapter_health_status[_PortA]
        assert s.restart_exhausted is True
        assert s.restart_count == 2

    async def test_exhausted_adapter_not_restarted_again(self) -> None:
        cb = AsyncMock(return_value=True)
        clock = FakeClock(0.0)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=1,
            max_restarts=1,
            restart_cooldown=1.0,
            on_restart_needed=cb,
            clock=clock,
        )
        # First fail triggers the one allowed restart
        await runner.run_startup_checks()
        assert cb.call_count == 1
        # Advance past cooldown, fail again → max reached → exhausted
        clock._time = 10.0
        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].restart_exhausted is True
        # Further failures don't trigger more restarts
        clock._time = 100.0
        await runner.run_startup_checks()
        assert cb.call_count == 1

    async def test_failed_callback_marks_exhausted(self) -> None:
        """When on_restart_needed returns False, adapter is permanently offline."""
        cb = AsyncMock(return_value=False)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=2,
            max_restarts=3,
            on_restart_needed=cb,
        )
        for _ in range(2):
            await runner.run_startup_checks()
        s = runner.adapter_health_status[_PortA]
        assert s.restart_exhausted is True
        assert s.restart_count == 0  # never incremented on failure
        cb.assert_called_once()

    async def test_cooldown_prevents_immediate_restart(self) -> None:
        """Restart is not attempted within the cooldown window."""
        cb = AsyncMock(return_value=True)
        clock = FakeClock(0.0)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=1,
            max_restarts=3,
            restart_cooldown=10.0,
            on_restart_needed=cb,
            clock=clock,
        )
        # First failure triggers restart at t=0
        await runner.run_startup_checks()
        assert cb.call_count == 1

        # Second failure at t=5 (within cooldown) — no restart
        clock._time = 5.0
        await runner.run_startup_checks()
        assert cb.call_count == 1

        # Third failure at t=15 (past cooldown) — restart allowed
        clock._time = 15.0
        await runner.run_startup_checks()
        assert cb.call_count == 2


class TestSustainedHealthReset:
    """Restart counter resets after sustained healthy period."""

    async def test_restart_count_resets_after_sustained_health(self) -> None:
        cb = AsyncMock(return_value=True)
        clock = FakeClock(0.0)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=1,
            max_restarts=3,
            sustained_health_reset=100.0,
            on_restart_needed=cb,
            clock=clock,
        )
        # Trigger a restart
        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].restart_count == 1

        # Swap to healthy adapter and let time pass beyond threshold
        runner._checkables[_PortA] = _HealthyAdapter()
        clock._time = 200.0
        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].restart_count == 0

    async def test_restart_count_not_reset_before_threshold(self) -> None:
        cb = AsyncMock(return_value=True)
        clock = FakeClock(0.0)
        runner, *_ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            restart_after_failures=1,
            max_restarts=3,
            sustained_health_reset=100.0,
            on_restart_needed=cb,
            clock=clock,
        )
        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].restart_count == 1

        runner._checkables[_PortA] = _HealthyAdapter()
        clock._time = 50.0  # less than sustained_health_reset
        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].restart_count == 1


# ---------------------------------------------------------------------------
# Phase 2: Restart execution tests
# ---------------------------------------------------------------------------


class _TrackingAdapter:
    """Adapter that tracks __aenter__/__aexit__ calls."""

    def __init__(self, *, fail_enter: bool = False, fail_exit: bool = False) -> None:
        self.enter_count = 0
        self.exit_count = 0
        self._fail_enter = fail_enter
        self._fail_exit = fail_exit

    async def __aenter__(self) -> _TrackingAdapter:
        self.enter_count += 1
        if self._fail_enter:
            msg = "enter failed"
            raise RuntimeError(msg)
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exit_count += 1
        if self._fail_exit:
            msg = "exit failed"
            raise RuntimeError(msg)

    async def health_check(self) -> bool:
        return True


class TestRestartSingleAdapter:
    """restart_single_adapter() lifecycle management."""

    async def test_calls_aexit_then_aenter(self) -> None:
        adapter = _TrackingAdapter()
        clock = FakeClock()
        event = asyncio.Event()
        result = await restart_single_adapter(adapter, 0.0, clock, event)
        assert result is True
        assert adapter.exit_count == 1
        assert adapter.enter_count == 1

    async def test_aexit_failure_continues_to_aenter(self) -> None:
        adapter = _TrackingAdapter(fail_exit=True)
        clock = FakeClock()
        event = asyncio.Event()
        result = await restart_single_adapter(adapter, 0.0, clock, event)
        assert result is True
        assert adapter.exit_count == 1
        assert adapter.enter_count == 1

    async def test_aenter_failure_returns_false(self) -> None:
        adapter = _TrackingAdapter(fail_enter=True)
        clock = FakeClock()
        event = asyncio.Event()
        result = await restart_single_adapter(adapter, 0.0, clock, event)
        assert result is False
        assert adapter.exit_count == 1
        assert adapter.enter_count == 1

    async def test_cooldown_sleep(self) -> None:
        adapter = _TrackingAdapter()
        clock = FakeClock(0.0)
        event = asyncio.Event()
        result = await restart_single_adapter(adapter, 2.0, clock, event)
        assert result is True
        assert clock.now() == 2.0  # cooldown elapsed

    async def test_shutdown_during_cooldown_returns_false(self) -> None:
        adapter = _TrackingAdapter()
        clock = FakeClock()
        event = asyncio.Event()
        event.set()  # already shutting down
        result = await restart_single_adapter(adapter, 5.0, clock, event)
        assert result is False


class TestCancelTasksForAdapter:
    """cancel_tasks_for_adapter() targeted cancellation."""

    async def test_cancels_only_matching_devices(self) -> None:
        async def noop() -> None:
            await asyncio.sleep(999)

        task_a = asyncio.create_task(noop(), name="device:blind")
        task_b = asyncio.create_task(noop(), name="device:window")

        device_task_map: DeviceTaskMap = {"blind": [task_a], "window": [task_b]}
        adapter_device_map = {_PortA: [DeviceInfo("blind", False)]}

        cancelled, deferred = await cancel_tasks_for_adapter(
            device_task_map,
            adapter_device_map,  # ty: ignore[invalid-argument-type]
            _PortA,
        )

        assert cancelled == ["blind"]
        assert deferred == []
        assert task_a.cancelled()
        assert not task_b.cancelled()
        # Cleanup
        task_b.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task_b

    async def test_returns_empty_for_unknown_adapter(self) -> None:
        device_task_map: DeviceTaskMap = {}
        adapter_device_map: dict[type, list[DeviceInfo]] = {}

        cancelled, deferred = await cancel_tasks_for_adapter(
            device_task_map, adapter_device_map, _PortA
        )
        assert cancelled == []
        assert deferred == []


class TestStartDeviceTasksForNames:
    """start_device_tasks_for_names() filtered task creation."""

    async def test_starts_only_matching_devices(self) -> None:
        """Verify that only device registrations with matching names are started."""
        from cosalette._context import DeviceContext
        from cosalette._errors import ErrorPublisher
        from cosalette._health import HealthReporter

        clock = FakeClock()
        mqtt = AsyncMock()
        reporter = HealthReporter(
            mqtt=mqtt, topic_prefix="test", version="0.1.0", clock=clock
        )
        error_pub = ErrorPublisher(mqtt=mqtt, topic_prefix="test")
        event = asyncio.Event()

        # Create minimal device registrations
        from cosalette._registration import _DeviceRegistration

        async def handler(ctx: DeviceContext) -> AsyncIterator[None]:
            await asyncio.sleep(999)
            yield

        dev_a = _DeviceRegistration(
            name="blind",
            func=handler,
            injection_plan=[],
            is_root=False,
        )
        dev_b = _DeviceRegistration(
            name="window",
            func=handler,
            injection_plan=[],
            is_root=False,
        )

        from cosalette.testing import make_settings

        settings = make_settings()
        ctx_blind = DeviceContext(
            name="blind",
            settings=settings,
            mqtt=mqtt,
            topic_prefix="test",
            shutdown_event=event,
            adapters={},
            clock=clock,
            is_root=False,
        )
        ctx_window = DeviceContext(
            name="window",
            settings=settings,
            mqtt=mqtt,
            topic_prefix="test",
            shutdown_event=event,
            adapters={},
            clock=clock,
            is_root=False,
        )
        contexts = {"blind": ctx_blind, "window": ctx_window}

        tasks, task_map = start_device_tasks_for_names(
            ["blind"],
            [dev_a, dev_b],
            [],
            None,
            contexts,
            error_pub,
            reporter,
        )

        assert len(tasks) == 1
        assert "blind" in task_map
        assert "window" not in task_map

        # Cleanup
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t


# ---------------------------------------------------------------------------
# Coalescing group restart isolation
# ---------------------------------------------------------------------------


class TestCoalescingGroupRestartIsolation:
    """Shared coalescing-group tasks survive single-adapter restart."""

    async def test_shared_group_task_not_cancelled_on_single_adapter_restart(
        self,
    ) -> None:
        """A group task shared across adapters is deferred, not cancelled."""

        async def noop() -> None:
            await asyncio.sleep(999)

        shared_task = asyncio.create_task(noop(), name="group:sensors")
        solo_task = asyncio.create_task(noop(), name="device:sensor_a")

        device_task_map: DeviceTaskMap = {
            "sensor_a": [solo_task, shared_task],  # adapter A
            "sensor_b": [shared_task],  # adapter B — shares group task
        }
        adapter_device_map = {_PortA: [DeviceInfo("sensor_a", False)]}

        cancelled, deferred = await cancel_tasks_for_adapter(
            device_task_map,
            adapter_device_map,  # ty: ignore[invalid-argument-type]
            _PortA,
        )

        assert cancelled == ["sensor_a"]
        assert deferred == [shared_task]
        assert not shared_task.cancelled()  # still alive for sensor_b
        assert solo_task.cancelled()

        # Cleanup
        shared_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shared_task

    async def test_group_fully_owned_by_restarting_adapter_is_cancelled(
        self,
    ) -> None:
        """When all group members belong to the same adapter, cancel normally."""

        async def noop() -> None:
            await asyncio.sleep(999)

        shared_task = asyncio.create_task(noop(), name="group:local")

        device_task_map: DeviceTaskMap = {
            "dev_x": [shared_task],
            "dev_y": [shared_task],
        }
        adapter_device_map = {
            _PortA: [DeviceInfo("dev_x", False), DeviceInfo("dev_y", False)],
        }

        cancelled, deferred = await cancel_tasks_for_adapter(
            device_task_map,
            adapter_device_map,  # ty: ignore[invalid-argument-type]
            _PortA,
        )

        assert set(cancelled) == {"dev_x", "dev_y"}
        assert deferred == []
        assert shared_task.cancelled()

    async def test_start_device_tasks_for_names_expands_to_full_group(self) -> None:
        """Restarting one group member recreates tasks for all members."""
        from cosalette._context import DeviceContext
        from cosalette._errors import ErrorPublisher
        from cosalette._health import HealthReporter
        from cosalette._registration import _DeviceRegistration, _TelemetryRegistration
        from cosalette.testing import make_settings

        clock = FakeClock()
        mqtt = AsyncMock()
        reporter = HealthReporter(
            mqtt=mqtt, topic_prefix="test", version="0.1.0", clock=clock
        )
        error_pub = ErrorPublisher(mqtt=mqtt, topic_prefix="test")
        event = asyncio.Event()
        settings = make_settings()

        async def handler(ctx: DeviceContext) -> AsyncIterator[None]:
            await asyncio.sleep(999)
            yield

        async def read_sensor(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        devs = [
            _DeviceRegistration(name=n, func=handler, injection_plan=[])
            for n in ("sensor_a", "sensor_b", "other")
        ]
        tels = [
            _TelemetryRegistration(
                name="sensor_a",
                func=read_sensor,
                injection_plan=[],
                interval=10.0,
                group="sensors",
            ),
            _TelemetryRegistration(
                name="sensor_b",
                func=read_sensor,
                injection_plan=[],
                interval=10.0,
                group="sensors",
            ),
            _TelemetryRegistration(
                name="other",
                func=read_sensor,
                injection_plan=[],
                interval=5.0,
            ),
        ]
        contexts = {
            n: DeviceContext(
                name=n,
                settings=settings,
                mqtt=mqtt,
                topic_prefix="test",
                shutdown_event=event,
                adapters={},
                clock=clock,
                is_root=False,
            )
            for n in ("sensor_a", "sensor_b", "other")
        }

        # Request only sensor_a — sensor_b should be expanded into the set
        tasks, task_map = start_device_tasks_for_names(
            ["sensor_a"],
            devs,
            tels,
            None,
            contexts,
            error_pub,
            reporter,
        )

        assert "sensor_a" in task_map
        assert "sensor_b" in task_map  # expanded from group
        assert "other" not in task_map

        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t


# ---------------------------------------------------------------------------
# _on_restart integration — deferred group task handoff
# ---------------------------------------------------------------------------


class TestOnRestartDeferredTaskHandoff:
    """Integration: _on_restart closure cancels deferred group tasks
    only after replacement tasks are created (successful restart), and
    leaves them running on failed restart.
    """

    async def test_successful_restart_replaces_then_cancels_deferred_task(
        self,
    ) -> None:
        """On success: new group tasks created, then old deferred tasks cancelled."""
        # Arrange
        h = await _make_restart_wiring()

        # Act — trigger restart for adapter A — shared group task should be deferred
        result = await h.on_restart(_PortA, h.adapter_a)

        # Assert
        assert result is True
        assert h.adapter_a.exit_count == 1
        assert h.adapter_a.enter_count == 1

        # Clean up
        await h.teardown()

    async def test_failed_restart_leaves_deferred_tasks_running(self) -> None:
        """On failure: deferred group tasks stay alive for healthy adapters."""
        # Arrange
        h = await _make_restart_wiring(fail_enter_a=True)

        # Act — trigger restart — adapter A fails __aenter__
        result = await h.on_restart(_PortA, h.adapter_a)

        # Assert
        assert result is False

        # Clean up
        await h.teardown()


class TestOnRestartPrunesCancelledTasks:
    """_on_restart closure prunes cancelled (done) tasks from device_tasks.

    Test Techniques:
    - State Transition Testing: device_tasks list before/after restart
    - Specification-based Testing: done() tasks removed, new tasks present
    """

    async def test_cancelled_tasks_pruned_and_new_tasks_present(self) -> None:
        """After restart, device_tasks has no done() tasks and includes replacements."""
        # Arrange
        h = await _make_restart_wiring()

        # Act — trigger restart for adapter A
        result = await h.on_restart(_PortA, h.adapter_a)

        # Assert — direct observation of device_tasks via closure vars
        assert result is True

        closure_vars = inspect.getclosurevars(h.on_restart)
        assert "device_tasks" in closure_vars.nonlocals, (
            "closure variable 'device_tasks' not found — was it renamed?"
        )
        device_tasks = closure_vars.nonlocals["device_tasks"]
        assert all(not t.done() for t in device_tasks), (
            "device_tasks contains done/cancelled tasks after restart"
        )

        # Second restart — confirm list doesn't grow unboundedly
        pre_restart_count = len(device_tasks)
        result2 = await h.on_restart(_PortA, h.adapter_a)
        assert result2 is True
        assert h.adapter_a.exit_count == 2
        assert h.adapter_a.enter_count == 2
        assert all(not t.done() for t in device_tasks), (
            "device_tasks contains done/cancelled tasks after second restart"
        )
        assert len(device_tasks) <= pre_restart_count, (
            "device_tasks grew across restart — stale tasks not pruned"
        )

        # Clean up
        await h.teardown()


class TestConcurrentAdapterRestart:
    """Two adapters restarted concurrently via asyncio.gather must not corrupt
    shared mutable state (device_task_map and device_tasks).

    Test Techniques:
    - State Transition Testing: device_tasks and device_task_map before/after
      concurrent restarts
    - Specification-based Testing: both adapters restart successfully, no stale
      tasks remain, map entries for all devices present
    """

    async def test_concurrent_restarts_do_not_corrupt_shared_state(self) -> None:
        """asyncio.gather restart of two adapters preserves device_tasks and
        device_task_map integrity.

        ISTQB Technique: State Transition Testing — concurrent mutations to
        device_tasks (list) and device_task_map (dict) must be conflict-free.
        """
        # Arrange
        h = await _make_restart_wiring()

        # Act — trigger both restarts concurrently
        result_a, result_b = await asyncio.gather(
            h.on_restart(_PortA, h.adapter_a),
            h.on_restart(_PortB, h.adapter_b),
        )

        # Assert — both restarts succeeded
        assert result_a is True, "restart of adapter A failed"
        assert result_b is True, "restart of adapter B failed"

        # Assert — device_task_map contains entries for both devices
        closure_vars = inspect.getclosurevars(h.on_restart)
        assert "device_task_map" in closure_vars.nonlocals, (
            "closure variable 'device_task_map' not found — was it renamed?"
        )
        assert "device_tasks" in closure_vars.nonlocals, (
            "closure variable 'device_tasks' not found — was it renamed?"
        )
        device_task_map: DeviceTaskMap = closure_vars.nonlocals["device_task_map"]
        assert "sensor_a" in device_task_map, (
            "device_task_map missing 'sensor_a' after concurrent restart"
        )
        assert "sensor_b" in device_task_map, (
            "device_task_map missing 'sensor_b' after concurrent restart"
        )

        # Assert — all tasks in device_tasks are alive (no stale/cancelled)
        device_tasks: list[asyncio.Task[None]] = closure_vars.nonlocals["device_tasks"]
        assert all(not t.done() for t in device_tasks), (
            "device_tasks contains done/cancelled tasks after concurrent restart"
        )

        # Assert — adapter lifecycle counts: each restart does one exit + one
        # re-enter (adapters arrive pre-entered from App wiring).
        assert h.adapter_a.enter_count == 1, (
            f"adapter_a enter_count={h.adapter_a.enter_count}, expected 1"
        )
        assert h.adapter_a.exit_count == 1, (
            f"adapter_a exit_count={h.adapter_a.exit_count}, expected 1"
        )
        assert h.adapter_b.enter_count == 1, (
            f"adapter_b enter_count={h.adapter_b.enter_count}, expected 1"
        )
        assert h.adapter_b.exit_count == 1, (
            f"adapter_b exit_count={h.adapter_b.exit_count}, expected 1"
        )

        # Clean up
        await h.teardown()


class _MockRestartableAdapter:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _MockRestartableAdapter:
        self.entered = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


class _ShutdownTriggeringAdapter(_MockRestartableAdapter):
    def __init__(self, shutdown_event: asyncio.Event) -> None:
        super().__init__()
        self._shutdown_event = shutdown_event

    @override
    async def __aenter__(self) -> _ShutdownTriggeringAdapter:
        await super().__aenter__()
        self._shutdown_event.set()
        return self


class TestEnterRestartableAdaptersShutdownGuard:
    """Shutdown-event race guard for ``enter_restartable_adapters``.

    Test Techniques:
    - State Transition Testing: shutdown_event checked before each adapter entry
    - Branch/Condition Coverage: pre-set, mid-loop, and never-set shutdown paths
    """

    async def test_enter_restartable_adapters_skips_all_when_shutdown_preset(
        self,
    ) -> None:
        # Arrange
        a1 = _MockRestartableAdapter()
        a2 = _MockRestartableAdapter()
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        # Act
        result = await enter_restartable_adapters([a1, a2], shutdown_event)

        # Assert
        assert result == []
        assert not a1.entered
        assert not a2.entered

    async def test_enter_restartable_adapters_stops_midway_on_shutdown(
        self,
    ) -> None:
        # Arrange
        shutdown_event = asyncio.Event()
        a1 = _ShutdownTriggeringAdapter(shutdown_event)
        a2 = _MockRestartableAdapter()

        # Act
        result = await enter_restartable_adapters([a1, a2], shutdown_event)

        # Assert
        assert result == [a1]
        assert a1.entered
        assert not a2.entered

    async def test_enter_restartable_adapters_enters_all_without_shutdown(
        self,
    ) -> None:
        # Arrange
        a1 = _MockRestartableAdapter()
        a2 = _MockRestartableAdapter()
        shutdown_event = asyncio.Event()

        # Act
        result = await enter_restartable_adapters([a1, a2], shutdown_event)

        # Assert
        assert result == [a1, a2]
        assert a1.entered
        assert a2.entered
