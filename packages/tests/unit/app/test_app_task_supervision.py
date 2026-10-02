"""App-level tests for task supervision (ADR-081).

Covers: the ``on_task_failure`` / ``task_max_restarts`` /
``task_restart_window`` parameters, escalation out of ``App._run_async``
as :class:`TaskSupervisionError`, the one error payload and the
``supervisor`` availability mark end to end, recovery after a restart, the
deferred first cycle of a re-created telemetry task or group, and
framework-internal loop escalation.

Test Techniques Used:
    - Equivalence Partitioning: valid and invalid constructor values.
    - Boundary Value Analysis: ``task_max_restarts`` 0 and -1, window 0.
    - State Transition Testing: running -> failed -> restarted -> recovered.
    - Specification-based Testing: payload ``details``, availability topics,
      raised exception attributes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from cosalette import TaskSupervisionError
from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette.testing import (
    AppHarness,
    FakeClock,
    ManualClock,
    MockMqttClient,
    make_settings,
)

pytestmark = pytest.mark.unit


def _harness(clock: Any = None, **app_kwargs: Any) -> AppHarness:
    """AppHarness whose App takes the supervision parameters."""
    return AppHarness(
        app=App(name="testapp", version="1.0.0", store=None, **app_kwargs),
        mqtt=MockMqttClient(),
        clock=clock if clock is not None else FakeClock(),
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
    )


def _task_named(name: str) -> asyncio.Task[Any]:
    (task,) = [t for t in asyncio.all_tasks() if t.get_name() == name]
    return task


def _payloads(harness: AppHarness, topic: str) -> list[dict[str, Any]]:
    return [json.loads(payload) for payload, _, _ in harness.messages_for(topic)]


def _availability(harness: AppHarness, entity: str) -> list[str]:
    return [p for p, _, _ in harness.messages_for(f"testapp/{entity}/availability")]


def _recovered(harness: AppHarness, entity: str) -> bool:
    """Whether *entity* went offline and is online again (before shutdown)."""
    availability = _availability(harness, entity)
    return "offline" in availability and availability[-1] == "online"


# ---------------------------------------------------------------------------
# Constructor parameters
# ---------------------------------------------------------------------------


class TestSupervisionParameters:
    """Technique: Equivalence Partitioning and Boundary Value Analysis."""

    def test_defaults(self) -> None:
        """Defaults: restart, 3 restarts, 300 s window."""
        # Act
        app = App(name="testapp", version="1.0.0", store=None)

        # Assert
        assert app._on_task_failure == "restart"  # noqa: SLF001
        assert app._task_max_restarts == 3  # noqa: SLF001
        assert app._task_restart_window == 300.0  # noqa: SLF001

    @pytest.mark.parametrize("policy", ["restart", "exit", "ignore"])
    def test_accepts_every_policy(self, policy: str) -> None:
        """Each documented policy is accepted."""
        # Act
        app = App(name="testapp", version="1.0.0", on_task_failure=policy)  # ty: ignore[invalid-argument-type]

        # Assert
        assert app._on_task_failure == policy  # noqa: SLF001

    def test_rejects_unknown_policy(self) -> None:
        """An unknown policy fails at construction."""
        # Act / Assert
        with pytest.raises(ValueError, match="on_task_failure"):
            App(name="testapp", version="1.0.0", on_task_failure="crash")  # ty: ignore[invalid-argument-type]

    def test_zero_restarts_is_valid(self) -> None:
        """task_max_restarts=0 is the lower bound (exit on first failure)."""
        # Act
        app = App(name="testapp", version="1.0.0", task_max_restarts=0)

        # Assert
        assert app._task_max_restarts == 0  # noqa: SLF001

    @pytest.mark.parametrize("value", [-1, True, 1.5, "3"])
    def test_rejects_invalid_max_restarts(self, value: object) -> None:
        """Negative, bool and non-int budgets are rejected."""
        # Act / Assert
        with pytest.raises(ValueError, match="task_max_restarts"):
            App(name="testapp", version="1.0.0", task_max_restarts=value)  # ty: ignore[invalid-argument-type]

    @pytest.mark.parametrize("value", [0, -5.0])
    def test_rejects_non_positive_window(self, value: float) -> None:
        """The restart window must be positive."""
        # Act / Assert
        with pytest.raises(ValueError, match="task_restart_window"):
            App(name="testapp", version="1.0.0", task_restart_window=value)


# ---------------------------------------------------------------------------
# Escalation and reporting end to end
# ---------------------------------------------------------------------------


class TestEscalation:
    """Technique: Specification-based Testing — ADR-081 sections 2-4."""

    async def test_exit_policy_raises_task_supervision_error(self) -> None:
        """A crashing device under 'exit' ends the run with the error."""
        # Arrange
        harness = _harness(on_task_failure="exit")

        @harness.app.device("bad")
        async def bad(ctx: DeviceContext) -> AsyncIterator[None]:
            msg = "sensor exploded"
            raise RuntimeError(msg)
            yield  # noqa: PGH004

        # Act
        with pytest.raises(TaskSupervisionError) as caught:
            await asyncio.wait_for(harness.run(), timeout=5.0)

        # Assert
        assert caught.value.task_name == "device:bad"
        assert caught.value.internal is False
        assert isinstance(caught.value.__cause__, RuntimeError)
        payloads = _payloads(harness, "testapp/bad/error")
        assert len(payloads) == 1
        assert payloads[0]["details"] == {"task_failure": True, "task": "device:bad"}
        assert "offline" in _availability(harness, "bad")

    async def test_budget_exhaustion_raises_after_restarts(self) -> None:
        """Under 'restart', the run ends once the budget is spent."""
        # Arrange
        harness = _harness(task_max_restarts=2, task_restart_window=1e9)
        attempts = 0

        @harness.app.device("bad")
        async def bad(ctx: DeviceContext) -> AsyncIterator[None]:
            nonlocal attempts
            attempts += 1
            msg = "always"
            raise RuntimeError(msg)
            yield  # noqa: PGH004

        # Act
        with pytest.raises(TaskSupervisionError) as caught:
            await asyncio.wait_for(harness.run(), timeout=5.0)

        # Assert
        assert attempts == 3
        assert caught.value.restart_count == 2
        assert len(_payloads(harness, "testapp/bad/error")) == 3

    async def test_periodic_failure_payload_has_no_device(self) -> None:
        """A dead periodic task's payload goes to the app error topic only.

        The periodic runner isolates handler errors per cycle, so the task
        dies only from outside: an external cancel.
        """
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, on_task_failure="exit")
        harness.run_periodic = True
        ran = asyncio.Event()

        @harness.app.periodic("tick", interval=60)
        async def tick() -> None:
            ran.set()

        run = asyncio.create_task(harness.run())
        await clock.settle(
            until=lambda: any(
                t.get_name() == "periodic:tick" for t in asyncio.all_tasks()
            )
        )

        # Act
        _task_named("periodic:tick").cancel()

        # Assert
        with pytest.raises(TaskSupervisionError) as caught:
            await asyncio.wait_for(run, timeout=5.0)
        assert caught.value.task_name == "periodic:tick"
        payloads = _payloads(harness, "testapp/error")
        task_payloads = [
            p for p in payloads if p.get("details", {}).get("task_failure")
        ]
        assert len(task_payloads) == 1
        assert task_payloads[0]["details"]["task"] == "periodic:tick"
        assert task_payloads[0].get("device") is None
        assert task_payloads[0]["error_type"] == "error"

    async def test_dead_internal_loop_escalates_under_ignore(self) -> None:
        """A cancelled heartbeat loop exits even with on_task_failure='ignore'."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, on_task_failure="ignore")

        @harness.app.telemetry("temp", interval=60)
        async def temp() -> dict[str, int]:
            return {"value": 1}

        run = asyncio.create_task(harness.run())
        await clock.settle(
            until=lambda: any(
                t.get_name() == "cosalette-heartbeat-loop" for t in asyncio.all_tasks()
            )
        )

        # Act
        _task_named("cosalette-heartbeat-loop").cancel()

        # Assert
        with pytest.raises(TaskSupervisionError) as caught:
            await asyncio.wait_for(run, timeout=5.0)
        assert caught.value.internal is True
        assert caught.value.task_name == "cosalette-heartbeat-loop"


# ---------------------------------------------------------------------------
# Restart, deferred first cycle and recovery
# ---------------------------------------------------------------------------


class TestRestartAndRecovery:
    """Technique: State Transition Testing."""

    async def test_restarted_device_recovers_availability(self) -> None:
        """A device that fails once comes back online at its first yield."""
        # Arrange
        harness = _harness()
        attempts = 0
        recovered = asyncio.Event()

        @harness.app.device("dev")
        async def dev(ctx: DeviceContext) -> AsyncIterator[None]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                msg = "first start fails"
                raise RuntimeError(msg)
            yield
            recovered.set()
            await asyncio.Event().wait()

        # Act
        run = asyncio.create_task(harness.run())
        await asyncio.wait_for(recovered.wait(), timeout=5.0)
        recovered_before_shutdown = _recovered(harness, "dev")
        harness.trigger_shutdown()
        await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert recovered_before_shutdown is True
        assert len(_payloads(harness, "testapp/dev/error")) == 1

    async def test_restarted_telemetry_defers_first_poll(self) -> None:
        """A re-created telemetry task waits one interval before polling.

        Technique: Boundary Value Analysis — just before and at the interval.
        """
        # Arrange
        clock = ManualClock()
        harness = _harness(clock)
        calls = 0

        @harness.app.telemetry("temp", interval=60)
        async def temp() -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"value": calls}

        run = asyncio.create_task(harness.run())
        try:
            await clock.settle(until=lambda: calls == 1)

            # Act — kill the task from outside the framework
            _task_named("telemetry:temp").cancel()
            await clock.settle()
            await harness.advance_time(1)  # restart backoff
            calls_after_restart = calls
            await harness.advance_time(59)
            calls_before_interval = calls
            await harness.advance_time(1)
            await clock.settle(until=lambda: _recovered(harness, "temp"))
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert calls_after_restart == 1
        assert calls_before_interval == 1

    async def test_restarted_group_defers_first_poll(self) -> None:
        """A re-created coalescing group polls no member before its interval."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock)
        calls = {"a": 0, "b": 0}

        @harness.app.telemetry("a", interval=60, group="g")
        async def a() -> dict[str, int]:
            calls["a"] += 1
            return {"value": 1}

        @harness.app.telemetry("b", interval=60, group="g")
        async def b() -> dict[str, int]:
            calls["b"] += 1
            return {"value": 2}

        run = asyncio.create_task(harness.run())
        try:
            await clock.settle(until=lambda: calls == {"a": 1, "b": 1})

            # Act
            _task_named("group:g").cancel()
            await clock.settle()
            await harness.advance_time(1)
            calls_after_restart = dict(calls)
            await harness.advance_time(60)
            await clock.settle(
                until=lambda: _recovered(harness, "a") and _recovered(harness, "b")
            )
            calls_after_interval = dict(calls)
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert calls_after_restart == {"a": 1, "b": 1}
        payloads = _payloads(harness, "testapp/error")
        group_payloads = [
            p for p in payloads if p.get("details", {}).get("task") == "group:g"
        ]
        assert len(group_payloads) == 1
        assert group_payloads[0]["details"]["entities"] == ["a", "b"]
        assert calls_after_interval == {"a": 2, "b": 2}
