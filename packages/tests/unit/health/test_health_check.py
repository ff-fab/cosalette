"""Tests for adapter health checks (COS-497.2, COS-497.3, COS-497.4).

Covers HealthCheckable protocol detection, AdapterHealthStatus value object,
adapter-to-device DI mapping, and the HealthCheckRunner periodic loop
(startup checks, availability toggling, timeout, log deduplication,
multi-adapter independence, timestamp tracking).

Techniques: protocol isinstance, frozen-dataclass immutability, AsyncMock,
FakeClock, caplog level assertions, asyncio task cancellation,
State Transition Testing (multi-failure recovery).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging as _logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from cosalette import App, HealthCheckable
from cosalette._context import DeviceContext
from cosalette._health import AdapterHealthStatus, HealthCheckRunner, HealthReporter
from cosalette._registration import _DeviceRegistration
from cosalette._settings import Settings
from cosalette._wiring import DeviceInfo, build_adapter_device_map
from cosalette._wiring._adapter_lifecycle import detect_health_checkable
from cosalette.testing._clock import FakeClock

pytestmark = pytest.mark.unit


class _HealthyAdapter:
    async def health_check(self) -> bool:
        return True


class _PlainAdapter:
    """Adapter without health_check — should not satisfy the protocol."""


class _PortA:
    """Dummy port type A."""


class _PortB:
    """Dummy port type B."""


class _PortC:
    """Dummy port type C."""


class TestHealthCheckableProtocol:
    def test_adapter_with_health_check_satisfies_protocol(self) -> None:
        assert isinstance(_HealthyAdapter(), HealthCheckable)

    def test_adapter_without_health_check_does_not_satisfy(self) -> None:
        assert not isinstance(_PlainAdapter(), HealthCheckable)


class TestDetectHealthCheckable:
    def test_detects_health_checkable_adapters(self) -> None:
        healthy = _HealthyAdapter()
        plain = _PlainAdapter()
        resolved = {_PortA: healthy, _PortB: plain}

        result = detect_health_checkable(resolved)  # ty: ignore[invalid-argument-type]

        assert result == {_PortA: healthy}

    def test_returns_empty_when_none_checkable(self) -> None:
        resolved = {_PortA: _PlainAdapter(), _PortB: _PlainAdapter()}

        result = detect_health_checkable(resolved)  # ty: ignore[invalid-argument-type]

        assert result == {}

    def test_returns_empty_for_empty_input(self) -> None:
        assert detect_health_checkable({}) == {}

    def test_multiple_health_checkable_adapters(self) -> None:
        h1 = _HealthyAdapter()
        h2 = _HealthyAdapter()
        resolved = {_PortA: h1, _PortB: _PlainAdapter(), _PortC: h2}

        result = detect_health_checkable(resolved)  # ty: ignore[invalid-argument-type]

        assert result == {_PortA: h1, _PortC: h2}


class TestHealthCheckIntervalParameter:
    def test_default_interval_is_30(self) -> None:
        app = App("test")
        assert app._health_check_interval == 30.0

    def test_custom_interval(self) -> None:
        app = App("test", health_check_interval=60.0)
        assert app._health_check_interval == 60.0

    def test_none_disables_health_check(self) -> None:
        app = App("test", health_check_interval=None)
        assert app._health_check_interval is None

    def test_zero_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="health_check_interval must be positive"):
            App("test", health_check_interval=0)

    def test_negative_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="health_check_interval must be positive"):
            App("test", health_check_interval=-5.0)


# ---------------------------------------------------------------------------
# Helpers for build_adapter_device_map tests
# ---------------------------------------------------------------------------


def _make_reg(
    name: str,
    *,
    injection_plan: list[tuple[str, type]] | None = None,
    is_root: bool = False,
) -> _DeviceRegistration:
    """Create a minimal _DeviceRegistration for testing."""

    async def _noop() -> AsyncIterator[None]:
        if False:
            yield

    return _DeviceRegistration(
        name=name,
        func=_noop,
        injection_plan=injection_plan or [],
        is_root=is_root,
    )


# ---------------------------------------------------------------------------
# AdapterHealthStatus
# ---------------------------------------------------------------------------


class TestAdapterHealthStatus:
    def test_defaults(self) -> None:
        status = AdapterHealthStatus()
        assert status.healthy is True
        assert status.consecutive_failures == 0
        assert status.last_check == 0.0

    def test_custom_values(self) -> None:
        status = AdapterHealthStatus(
            healthy=False, consecutive_failures=3, last_check=42.0
        )
        assert status.healthy is False
        assert status.consecutive_failures == 3
        assert status.last_check == 42.0

    def test_immutable(self) -> None:
        status = AdapterHealthStatus()
        with pytest.raises(AttributeError):
            status.healthy = False  # ty: ignore[invalid-assignment]


# ---------------------------------------------------------------------------
# build_adapter_device_map
# ---------------------------------------------------------------------------


class TestBuildAdapterDeviceMap:
    def test_maps_adapter_to_devices(self) -> None:
        """Devices that inject an adapter type are mapped to it."""
        regs = [
            _make_reg("blind", injection_plan=[("adapter", _PortA)]),
            _make_reg("window", injection_plan=[("adapter", _PortA)]),
        ]
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}

        result = build_adapter_device_map(regs, adapters)  # ty: ignore[invalid-argument-type]

        assert result == {
            _PortA: [DeviceInfo("blind", False), DeviceInfo("window", False)]
        }

    def test_ignores_known_injectable_types(self) -> None:
        """Framework-provided types are not treated as adapters."""
        regs = [
            _make_reg(
                "dev",
                injection_plan=[
                    ("ctx", DeviceContext),
                    ("settings", Settings),
                    ("logger", _logging.Logger),
                    ("adapter", _PortA),
                ],
            ),
        ]
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}

        result = build_adapter_device_map(regs, adapters)  # ty: ignore[invalid-argument-type]

        assert _PortA in result
        assert len(result) == 1  # only _PortA, not framework types

    def test_deduplicates_shared_names(self) -> None:
        """Telemetry + command sharing a name produce one mapping entry."""
        regs = [
            _make_reg("sensor", injection_plan=[("adapter", _PortA)]),
            _make_reg("sensor", injection_plan=[("adapter", _PortA)]),
        ]
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}

        result = build_adapter_device_map(regs, adapters)  # ty: ignore[invalid-argument-type]

        assert result[_PortA] == [DeviceInfo("sensor", False)]

    def test_multiple_adapters(self) -> None:
        """Multiple adapter types map to their respective devices."""
        regs = [
            _make_reg("blind", injection_plan=[("a", _PortA)]),
            _make_reg("sensor", injection_plan=[("b", _PortB)]),
        ]
        adapters: dict[type, object] = {
            _PortA: _HealthyAdapter(),
            _PortB: _PlainAdapter(),
        }

        result = build_adapter_device_map(regs, adapters)  # ty: ignore[invalid-argument-type]

        assert result[_PortA] == [DeviceInfo("blind", False)]
        assert result[_PortB] == [DeviceInfo("sensor", False)]

    def test_root_device_preserves_is_root(self) -> None:
        """Root devices carry is_root=True in the mapping."""
        regs = [
            _make_reg("app", injection_plan=[("adapter", _PortA)], is_root=True),
        ]
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}

        result = build_adapter_device_map(regs, adapters)  # ty: ignore[invalid-argument-type]

        assert result[_PortA] == [DeviceInfo("app", True)]

    def test_empty_registrations(self) -> None:
        adapters: dict[type, object] = {_PortA: _HealthyAdapter()}
        result = build_adapter_device_map([], adapters)
        assert result == {_PortA: []}

    def test_empty_adapters(self) -> None:
        regs = [_make_reg("dev", injection_plan=[("adapter", _PortA)])]
        result = build_adapter_device_map(regs, {})  # ty: ignore[invalid-argument-type]
        assert result == {}


# ---------------------------------------------------------------------------
# HealthCheckRunner
# ---------------------------------------------------------------------------


class _UnhealthyAdapter:
    async def health_check(self) -> bool:
        return False


class _FailingAdapter:
    async def health_check(self) -> bool:
        raise ConnectionError("adapter wedged")


class _SlowAdapter:
    async def health_check(self) -> bool:
        await asyncio.sleep(999)
        return True


def _make_runner(
    *,
    adapters: dict[type, object] | None = None,
    device_map: dict[type, list[tuple[str, bool]]] | None = None,
    interval: float = 10.0,
    clock: FakeClock | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> tuple[HealthCheckRunner, HealthReporter, FakeClock, asyncio.Event]:
    """Build a HealthCheckRunner with test doubles."""
    clock = clock or FakeClock()
    event = shutdown_event or asyncio.Event()
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
    )
    return runner, reporter, clock, event


class TestHealthCheckRunnerProbe:
    """Tests for the _probe method via run_startup_checks."""

    async def test_healthy_adapter_stays_online(self) -> None:
        runner, reporter, clock, _ = _make_runner()
        await runner.run_startup_checks()

        status = runner.adapter_health_status[_PortA]
        assert status.healthy is True
        assert status.consecutive_failures == 0

    async def test_unhealthy_adapter_publishes_offline(self) -> None:
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
        )
        await runner.run_startup_checks()

        status = runner.adapter_health_status[_PortA]
        assert status.healthy is False
        assert status.consecutive_failures == 1
        calls = reporter.mqtt.publish.call_args_list  # ty: ignore[unresolved-attribute]
        offline_calls = [c for c in calls if c.args[1] == "offline"]
        assert len(offline_calls) == 1

    async def test_exception_treated_as_failure(self) -> None:
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _FailingAdapter()},
        )
        await runner.run_startup_checks()

        status = runner.adapter_health_status[_PortA]
        assert status.healthy is False
        assert status.consecutive_failures == 1

    async def test_timeout_treated_as_failure(self) -> None:
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _SlowAdapter()},
            interval=0.01,
        )
        await runner.run_startup_checks()

        status = runner.adapter_health_status[_PortA]
        assert status.healthy is False

    async def test_recovery_publishes_online(self) -> None:
        """After failure, a healthy check restores availability."""
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
        )
        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].healthy is False

        runner._checkables[_PortA] = _HealthyAdapter()
        await runner.run_startup_checks()

        status = runner.adapter_health_status[_PortA]
        assert status.healthy is True
        assert status.consecutive_failures == 0

    async def test_consecutive_failures_increment(self) -> None:
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
        )
        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].consecutive_failures == 1

        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].consecutive_failures == 2

        await runner.run_startup_checks()
        assert runner.adapter_health_status[_PortA].consecutive_failures == 3

    async def test_multiple_devices_go_offline(self) -> None:
        """All devices mapped to a failing adapter go offline."""
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            device_map={_PortA: [("blind", False), ("window", False)]},
        )
        await runner.run_startup_checks()

        calls = reporter.mqtt.publish.call_args_list  # ty: ignore[unresolved-attribute]
        offline_calls = [c for c in calls if c.args[1] == "offline"]
        assert len(offline_calls) == 2

    async def test_root_device_uses_root_topic(self) -> None:
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
            device_map={_PortA: [("app", True)]},
        )
        await runner.run_startup_checks()

        calls = reporter.mqtt.publish.call_args_list  # ty: ignore[unresolved-attribute]
        offline_calls = [c for c in calls if c.args[1] == "offline"]
        assert any(c.args[0] == "test/availability" for c in offline_calls)

    async def test_multiple_adapters_probed_independently(self) -> None:
        """Each adapter's health state is tracked independently.

        Technique: State Transition Testing — PortA fails while PortB stays
        healthy; verify each has its own AdapterHealthStatus.
        """
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter(), _PortB: _HealthyAdapter()},
            device_map={
                _PortA: [("blind", False)],
                _PortB: [("sensor", False)],
            },
        )

        await runner.run_startup_checks()

        status_a = runner.adapter_health_status[_PortA]
        status_b = runner.adapter_health_status[_PortB]
        assert status_a.healthy is False
        assert status_a.consecutive_failures == 1
        assert status_b.healthy is True
        assert status_b.consecutive_failures == 0

    async def test_last_check_set_from_clock(self) -> None:
        """AdapterHealthStatus.last_check reflects clock.now() at probe time.

        Technique: Specification-based — verify the timestamp contract.
        """
        clock = FakeClock(42.0)
        runner, _, _, _ = _make_runner(clock=clock)

        await runner.run_startup_checks()

        status = runner.adapter_health_status[_PortA]
        assert status.last_check == 42.0

    async def test_recovery_resets_after_multiple_failures(self) -> None:
        """Consecutive failures reset to 0 when adapter recovers.

        Technique: State Transition Testing — 3 failures → recovery →
        verify counter is zeroed.
        """
        runner, reporter, clock, _ = _make_runner(
            adapters={_PortA: _UnhealthyAdapter()},
        )

        for _ in range(3):
            await runner.run_startup_checks()

        assert runner.adapter_health_status[_PortA].consecutive_failures == 3

        runner._checkables[_PortA] = _HealthyAdapter()
        await runner.run_startup_checks()

        status = runner.adapter_health_status[_PortA]
        assert status.healthy is True
        assert status.consecutive_failures == 0


class TestHealthCheckRunnerLoop:
    """Tests for the periodic run_loop."""

    async def test_loop_stops_on_shutdown(self) -> None:
        runner, reporter, clock, event = _make_runner()
        event.set()
        await runner.run_loop()  # should return immediately

    async def test_loop_checks_adapter_each_iteration(self) -> None:
        call_count = 0

        class _CountingAdapter:
            async def health_check(self) -> bool:
                nonlocal call_count
                call_count += 1
                return True

        runner, reporter, clock, event = _make_runner(
            adapters={_PortA: _CountingAdapter()},
        )

        async def stop_after_probes() -> None:
            # Yield enough ticks for _shutdown_aware_sleep + _probe
            for _ in range(20):
                await asyncio.sleep(0)
            event.set()

        task = asyncio.create_task(runner.run_loop())
        await stop_after_probes()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert call_count >= 1


class TestHealthCheckRunnerLogging:
    """Log deduplication: first failure WARNING, consecutive DEBUG, recovery INFO."""

    async def test_first_failure_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        runner, _, _, _ = _make_runner(adapters={_PortA: _UnhealthyAdapter()})
        with caplog.at_level(_logging.DEBUG, logger="cosalette._health"):
            await runner.run_startup_checks()

        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert len(warnings) == 1
        assert "health check failed" in warnings[0].message

    async def test_consecutive_failure_logs_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        runner, _, _, _ = _make_runner(adapters={_PortA: _UnhealthyAdapter()})
        await runner.run_startup_checks()  # first failure

        with caplog.at_level(_logging.DEBUG, logger="cosalette._health"):
            caplog.clear()
            await runner.run_startup_checks()  # second failure

        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        debugs = [r for r in caplog.records if r.levelno == _logging.DEBUG]
        assert len(warnings) == 0
        assert any("health check failed" in r.message for r in debugs)

    async def test_recovery_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        runner, _, _, _ = _make_runner(adapters={_PortA: _UnhealthyAdapter()})
        await runner.run_startup_checks()  # fail

        runner._checkables[_PortA] = _HealthyAdapter()
        with caplog.at_level(_logging.DEBUG, logger="cosalette._health"):
            caplog.clear()
            await runner.run_startup_checks()  # recover

        infos = [r for r in caplog.records if r.levelno == _logging.INFO]
        assert any("recovered" in r.message for r in infos)
