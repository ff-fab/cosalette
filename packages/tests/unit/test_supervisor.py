"""Unit tests for cosalette._supervisor — task supervision (ADR-081).

Covers: failure detection (normal return, expected and external
cancellation, exceptions), the one error payload per crash and its routing,
log deduplication, the ``supervisor`` availability mark, the
``on_task_failure`` policies, the restart backoff and budget, internal-loop
escalation, policy resolution, the ADR-029 adapter-restart interactions,
counters, shutdown and coalescing-group member ``init=`` isolation.

Test Techniques Used:
    - Specification-based Testing: payload shape, log records, counters.
    - Decision Table: policy x task kind (entity / internal / no factory)
      -> restart, stay down or escalate.
    - Boundary Value Analysis: backoff 1/2/4 s and the 60 s cap; the budget
      at max_restarts and the restart window at 300 s.
    - State Transition Testing: running -> failed -> restart pending ->
      running; budget window reset; adapter-owned state.
    - Equivalence Partitioning: task end classes (return, raise, expected
      cancel, shutdown cancel, external cancel).
    - Error Guessing: a raising restart factory, a duplicate live task.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cosalette import _supervisor
from cosalette._supervisor import (
    RESTART_BACKOFF_CAP,
    SUPERVISOR_SOURCE,
    TaskRestartCounters,
    TaskSupervisionError,
    TaskSupervisor,
    resolve_task_failure_policy,
    restart_backoff,
    validate_task_failure_policy,
)

pytestmark = pytest.mark.unit

_SUPERVISOR_LOGGER = "cosalette._supervisor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StepClock:
    """Clock double recording each sleep; time moves only by sleeps or ``t``.

    With ``gate`` set, a sleep blocks until the gate opens instead.
    """

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []
        self.gate: asyncio.Event | None = None

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.gate is not None:
            await self.gate.wait()
            return
        self.t += seconds
        await asyncio.sleep(0)


class _Harness:
    """A supervisor wired to mock collaborators."""

    def __init__(self, **kwargs: Any) -> None:
        self.clock = _StepClock()
        self.shutdown = asyncio.Event()
        self.health = MagicMock()
        self.health.publish_device_unavailable = AsyncMock()
        self.errors = MagicMock()
        self.errors.publish = AsyncMock()
        self.supervisor = TaskSupervisor(
            clock=self.clock,
            shutdown_event=self.shutdown,
            health_reporter=self.health,
            error_publisher=self.errors,
            **kwargs,
        )


class _Factory:
    """Restart factory: creates a task named *name* from *make* each call."""

    def __init__(
        self, name: str, make: Callable[[], Coroutine[Any, Any, None]]
    ) -> None:
        self.name = name
        self.make = make
        self.calls = 0
        self.tasks: list[asyncio.Task[None]] = []

    def start(self) -> asyncio.Task[None]:
        task = asyncio.create_task(self.make(), name=self.name)
        self.tasks.append(task)
        return task

    def __call__(self) -> asyncio.Task[None]:
        self.calls += 1
        return self.start()


async def _boom() -> None:
    msg = "boom"
    raise RuntimeError(msg)


async def _forever() -> None:
    await asyncio.Event().wait()


async def _settle(rounds: int = 50) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


async def _cleanup(*tasks: asyncio.Task[None]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Backoff and policy helpers
# ---------------------------------------------------------------------------


class TestRestartBackoff:
    """Technique: Boundary Value Analysis — doubling from 1 s, 60 s cap."""

    @pytest.mark.parametrize(
        ("restart_number", "expected"),
        [(0, 1.0), (1, 1.0), (2, 2.0), (3, 4.0), (6, 32.0), (7, 60.0), (1000, 60.0)],
    )
    def test_restart_backoff_doubles_up_to_cap(
        self, restart_number: int, expected: float
    ) -> None:
        """Backoff is 1 s, doubling per restart, never above 60 s."""
        # Act
        delay = restart_backoff(restart_number)

        # Assert
        assert delay == expected
        assert delay <= RESTART_BACKOFF_CAP


class TestPolicyHelpers:
    """Technique: Equivalence Partitioning and Decision Table."""

    @pytest.mark.parametrize("policy", ["restart", "exit", "ignore"])
    def test_validate_accepts_known_policies(self, policy: str) -> None:
        """Every documented policy validates to itself."""
        # Act / Assert
        assert validate_task_failure_policy(policy) == policy

    @pytest.mark.parametrize("policy", ["Restart", "", None, 1, "crash"])
    def test_validate_rejects_unknown_policies(self, policy: object) -> None:
        """Anything else raises ValueError naming the allowed values."""
        # Act / Assert
        with pytest.raises(ValueError, match="on_task_failure must be one of"):
            validate_task_failure_policy(policy)

    def test_resolve_without_registrations_returns_app_policy(self) -> None:
        """Internal-free tasks with no registrations inherit the app policy."""
        # Act / Assert
        assert resolve_task_failure_policy("ignore") == "ignore"

    def test_resolve_inherits_app_policy_for_every_registration(self) -> None:
        """No per-registration override exists yet: the app policy applies."""
        # Act / Assert
        assert resolve_task_failure_policy("exit", [object(), object()]) == "exit"

    @pytest.mark.parametrize(
        ("member_policies", "expected"),
        [
            (["ignore", "restart"], "restart"),
            (["restart", "exit"], "exit"),
            (["ignore", "exit", "restart"], "exit"),
            (["ignore", "ignore"], "ignore"),
        ],
    )
    def test_resolve_strictest_member_policy_wins(
        self,
        monkeypatch: pytest.MonkeyPatch,
        member_policies: list[str],
        expected: str,
    ) -> None:
        """Group members with different policies resolve to the strictest.

        Technique: Decision Table — exit > restart > ignore.
        """
        # Arrange
        regs = [object() for _ in member_policies]
        by_reg = dict(zip(map(id, regs), member_policies, strict=True))
        monkeypatch.setattr(
            _supervisor, "_registration_policy", lambda reg, _app: by_reg[id(reg)]
        )

        # Act
        result = resolve_task_failure_policy("restart", regs)

        # Assert
        assert result == expected


class TestTaskSupervisionError:
    """Technique: Specification-based Testing — attributes and message."""

    def test_entity_task_error_carries_name_and_count(self) -> None:
        """An entity failure names the task and its restart count."""
        # Act
        error = TaskSupervisionError("telemetry:radon", 3, internal=False)

        # Assert
        assert isinstance(error, RuntimeError)
        assert error.task_name == "telemetry:radon"
        assert error.restart_count == 3
        assert error.internal is False
        assert "telemetry:radon" in str(error)
        assert "3 restart(s)" in str(error)

    def test_internal_loop_error_says_framework_loop(self) -> None:
        """An internal-loop failure is worded as a framework loop death."""
        # Act
        error = TaskSupervisionError("cosalette-heartbeat-loop", 0, internal=True)

        # Assert
        assert error.internal is True
        assert "Framework loop" in str(error)


# ---------------------------------------------------------------------------
# Failure detection
# ---------------------------------------------------------------------------


class TestFailureDetection:
    """Technique: Equivalence Partitioning — the classes of task end."""

    async def test_normal_return_is_not_a_failure(self) -> None:
        """A task that returns is never reported or restarted."""
        # Arrange
        h = _Harness()
        factory = _Factory("device:quiet", _settle)
        h.supervisor.supervise(
            factory.start(), entities=[("quiet", False)], restart=factory
        )

        # Act
        await _settle(100)

        # Assert
        h.errors.publish.assert_not_awaited()
        assert factory.calls == 0
        assert h.supervisor.counters("device:quiet") == TaskRestartCounters()

    async def test_expected_cancel_is_not_a_failure(self) -> None:
        """A cancel announced with expect_cancel never reaches the policy."""
        # Arrange
        h = _Harness()
        factory = _Factory("device:a", _forever)
        task = factory.start()
        h.supervisor.supervise(task, entities=[("a", False)], restart=factory)
        await _settle()

        # Act
        h.supervisor.expect_cancel(task)
        task.cancel()
        await _settle()

        # Assert
        h.errors.publish.assert_not_awaited()
        assert factory.calls == 0

    async def test_cancel_during_shutdown_is_not_a_failure(self) -> None:
        """Once the shutdown event is set, cancellations are expected."""
        # Arrange
        h = _Harness()
        factory = _Factory("device:a", _forever)
        task = factory.start()
        h.supervisor.supervise(task, entities=[("a", False)], restart=factory)
        await _settle()

        # Act
        h.shutdown.set()
        task.cancel()
        await _settle()

        # Assert
        h.errors.publish.assert_not_awaited()
        assert h.supervisor.fatal_error is None

    async def test_external_cancel_is_a_failure(self) -> None:
        """A cancel from outside the framework is reported and restarted."""
        # Arrange
        h = _Harness()
        factory = _Factory("device:a", _forever)
        task = factory.start()
        h.supervisor.supervise(task, entities=[("a", False)], restart=factory)
        await _settle()

        # Act
        task.cancel()
        await _settle()

        # Assert
        h.errors.publish.assert_awaited_once()
        reported = h.errors.publish.await_args.args[0]
        assert isinstance(reported, asyncio.CancelledError)
        assert factory.calls == 1
        assert h.supervisor.was_handled(task)

        await _cleanup(*factory.tasks)

    async def test_exception_is_a_failure(self) -> None:
        """A raising task is reported once and marked handled."""
        # Arrange
        h = _Harness(policy="ignore")
        task = asyncio.create_task(_boom(), name="device:a")

        # Act
        h.supervisor.supervise(task, entities=[("a", False)])
        await _settle()

        # Assert
        h.errors.publish.assert_awaited_once()
        assert h.supervisor.was_handled(task)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestFailureReport:
    """Technique: Specification-based Testing — ADR-081 section 2."""

    @pytest.mark.parametrize(
        ("entities", "expected_device", "expected_root", "expected_entities"),
        [
            ([("radon", False)], "radon", False, None),
            ([("hub", True)], "hub", True, None),
            ([("a", False), ("b", False)], None, False, ["a", "b"]),
            ([], None, False, None),
        ],
        ids=["single", "root", "group", "periodic"],
    )
    async def test_one_payload_routed_by_entities(
        self,
        entities: list[tuple[str, bool]],
        expected_device: str | None,
        expected_root: bool,
        expected_entities: list[str] | None,
    ) -> None:
        """One payload per crash; device routing follows the entity count.

        Technique: Equivalence Partitioning — one entity, a root entity,
        several entities (group), none (periodic).
        """
        # Arrange
        h = _Harness(policy="ignore")
        task = asyncio.create_task(_boom(), name="task:x")

        # Act
        h.supervisor.supervise(task, entities=entities)
        await _settle()

        # Assert
        h.errors.publish.assert_awaited_once()
        call = h.errors.publish.await_args
        assert isinstance(call.args[0], RuntimeError)
        assert call.kwargs["device"] == expected_device
        assert call.kwargs["is_root"] is expected_root
        assert call.kwargs["log_traceback"] is False
        details = call.kwargs["details"]
        assert details["task_failure"] is True
        assert details["task"] == "task:x"
        assert details.get("entities") == expected_entities

    async def test_one_critical_log_with_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The supervisor logs the crash once, at CRITICAL, with traceback."""
        # Arrange
        h = _Harness(policy="ignore")
        task = asyncio.create_task(_boom(), name="device:a")

        # Act
        with caplog.at_level(logging.DEBUG, logger=_SUPERVISOR_LOGGER):
            h.supervisor.supervise(task, entities=[("a", False)])
            await _settle()

        # Assert
        traced = [r for r in caplog.records if r.exc_info]
        assert len(traced) == 1
        assert traced[0].levelno == logging.CRITICAL
        assert "device:a" in traced[0].getMessage()
        assert "(entities: a)" in traced[0].getMessage()

    async def test_entities_marked_offline_with_error_status(self) -> None:
        """Every entity gets the supervisor source and the error status."""
        # Arrange
        h = _Harness(policy="ignore")
        task = asyncio.create_task(_boom(), name="group:g")

        # Act
        h.supervisor.supervise(task, entities=[("a", False), ("b", True)])
        await _settle()

        # Assert
        h.health.publish_device_unavailable.assert_any_await(
            "a", is_root=False, source=SUPERVISOR_SOURCE
        )
        h.health.publish_device_unavailable.assert_any_await(
            "b", is_root=True, source=SUPERVISOR_SOURCE
        )
        h.health.set_device_status.assert_any_call("a", "error")
        h.health.set_device_status.assert_any_call("b", "error")

    @pytest.mark.parametrize("is_root", [False, True], ids=["named", "root"])
    async def test_stream_entities_get_heartbeat_status_only(
        self, is_root: bool
    ) -> None:
        """``availability=False``: no offline publish, no device roster entry.

        The single payload still follows the entity, so a root stream's
        payload is routed with ``is_root=True`` (``{prefix}/error`` only).

        Technique: Equivalence Partitioning — named vs root stream.
        """
        # Arrange
        h = _Harness(policy="ignore")
        task = asyncio.create_task(_boom(), name="stream:feed")

        # Act
        h.supervisor.supervise(task, entities=[("feed", is_root)], availability=False)
        await _settle()

        # Assert
        h.health.mark_stream_failed.assert_called_once_with("feed")
        h.health.publish_device_unavailable.assert_not_awaited()
        h.health.set_device_status.assert_not_called()
        call = h.errors.publish.await_args
        assert call.kwargs["device"] == "feed"
        assert call.kwargs["is_root"] is is_root

    async def test_report_failure_does_not_stop_policy(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A raising error publisher is logged; the policy still applies.

        Technique: Error Guessing.
        """
        # Arrange
        h = _Harness(policy="exit")
        h.errors.publish.side_effect = OSError("broker gone")
        task = asyncio.create_task(_boom(), name="device:a")

        # Act
        with caplog.at_level(logging.ERROR, logger=_SUPERVISOR_LOGGER):
            h.supervisor.supervise(task, entities=[("a", False)])
            await _settle()

        # Assert
        assert "Failed to report the failure" in caplog.text
        assert h.shutdown.is_set()


# ---------------------------------------------------------------------------
# Policies, backoff and budget
# ---------------------------------------------------------------------------


class TestPolicies:
    """Technique: Decision Table — policy x task kind."""

    async def test_restart_budget_backoff_then_exit(self) -> None:
        """Three restarts after 1, 2 and 4 s; the fourth failure exits.

        Technique: Boundary Value Analysis — the budget of 3.
        """
        # Arrange
        h = _Harness(policy="restart", max_restarts=3)
        factory = _Factory("telemetry:radon", _boom)

        # Act
        h.supervisor.supervise(
            factory.start(), entities=[("radon", False)], restart=factory
        )
        await _settle(200)

        # Assert
        assert h.clock.sleeps == [1.0, 2.0, 4.0]
        assert factory.calls == 3
        assert h.errors.publish.await_count == 4
        assert h.shutdown.is_set()
        error = h.supervisor.fatal_error
        assert isinstance(error, TaskSupervisionError)
        assert error.task_name == "telemetry:radon"
        assert error.restart_count == 3
        assert error.internal is False
        assert isinstance(error.__cause__, RuntimeError)
        counters = h.supervisor.counters("telemetry:radon")
        assert counters.restarts_in_window == 3
        assert counters.total_restarts == 3
        assert counters.last_failure_type == "RuntimeError"

    async def test_zero_budget_exits_on_first_failure(self) -> None:
        """task_max_restarts=0 escalates without any restart.

        Technique: Boundary Value Analysis — the lower bound.
        """
        # Arrange
        h = _Harness(policy="restart", max_restarts=0)
        factory = _Factory("device:a", _boom)

        # Act
        h.supervisor.supervise(
            factory.start(), entities=[("a", False)], restart=factory
        )
        await _settle()

        # Assert
        assert factory.calls == 0
        assert isinstance(h.supervisor.fatal_error, TaskSupervisionError)

    @pytest.mark.parametrize(
        ("quiet", "expected_in_window"),
        [(299.0, 2), (300.0, 1)],
        ids=["inside-window", "window-elapsed"],
    )
    async def test_budget_resets_after_quiet_window(
        self, quiet: float, expected_in_window: int
    ) -> None:
        """The window count resets once 300 s pass without a failure.

        Technique: Boundary Value Analysis — just below and at the window.
        """
        # Arrange
        h = _Harness(policy="restart", max_restarts=3, restart_window=300.0)
        triggers: list[asyncio.Event] = []

        async def _fails_on_signal() -> None:
            trigger = asyncio.Event()
            triggers.append(trigger)
            await trigger.wait()
            raise RuntimeError("late")

        factory = _Factory("device:a", _fails_on_signal)
        first = factory.start()
        h.supervisor.supervise(first, entities=[("a", False)], restart=factory)
        await _settle()
        triggers[0].set()
        await _settle()
        assert factory.calls == 1
        failure_time = h.supervisor.counters("device:a").last_failure
        assert failure_time is not None

        # Act
        h.clock.t = failure_time + quiet
        triggers[1].set()
        await _settle()

        # Assert
        counters = h.supervisor.counters("device:a")
        assert counters.restarts_in_window == expected_in_window
        assert counters.total_restarts == 2

        await _cleanup(*factory.tasks)

    async def test_exit_policy_escalates_first_failure(self) -> None:
        """on_task_failure='exit' shuts down on the first failure."""
        # Arrange
        h = _Harness(policy="exit")
        factory = _Factory("device:a", _boom)

        # Act
        h.supervisor.supervise(
            factory.start(), entities=[("a", False)], restart=factory
        )
        await _settle()

        # Assert
        assert factory.calls == 0
        assert h.shutdown.is_set()
        assert h.supervisor.fatal_error is not None
        assert h.supervisor.fatal_error.restart_count == 0

    async def test_ignore_policy_leaves_entity_down(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """on_task_failure='ignore' reports, marks offline, never restarts."""
        # Arrange
        h = _Harness(policy="ignore")
        factory = _Factory("device:a", _boom)

        # Act
        with caplog.at_level(logging.WARNING, logger=_SUPERVISOR_LOGGER):
            h.supervisor.supervise(
                factory.start(), entities=[("a", False)], restart=factory
            )
            await _settle()

        # Assert
        assert factory.calls == 0
        assert not h.shutdown.is_set()
        assert h.supervisor.fatal_error is None
        h.health.set_device_status.assert_called_with("a", "error")
        assert "stays down" in caplog.text

    async def test_restart_without_factory_escalates(self) -> None:
        """A task with no restart factory cannot restart, so it escalates."""
        # Arrange
        h = _Harness(policy="restart")
        task = asyncio.create_task(_boom(), name="periodic:p")

        # Act
        h.supervisor.supervise(task)
        await _settle()

        # Assert
        assert h.shutdown.is_set()

    @pytest.mark.parametrize("policy", ["restart", "exit", "ignore"])
    async def test_internal_loop_always_escalates(self, policy: str) -> None:
        """A dead framework loop exits under every policy."""
        # Arrange
        h = _Harness(policy=policy)
        task = asyncio.create_task(_boom(), name="cosalette-heartbeat-loop")

        # Act
        h.supervisor.supervise_internal(task)
        await _settle()

        # Assert
        assert h.shutdown.is_set()
        error = h.supervisor.fatal_error
        assert error is not None
        assert error.internal is True
        assert error.task_name == "cosalette-heartbeat-loop"
        h.health.publish_device_unavailable.assert_not_awaited()

    async def test_supervise_internal_accepts_none(self) -> None:
        """A loop that was not started (None) is ignored."""
        # Arrange
        h = _Harness()

        # Act
        h.supervisor.supervise_internal(None)

        # Assert
        assert h.supervisor.all_counters() == {}

    async def test_first_escalation_wins(self) -> None:
        """Later escalations keep the first fatal error."""
        # Arrange
        h = _Harness(policy="exit")
        first = asyncio.create_task(_boom(), name="device:first")
        h.supervisor.supervise(first, entities=[("first", False)])
        await _settle()

        # Act
        h.supervisor._closed = False  # noqa: SLF001
        h.shutdown.clear()
        second = asyncio.create_task(_boom(), name="device:second")
        h.supervisor.supervise(second, entities=[("second", False)])
        await _settle()

        # Assert
        assert h.supervisor.fatal_error is not None
        assert h.supervisor.fatal_error.task_name == "device:first"

    async def test_raising_restart_factory_counts_as_failure(self) -> None:
        """A factory that raises is a failure of the same registration.

        Technique: Error Guessing.
        """
        # Arrange
        h = _Harness(policy="restart", max_restarts=2)

        def _bad_factory() -> asyncio.Task[None]:
            msg = "cannot start"
            raise ValueError(msg)

        task = asyncio.create_task(_boom(), name="device:a")

        # Act
        h.supervisor.supervise(task, entities=[("a", False)], restart=_bad_factory)
        await _settle(200)

        # Assert
        assert h.clock.sleeps == [1.0, 2.0]
        assert h.supervisor.fatal_error is not None
        assert isinstance(h.supervisor.fatal_error.__cause__, ValueError)

    async def test_restarted_task_is_supervised_again(self) -> None:
        """The replacement task is tracked: its own failure is seen."""
        # Arrange
        h = _Harness(policy="restart", max_restarts=5)
        attempts = 0

        async def _fails_twice() -> None:
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise RuntimeError("flaky")
            await asyncio.Event().wait()

        factory = _Factory("device:a", _fails_twice)

        # Act
        h.supervisor.supervise(
            factory.start(), entities=[("a", False)], restart=factory
        )
        await _settle(200)

        # Assert
        assert factory.calls == 2
        assert h.supervisor.counters("device:a").total_restarts == 2
        assert not h.shutdown.is_set()

        await _cleanup(*factory.tasks)


# ---------------------------------------------------------------------------
# ADR-029 interactions
# ---------------------------------------------------------------------------


class TestAdapterRestartInteraction:
    """Technique: State Transition Testing — ADR-081 section 7."""

    async def test_adapter_restart_cancels_pending_restart(self) -> None:
        """begin_adapter_restart cancels a scheduled supervisor restart."""
        # Arrange
        h = _Harness(policy="restart")
        gate = asyncio.Event()
        h.clock.gate = gate
        factory = _Factory("device:a", _boom)
        h.supervisor.supervise(
            factory.start(), entities=[("a", False)], restart=factory
        )
        await _settle()
        assert h.clock.sleeps == [1.0]

        # Act
        keys = h.supervisor.begin_adapter_restart(["a"])
        gate.set()
        await _settle()
        h.supervisor.end_adapter_restart(keys)
        await _settle()

        # Assert
        assert keys == ["device:a"]
        assert factory.calls == 0
        assert h.supervisor.counters("device:a").total_restarts == 0

    async def test_failure_while_adapter_owned_is_not_counted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A task failing during an adapter restart is logged, not counted."""
        # Arrange
        h = _Harness(policy="restart")
        fail = asyncio.Event()

        async def _fails_on_signal() -> None:
            await fail.wait()
            raise RuntimeError("adapter gone")

        factory = _Factory("device:a", _fails_on_signal)
        h.supervisor.supervise(
            factory.start(), entities=[("a", False)], restart=factory
        )
        keys = h.supervisor.begin_adapter_restart(["a"])

        # Act
        with caplog.at_level(logging.WARNING, logger=_SUPERVISOR_LOGGER):
            fail.set()
            await _settle()

        # Assert
        h.errors.publish.assert_not_awaited()
        assert factory.calls == 0
        assert h.supervisor.counters("device:a") == TaskRestartCounters()
        assert "adapter restart owns it" in caplog.text
        h.supervisor.end_adapter_restart(keys)

    async def test_adapter_owned_cancel_is_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unannounced cancel while adapter-owned is neither logged nor counted."""
        # Arrange
        h = _Harness(policy="restart")
        factory = _Factory("device:a", _forever)
        task = factory.start()
        h.supervisor.supervise(task, entities=[("a", False)], restart=factory)
        await _settle()
        keys = h.supervisor.begin_adapter_restart(["a"])

        # Act
        with caplog.at_level(logging.WARNING, logger=_SUPERVISOR_LOGGER):
            task.cancel()
            await _settle()

        # Assert
        assert caplog.records == []
        h.errors.publish.assert_not_awaited()
        h.supervisor.end_adapter_restart(keys)

    async def test_nested_adapter_restarts_refcount_ownership(self) -> None:
        """Ownership holds until every overlapping adapter restart ends."""
        # Arrange
        h = _Harness(policy="restart")
        factory = _Factory("group:g", _forever)
        h.supervisor.supervise(
            factory.start(), entities=[("a", False), ("b", False)], restart=factory
        )
        first = h.supervisor.begin_adapter_restart(["a"])
        second = h.supervisor.begin_adapter_restart(["b"])

        # Act
        h.supervisor.end_adapter_restart(first)

        # Assert
        assert "group:g" in h.supervisor._adapter_owned  # noqa: SLF001
        h.supervisor.end_adapter_restart(second)
        assert "group:g" not in h.supervisor._adapter_owned  # noqa: SLF001

        await _cleanup(*factory.tasks)

    async def test_exhausted_adapter_task_is_not_restarted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A task whose adapter is restart-exhausted stays down."""
        # Arrange
        h = _Harness(policy="restart")
        h.supervisor.set_adapter_exhausted_check(lambda name: name == "a")
        factory = _Factory("device:a", _boom)

        # Act
        with caplog.at_level(logging.ERROR, logger=_SUPERVISOR_LOGGER):
            h.supervisor.supervise(
                factory.start(), entities=[("a", False)], restart=factory
            )
            await _settle()

        # Assert
        assert factory.calls == 0
        assert not h.shutdown.is_set()
        assert "permanently offline" in caplog.text

    async def test_counters_survive_resupervision(self) -> None:
        """A task re-created by an adapter restart keeps its counters."""
        # Arrange
        h = _Harness(policy="restart", max_restarts=5)
        attempts = 0

        async def _fails_once() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("once")
            await asyncio.Event().wait()

        factory = _Factory("device:a", _fails_once)
        h.supervisor.supervise(
            factory.start(), entities=[("a", False)], restart=factory
        )
        await _settle()
        assert h.supervisor.counters("device:a").total_restarts == 1

        # Act — an adapter restart replaces the task with a fresh one
        old = factory.tasks[-1]
        h.supervisor.expect_cancel(old)
        old.cancel()
        replacement = factory.start()
        h.supervisor.supervise(replacement, entities=[("a", False)], restart=factory)
        await _settle()

        # Assert
        assert h.supervisor.counters("device:a").total_restarts == 1

        await _cleanup(*factory.tasks)

    async def test_restart_skipped_when_live_task_exists(self) -> None:
        """At most one live task per registration: a due restart is skipped
        when another task already serves it.

        Technique: Error Guessing — a duplicate task after an adapter restart.
        """
        # Arrange
        h = _Harness(policy="restart")
        gate = asyncio.Event()
        h.clock.gate = gate
        factory = _Factory("device:a", _boom)
        h.supervisor.supervise(
            factory.start(), entities=[("a", False)], restart=factory
        )
        await _settle()

        # Act — something else re-creates the task before the timer fires
        live = asyncio.create_task(_forever(), name="device:a")
        h.supervisor.supervise(live, entities=[("a", False)], restart=factory)
        gate.set()
        await _settle()

        # Assert
        assert factory.calls == 0

        await _cleanup(live)


# ---------------------------------------------------------------------------
# Counters and shutdown
# ---------------------------------------------------------------------------


class TestCountersAndClose:
    """Technique: Specification-based Testing."""

    async def test_unknown_task_has_empty_counters(self) -> None:
        """An unsupervised name reports zeroed counters."""
        # Arrange
        h = _Harness()

        # Act / Assert
        assert h.supervisor.counters("device:none") == TaskRestartCounters()

    async def test_all_counters_lists_every_registration(self) -> None:
        """all_counters keys every supervised task name."""
        # Arrange
        h = _Harness()
        a = asyncio.create_task(_forever(), name="device:a")
        b = asyncio.create_task(_forever(), name="periodic:b")
        h.supervisor.supervise(a, entities=[("a", False)])
        h.supervisor.supervise(b)

        # Act
        result = h.supervisor.all_counters()

        # Assert
        assert set(result) == {"device:a", "periodic:b"}
        await _cleanup(a, b)

    async def test_aclose_cancels_pending_restart(self) -> None:
        """aclose stops a scheduled restart and later failures are ignored."""
        # Arrange
        h = _Harness(policy="restart")
        gate = asyncio.Event()
        h.clock.gate = gate
        factory = _Factory("device:a", _boom)
        h.supervisor.supervise(
            factory.start(), entities=[("a", False)], restart=factory
        )
        await _settle()

        # Act
        await h.supervisor.aclose()
        gate.set()
        await _settle()

        # Assert
        assert h.supervisor.stopping is True
        assert factory.calls == 0

    async def test_aclose_is_idempotent(self) -> None:
        """A second aclose is a no-op."""
        # Arrange
        h = _Harness()

        # Act
        await h.supervisor.aclose()
        await h.supervisor.aclose()

        # Assert
        assert h.supervisor.stopping is True


# ---------------------------------------------------------------------------
# Coalescing-group member init= failures (ADR-081 member isolation)
# ---------------------------------------------------------------------------


def _init_error() -> RuntimeError:
    return RuntimeError("sensor out of range")


class TestGroupMemberInit:
    """A member's ``init=`` failure is isolated to that member.

    Technique: Decision Table (policy x budget -> retry delay, stay
    offline or escalate), Boundary Value Analysis (the budget edge and the
    quiet window) and Specification-based Testing (payload and marks).
    """

    async def test_failure_is_reported_for_the_member_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """One payload with member detail, one CRITICAL log, member offline."""
        # Arrange
        h = _Harness(policy="restart")
        exc = _init_error()

        # Act
        with caplog.at_level(logging.DEBUG, logger=_SUPERVISOR_LOGGER):
            delay = await h.supervisor.member_init_failed(
                "group:g", "broken", exc, is_root=False
            )

        # Assert
        assert delay == 1.0
        h.errors.publish.assert_awaited_once()
        call = h.errors.publish.await_args
        assert call.args[0] is exc
        assert call.kwargs["device"] == "broken"
        assert call.kwargs["log_traceback"] is False
        assert call.kwargs["details"] == {
            "task_failure": True,
            "task": "group:g",
            "member": "broken",
            "phase": "init",
        }
        h.health.publish_device_unavailable.assert_awaited_once_with(
            "broken", is_root=False, source=SUPERVISOR_SOURCE
        )
        h.health.set_device_status.assert_called_once_with("broken", "error")
        traced = [r for r in caplog.records if r.exc_info]
        assert len(traced) == 1
        assert traced[0].levelno == logging.CRITICAL
        assert not h.shutdown.is_set()

    async def test_retries_back_off_until_the_budget_is_spent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Delays 1, 2, 4 s, then the member stays offline — no exit.

        Technique: Boundary Value Analysis — the last granted retry and
        the first refused one.
        """
        # Arrange
        h = _Harness(policy="restart", max_restarts=3, restart_window=1e9)
        delays: list[float | None] = []

        # Act
        with caplog.at_level(logging.DEBUG, logger=_SUPERVISOR_LOGGER):
            for _ in range(4):
                delays.append(
                    await h.supervisor.member_init_failed(
                        "group:g", "broken", _init_error()
                    )
                )

        # Assert
        assert delays == [1.0, 2.0, 4.0, None]
        assert not h.shutdown.is_set()
        assert h.supervisor.fatal_error is None
        counters = h.supervisor.counters("group:g/broken")
        assert counters.restarts_in_window == 3
        assert counters.total_restarts == 3
        assert counters.last_failure_type == "RuntimeError"
        exhausted = [
            r
            for r in caplog.records
            if "stays offline until the process restarts" in r.getMessage()
        ]
        assert len(exhausted) == 1
        assert exhausted[0].levelno == logging.CRITICAL

    @pytest.mark.parametrize(
        ("quiet", "expected"),
        [(299.0, None), (300.0, 1.0)],
        ids=["inside-window", "window-elapsed"],
    )
    async def test_budget_resets_after_quiet_period(
        self, quiet: float, expected: float | None
    ) -> None:
        """An exhausted member gets a fresh budget after a quiet window.

        Technique: Boundary Value Analysis — just below and at the window.
        """
        # Arrange
        h = _Harness(policy="restart", max_restarts=1, restart_window=300.0)
        await h.supervisor.member_init_failed("group:g", "m", _init_error())
        await h.supervisor.member_init_failed("group:g", "m", _init_error())
        last = h.supervisor.counters("group:g/m").last_failure
        assert last is not None

        # Act
        h.clock.t = last + quiet
        delay = await h.supervisor.member_init_failed("group:g", "m", _init_error())

        # Assert
        assert delay == expected

    async def test_exit_policy_escalates_with_the_member_key(self) -> None:
        """'exit' shuts the app down; the error names the member."""
        # Arrange
        h = _Harness(policy="exit")
        exc = _init_error()

        # Act
        delay = await h.supervisor.member_init_failed("group:g", "m", exc)

        # Assert
        assert delay is None
        assert h.shutdown.is_set()
        error = h.supervisor.fatal_error
        assert isinstance(error, TaskSupervisionError)
        assert error.task_name == "group:g/m"
        assert error.__cause__ is exc

    async def test_ignore_policy_reports_and_never_retries(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """'ignore' reports the member, leaves it offline, grants no retry."""
        # Arrange
        h = _Harness(policy="ignore")

        # Act
        with caplog.at_level(logging.WARNING, logger=_SUPERVISOR_LOGGER):
            delay = await h.supervisor.member_init_failed("group:g", "m", _init_error())

        # Assert
        assert delay is None
        assert not h.shutdown.is_set()
        h.errors.publish.assert_awaited_once()
        h.health.set_device_status.assert_called_once_with("m", "error")
        assert h.supervisor.counters("group:g/m").total_restarts == 0
        assert "stays down" in caplog.text

    async def test_exhausted_adapter_member_is_not_retried(self) -> None:
        """A member whose adapter is permanently offline gets no retry."""
        # Arrange
        h = _Harness(policy="restart")
        h.supervisor.set_adapter_exhausted_check(lambda name: name == "m")

        # Act
        delay = await h.supervisor.member_init_failed("group:g", "m", _init_error())

        # Assert
        assert delay is None
        assert h.supervisor.counters("group:g/m").total_restarts == 0

    async def test_failure_while_stopping_is_not_reported(self) -> None:
        """During shutdown the failure is neither reported nor retried."""
        # Arrange
        h = _Harness(policy="restart")
        h.shutdown.set()

        # Act
        delay = await h.supervisor.member_init_failed("group:g", "m", _init_error())

        # Assert
        assert delay is None
        h.errors.publish.assert_not_awaited()

    async def test_adapter_restart_does_not_own_the_member_record(self) -> None:
        """The group is handed over; the member budget keeps counting.

        Technique: State Transition Testing — a member failing in the group
        an ADR-029 restart re-created is counted against the budget it had.
        """
        # Arrange
        h = _Harness(policy="restart", max_restarts=1, restart_window=1e9)
        group = asyncio.create_task(_forever(), name="group:g")
        h.supervisor.supervise(group, entities=[("m", False), ("ok", False)])
        await h.supervisor.member_init_failed("group:g", "m", _init_error())

        # Act
        keys = h.supervisor.begin_adapter_restart(["m"])
        delay = await h.supervisor.member_init_failed("group:g", "m", _init_error())
        h.supervisor.end_adapter_restart(keys)

        # Assert
        assert keys == ["group:g"]
        assert delay is None
        assert h.supervisor.counters("group:g/m").total_restarts == 1
        assert set(h.supervisor.all_counters()) == {"group:g", "group:g/m"}
        await _cleanup(group)
