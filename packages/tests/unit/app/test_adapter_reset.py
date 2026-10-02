"""Tests for the reset() restart protocol and restart_on_stale (ADR-084).

A health-checkable adapter without an async context manager restarts by
``reset()``; only restartable adapters are restarted; a telemetry entity
that goes stale can request a restart of the adapters it depends on.

Test Techniques Used:
    - Decision Table Testing: restart eligibility (HealthCheckable x opt-out
      x context manager x reset()) and which restart protocol runs
    - State Transition Testing: stale episodes, unhealthy episodes and the
      restart budget (fresh -> stale -> fresh -> stale)
    - Boundary Value Analysis: restart_count at max_restarts
    - Error Guessing: raising and synchronous reset(), shutdown during the
      cooldown, a restart request racing a probe round
    - Specification-based Testing: App parameter validation, log wording
    - Mock-based Isolation: FakeClock, ManualClock, MockMqttClient
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from cosalette import App
from cosalette._health import HealthCheckRunner, HealthReporter
from cosalette._wiring import DeviceInfo
from cosalette._wiring._adapter_lifecycle import (
    detect_restartable_adapters,
    lifecycle_restartable,
    restart_single_adapter,
    uses_reset_restart,
)
from cosalette._wiring._task_lifecycle import stale_restart_callback
from cosalette.testing import (
    AppHarness,
    FakeClock,
    ManualClock,
    MockMqttClient,
    make_settings,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class _PortA:
    """Dummy port type A."""


class _PortB:
    """Dummy port type B."""


class _ResetAdapter:
    """Health-checkable with an async reset() and no context manager."""

    def __init__(self, *, fail: bool = False, healthy: bool = True) -> None:
        self.resets = 0
        self.fail = fail
        self.healthy = healthy

    async def health_check(self) -> bool:
        return self.healthy

    async def reset(self) -> None:
        self.resets += 1
        if self.fail:
            msg = "reset failed"
            raise RuntimeError(msg)


class _SyncResetAdapter:
    def __init__(self) -> None:
        self.resets = 0

    async def health_check(self) -> bool:
        return True

    def reset(self) -> None:
        self.resets += 1


class _BothAdapter(_ResetAdapter):
    """Context manager and reset(): the context manager wins."""

    def __init__(self) -> None:
        super().__init__()
        self.enters = 0
        self.exits = 0

    async def __aenter__(self) -> _BothAdapter:
        self.enters += 1
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exits += 1


class _OptedOutReset(_ResetAdapter):
    restartable = False


class _PlainHealthy:
    async def health_check(self) -> bool:
        return True


class _PlainUnhealthy:
    async def health_check(self) -> bool:
        return False


def _make_runner(
    adapters: dict[type, object],
    *,
    restartable: frozenset[type] | None = None,
    restart_after_failures: int = 2,
    max_restarts: int = 3,
    on_restart_needed: AsyncMock | None = None,
) -> HealthCheckRunner:
    clock = FakeClock()
    reporter = HealthReporter(
        mqtt=AsyncMock(), topic_prefix="test", version="0.1.0", clock=clock
    )
    return HealthCheckRunner(
        health_checkables=adapters,
        adapter_device_map={t: [("dev", False)] for t in adapters},
        health_reporter=reporter,
        clock=clock,
        interval=10.0,
        shutdown_event=asyncio.Event(),
        restart_after_failures=restart_after_failures,
        max_restarts=max_restarts,
        on_restart_needed=on_restart_needed,
        restartable=restartable,
    )


# ---------------------------------------------------------------------------
# Eligibility and protocol choice
# ---------------------------------------------------------------------------


class TestRestartEligibility:
    """detect_restartable_adapters accepts reset() as a second protocol."""

    @pytest.mark.parametrize(
        ("adapter", "restartable"),
        [
            (_ResetAdapter(), True),
            (_SyncResetAdapter(), True),
            (_BothAdapter(), True),
            (_OptedOutReset(), False),
            (_PlainHealthy(), False),
        ],
        ids=["reset", "sync-reset", "both", "opted-out", "neither"],
    )
    def test_decision_table(self, adapter: object, restartable: bool) -> None:
        found = detect_restartable_adapters({_PortA: adapter})

        assert (_PortA in found) is restartable

    def test_neither_protocol_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            detect_restartable_adapters({_PortA: _PlainHealthy()})

        assert "no async context manager or reset()" in caplog.text

    def test_context_manager_takes_precedence(self) -> None:
        assert uses_reset_restart(_ResetAdapter()) is True
        assert uses_reset_restart(_BothAdapter()) is False

    def test_only_context_managers_are_entered(self) -> None:
        reset_only, both = _ResetAdapter(), _BothAdapter()

        assert lifecycle_restartable([reset_only, both]) == [both]


class TestResetRestart:
    """restart_single_adapter() awaits reset() for a reset-only adapter."""

    async def test_cooldown_then_reset(self) -> None:
        adapter = _ResetAdapter()
        clock = FakeClock(0.0)

        ok = await restart_single_adapter(adapter, 2.0, clock, asyncio.Event())

        assert ok is True
        assert adapter.resets == 1
        assert clock.now() == 2.0

    async def test_synchronous_reset(self) -> None:
        adapter = _SyncResetAdapter()

        ok = await restart_single_adapter(adapter, 0.0, FakeClock(), asyncio.Event())

        assert ok is True
        assert adapter.resets == 1

    async def test_raising_reset_is_a_failed_restart(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = _ResetAdapter(fail=True)

        with caplog.at_level(logging.CRITICAL):
            ok = await restart_single_adapter(
                adapter, 0.0, FakeClock(), asyncio.Event()
            )

        assert ok is False
        assert "reset() failed during restart" in caplog.text

    async def test_shutdown_skips_reset(self) -> None:
        adapter = _ResetAdapter()
        event = asyncio.Event()
        event.set()

        ok = await restart_single_adapter(adapter, 5.0, FakeClock(), event)

        assert ok is False
        assert adapter.resets == 0

    async def test_context_manager_restart_never_calls_reset(self) -> None:
        adapter = _BothAdapter()

        ok = await restart_single_adapter(adapter, 0.0, FakeClock(), asyncio.Event())

        assert ok is True
        assert (adapter.exits, adapter.enters, adapter.resets) == (1, 1, 0)


# ---------------------------------------------------------------------------
# Runner: only eligible adapters restart
# ---------------------------------------------------------------------------


class TestRunnerEligibility:
    """The runner restarts only adapter types it was told are restartable."""

    async def test_non_restartable_adapter_is_not_restarted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange
        cb = AsyncMock(return_value=True)
        runner = _make_runner(
            {_PortA: _PlainUnhealthy()}, restartable=frozenset(), on_restart_needed=cb
        )

        # Act
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                await runner.run_startup_checks()

        # Assert: one WARNING for the episode, no restart, no budget used
        cb.assert_not_called()
        assert caplog.text.count("is not restartable") == 1
        assert runner.adapter_health_status[_PortA].restart_count == 0

    async def test_warns_again_in_a_new_unhealthy_episode(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = _ResetAdapter(healthy=False)
        runner = _make_runner({_PortA: adapter}, restartable=frozenset())

        with caplog.at_level(logging.WARNING):
            for healthy in (False, False, True, False, False):
                adapter.healthy = healthy
                await runner.run_startup_checks()

        assert caplog.text.count("is not restartable") == 2

    async def test_default_restarts_every_adapter(self) -> None:
        cb = AsyncMock(return_value=True)
        runner = _make_runner({_PortA: _PlainUnhealthy()}, on_restart_needed=cb)

        for _ in range(2):
            await runner.run_startup_checks()

        cb.assert_called_once()


# ---------------------------------------------------------------------------
# Runner: request_restart
# ---------------------------------------------------------------------------


class TestRequestRestart:
    """request_restart() skips the threshold but uses the budget."""

    async def test_restarts_a_healthy_adapter_and_counts(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Arrange
        cb = AsyncMock(return_value=True)
        runner = _make_runner({_PortA: _ResetAdapter()}, on_restart_needed=cb)

        # Act
        with caplog.at_level(logging.WARNING):
            attempted = await runner.request_restart(_PortA, "stale telemetry 'dev'")

        # Assert
        assert attempted is True
        cb.assert_awaited_once()
        assert runner.adapter_health_status[_PortA].restart_count == 1
        assert (
            "Restarting adapter _PortA after stale telemetry 'dev' (restart 1/3)"
            in caplog.text
        )

    async def test_failed_restart_counts_against_the_budget(self) -> None:
        cb = AsyncMock(return_value=False)
        runner = _make_runner({_PortA: _ResetAdapter()}, on_restart_needed=cb)

        await runner.request_restart(_PortA, "stale")

        status = runner.adapter_health_status[_PortA]
        assert (status.restart_count, status.healthy) == (1, False)
        assert runner._health_reporter.is_unavailable("dev")

    @pytest.mark.parametrize(
        ("restartable", "known"),
        [(frozenset(), True), (None, False)],
        ids=["not-restartable", "unknown-type"],
    )
    async def test_ignored_for_ineligible_adapter(
        self, restartable: frozenset[type] | None, known: bool
    ) -> None:
        cb = AsyncMock(return_value=True)
        runner = _make_runner(
            {_PortA: _ResetAdapter()}, restartable=restartable, on_restart_needed=cb
        )

        attempted = await runner.request_restart(_PortA if known else _PortB, "x")

        assert attempted is False
        cb.assert_not_called()

    async def test_budget_boundary_exhausts_then_ignores(self) -> None:
        # Arrange: max_restarts=1, so the second request finds the budget used
        cb = AsyncMock(return_value=True)
        runner = _make_runner(
            {_PortA: _ResetAdapter()}, max_restarts=1, on_restart_needed=cb
        )

        # Act
        first = await runner.request_restart(_PortA, "stale")
        second = await runner.request_restart(_PortA, "stale")
        third = await runner.request_restart(_PortA, "stale")

        # Assert
        assert (first, second, third) == (True, True, False)
        assert cb.await_count == 1
        assert runner.adapter_health_status[_PortA].restart_exhausted is True

    async def test_waits_for_a_running_probe_round(self) -> None:
        # Arrange: hold the lock the way a probe round does
        cb = AsyncMock(return_value=True)
        runner = _make_runner({_PortA: _ResetAdapter()}, on_restart_needed=cb)
        await runner._lock.acquire()

        # Act
        request = asyncio.create_task(runner.request_restart(_PortA, "stale"))
        await asyncio.sleep(0)
        called_while_locked = cb.await_count
        runner._lock.release()
        await request

        # Assert
        assert called_while_locked == 0
        cb.assert_awaited_once()


# ---------------------------------------------------------------------------
# Freshness -> restart
# ---------------------------------------------------------------------------


class TestCheckFreshnessReturnsNewlyStale:
    """check_freshness() reports each stale transition once."""

    async def test_transition_only(self) -> None:
        clock = FakeClock()
        clock._time = 0.0
        reporter = HealthReporter(
            mqtt=MockMqttClient(), topic_prefix="p", version="1", clock=clock
        )
        reporter.track_freshness("a", 10.0)

        clock._time = 5.0
        before = await reporter.check_freshness()
        clock._time = 10.0
        first = await reporter.check_freshness()
        clock._time = 20.0
        again = await reporter.check_freshness()

        assert (before, first, again) == ([], ["a"], [])


class TestStaleRestartCallback:
    """stale_restart_callback() maps stale entities to their adapters."""

    def test_off_by_default(self) -> None:
        runner = _make_runner({_PortA: _ResetAdapter()})

        assert stale_restart_callback(False, runner, {}) is None

    def test_needs_a_runner(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            callback = stale_restart_callback(
                True, None, {_PortA: [DeviceInfo("a", False)]}
            )

        assert callback is None
        assert "restart_on_stale has no effect" in caplog.text

    async def test_one_request_per_adapter(self) -> None:
        # Arrange: two stale entities share _PortA; _PortB is not involved
        runner = AsyncMock(spec=HealthCheckRunner)
        callback = stale_restart_callback(
            True,
            runner,
            {
                _PortA: [DeviceInfo("a", False), DeviceInfo("b", False)],
                _PortB: [DeviceInfo("c", False)],
            },
        )
        assert callback is not None

        # Act
        await callback(["a", "b"])

        # Assert
        runner.request_restart.assert_awaited_once_with(_PortA, "stale telemetry 'a'")


class TestRestartOnStaleParameter:
    def test_default_is_false(self) -> None:
        assert App("x")._restart_on_stale is False

    def test_rejects_non_bool(self) -> None:
        with pytest.raises(TypeError, match="restart_on_stale must be a bool"):
            App("x", restart_on_stale=1)  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


class _SensorPort:
    """Port the telemetry handler depends on."""


class _StuckClient:
    """Health check passes, reads fail until reset() (the ADR-084 case)."""

    def __init__(self) -> None:
        self.resets = 0
        self.stuck = True

    async def health_check(self) -> bool:
        return True

    async def reset(self) -> None:
        self.resets += 1
        self.stuck = False

    async def read(self) -> float:
        if self.stuck:
            msg = "no data"
            raise TimeoutError(msg)
        return 1.0


def _harness(app: App, clock: ManualClock) -> AppHarness:
    return AppHarness(
        app=app,
        mqtt=MockMqttClient(),
        clock=clock,
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
    )


class TestRestartOnStaleEndToEnd:
    """A stale entity resets the adapter behind it and comes back online."""

    @pytest.mark.parametrize("restart_on_stale", [True, False])
    async def test_stale_entity_resets_its_adapter(
        self, restart_on_stale: bool
    ) -> None:
        # Arrange
        client = _StuckClient()
        app = App(
            "testapp",
            health_check_interval=10.0,
            restart_cooldown=1.0,
            restart_on_stale=restart_on_stale,
        )
        app.adapter(_SensorPort, lambda: client)

        @app.telemetry("sensor", interval=10, stale_after=20.0)
        async def sensor(port: _SensorPort) -> dict[str, float]:
            return {"value": await port.read()}  # ty: ignore[unresolved-attribute]

        clock = ManualClock()
        harness = _harness(app, clock)
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count("testapp/sensor/availability", 1)

        # Act
        for _ in range(8):
            await harness.advance_time(10.0)

        # Assert
        availability = [
            p for p, *_ in harness.messages_for("testapp/sensor/availability")
        ]
        harness.trigger_shutdown()
        await task
        if restart_on_stale:
            assert client.resets == 1
            assert availability[-1] == "online"
        else:
            assert client.resets == 0
            assert availability[-1] == "offline"

    async def test_same_name_command_does_not_restart_its_adapter(self) -> None:
        telemetry_client = _StuckClient()
        command_client = _ResetAdapter()
        app = App(
            "testapp",
            health_check_interval=10.0,
            restart_cooldown=1.0,
            restart_on_stale=True,
        )
        app.adapter(_SensorPort, lambda: telemetry_client)
        app.adapter(_PortB, lambda: command_client)

        @app.telemetry("sensor", interval=10, stale_after=20.0)
        async def sensor(port: _SensorPort) -> dict[str, float]:
            return {"value": await port.read()}  # ty: ignore[unresolved-attribute]

        @app.command("sensor")
        async def sensor_command(topic: str, payload: str, port: _PortB) -> None:
            del topic, payload, port

        harness = _harness(app, ManualClock())
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count("testapp/sensor/availability", 1)

        for _ in range(8):
            await harness.advance_time(10.0)

        harness.trigger_shutdown()
        await task

        assert telemetry_client.resets == 1
        assert command_client.resets == 0

    async def test_unhealthy_plain_adapter_is_never_restarted(self) -> None:
        # Arrange: health-checkable without a restart protocol (ADR-029 D5).
        # Before ADR-084 the runner "restarted" it: exit and enter failed and
        # the telemetry task was cancelled and never re-created.
        client = _PlainUnhealthy()
        app = App("testapp", health_check_interval=10.0, restart_after_failures=1)
        app.adapter(_SensorPort, lambda: client)

        @app.telemetry("sensor", interval=10)
        async def sensor(port: _SensorPort) -> dict[str, int]:
            return {"value": 1}

        harness = _harness(app, ManualClock())
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count("testapp/sensor/state", 1)

        # Act
        for _ in range(4):
            await harness.advance_time(10.0)
        polls = len(harness.messages_for("testapp/sensor/state"))
        harness.trigger_shutdown()
        await task

        # Assert: the telemetry task kept polling through every probe round
        assert polls == 5
