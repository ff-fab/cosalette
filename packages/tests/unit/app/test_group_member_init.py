"""App-level tests for coalescing-group member ``init=`` isolation (ADR-081).

A group member whose ``init=`` raises is handled on its own: the task
supervisor reports it and applies ``on_task_failure`` to that member while
the group task keeps polling every other member on its normal schedule.
Under ``"restart"`` the group runner retries the member's ``init=`` in
place with the supervisor's backoff and per-member budget; an exhausted
member stays offline without an exit, and a group with no member left idles
until shutdown.

Test Techniques Used:
    - State Transition Testing: inactive -> retry -> joined -> recovered;
      inactive -> exhausted -> offline; group re-created by the supervisor
      and by an ADR-029 adapter restart.
    - Boundary Value Analysis: the backoff sequence (1, 2, 4 s), the last
      granted retry and the first refused one.
    - Equivalence Partitioning: the ``restart`` / ``ignore`` policies; one
      failing member versus every member failing.
    - Specification-based Testing: payload ``details``, availability
      topics, log records, the one-live-task invariant.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, override
from unittest.mock import AsyncMock

import pytest

from cosalette._app import App, _lifecycle
from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthCheckRunner, HealthReporter
from cosalette._injection import build_injection_plan
from cosalette._registration import _noop_lifespan, _TelemetryRegistration
from cosalette._supervisor import TaskRestartCounters, TaskSupervisor
from cosalette._wiring import DeviceInfo, run_lifespan_and_devices
from cosalette.testing import AppHarness, ManualClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


class _Probe:
    """Init result injected into a member's handler."""


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _recording(logger_name: str) -> Iterator[_RecordingHandler]:
    # configure_logging() replaces root handlers, so caplog cannot see the
    # records; listen on the named logger directly.
    handler = _RecordingHandler()
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


@pytest.fixture
def supervisors(monkeypatch: pytest.MonkeyPatch) -> list[TaskSupervisor]:
    """Capture the App's task supervisor to read its counters."""
    created: list[TaskSupervisor] = []

    class _Capturing(TaskSupervisor):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            created.append(self)

    monkeypatch.setattr(_lifecycle, "TaskSupervisor", _Capturing)
    return created


def _harness(clock: ManualClock, **app_kwargs: Any) -> AppHarness:
    return AppHarness(
        app=App(name="testapp", version="1.0.0", store=None, **app_kwargs),
        mqtt=MockMqttClient(),
        clock=clock,
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
    )


def _payloads(harness: AppHarness, topic: str) -> list[dict[str, Any]]:
    return [json.loads(payload) for payload, _, _ in harness.messages_for(topic)]


def _availability(harness: AppHarness, entity: str) -> list[str]:
    return [p for p, _, _ in harness.messages_for(f"testapp/{entity}/availability")]


def _group_tasks(name: str = "group:g") -> list[asyncio.Task[Any]]:
    return [t for t in asyncio.all_tasks() if t.get_name() == name and not t.done()]


class _Group:
    """A two-member group: ``broken`` (index 0) with an ``init=``, ``ok``.

    *init_fails* decides, per attempt number (1-based), whether the init
    raises.  Every attempt and poll is stamped with the virtual time since
    the group started.
    """

    def __init__(
        self,
        harness: AppHarness,
        clock: ManualClock,
        init_fails: Callable[[int], bool],
        *,
        interval: float = 60,
    ) -> None:
        self.clock = clock
        self.start = clock.now()
        self.init_at: list[float] = []
        self.polls: dict[str, list[float]] = {"broken": [], "ok": []}

        def probe_init() -> _Probe:
            self.init_at.append(self.now())
            if init_fails(len(self.init_at)):
                msg = "sensor out of range"
                raise RuntimeError(msg)
            return _Probe()

        @harness.app.telemetry("broken", interval=interval, group="g", init=probe_init)
        async def broken(probe: _Probe) -> dict[str, int]:
            self.polls["broken"].append(self.now())
            return {"value": len(self.polls["broken"])}

        @harness.app.telemetry("ok", interval=interval, group="g")
        async def ok() -> dict[str, int]:
            self.polls["ok"].append(self.now())
            return {"value": len(self.polls["ok"])}

    def now(self) -> float:
        return self.clock.now() - self.start


async def _settle(clock: ManualClock, until: Callable[[], bool]) -> None:
    """Wait for *until*, then let the scheduler park on its next sleep.

    Without the second, unconditional settle an ``advance()`` could run
    before the group re-enters its sleep and start that sleep late.
    """
    await clock.settle(until=until)
    await clock.settle()


async def _stop(harness: AppHarness, run: asyncio.Task[None]) -> None:
    harness.trigger_shutdown()
    await asyncio.wait_for(run, timeout=5.0)


class TestMemberIsolation:
    """Technique: Specification-based Testing — one member fails alone."""

    async def test_other_members_keep_polling(self) -> None:
        """A failing member's init leaves the rest of the group on schedule."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, on_task_failure="ignore")
        group = _Group(harness, clock, lambda _attempt: True)

        with (
            _recording("cosalette._supervisor") as supervisor_log,
            _recording("cosalette._errors") as errors_log,
        ):
            run = asyncio.create_task(harness.run())
            try:
                await _settle(clock, lambda: len(group.polls["ok"]) == 1)

                # Act
                await harness.advance_time(60)
                await harness.advance_time(60)
                await _settle(clock, lambda: len(group.polls["ok"]) == 3)
                group_alive = len(_group_tasks()) == 1
            finally:
                await _stop(harness, run)

        # Assert
        assert group.polls == {"broken": [], "ok": [0.0, 60.0, 120.0]}
        assert group_alive is True
        details = [p["details"] for p in _payloads(harness, "testapp/broken/error")]
        assert details == [
            {
                "task_failure": True,
                "task": "group:g",
                "member": "broken",
                "phase": "init",
            }
        ]
        assert _payloads(harness, "testapp/ok/error") == []
        assert "offline" in _availability(harness, "broken")
        assert "offline" not in _availability(harness, "ok")[:-1]
        critical = [r for r in supervisor_log.records if r.levelname == "CRITICAL"]
        assert len(critical) == 1
        assert critical[0].exc_info is not None
        warnings = [r for r in errors_log.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert warnings[0].exc_info is None

    async def test_ignore_policy_never_retries_the_member(self) -> None:
        """Under 'ignore' the member's init is attempted exactly once."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, on_task_failure="ignore")
        group = _Group(harness, clock, lambda _attempt: True)
        run = asyncio.create_task(harness.run())
        try:
            await _settle(clock, lambda: len(group.polls["ok"]) == 1)

            # Act
            for _ in range(10):
                await harness.advance_time(60)
            await _settle(clock, lambda: len(group.polls["ok"]) == 11)
            running = not run.done()
        finally:
            await _stop(harness, run)

        # Assert
        assert group.init_at == [0.0]
        assert running is True
        assert _availability(harness, "broken")[-1] == "offline"


class TestMemberRetry:
    """Technique: State Transition Testing — retry, join, recover."""

    async def test_retry_backs_off_then_joins_and_recovers(
        self, supervisors: list[TaskSupervisor]
    ) -> None:
        """Init retried at 1 s and 3 s; success polls at once, then aligns.

        Technique: Boundary Value Analysis — the 1 s and 2 s backoff steps.
        """
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, task_restart_window=1e9)
        group = _Group(harness, clock, lambda attempt: attempt <= 2)
        run = asyncio.create_task(harness.run())
        try:
            await _settle(clock, lambda: len(group.polls["ok"]) == 1)

            # Act
            await harness.advance_time(1)
            await harness.advance_time(2)
            await _settle(clock, lambda: len(group.polls["broken"]) == 1)
            await harness.advance_time(57)
            await clock.settle(
                until=lambda: (
                    len(group.polls["broken"]) == 2 and len(group.polls["ok"]) == 2
                )
            )
            (supervisor,) = supervisors
            counters = supervisor.counters("group:g/broken")
            group_counters = supervisor.counters("group:g")
        finally:
            await _stop(harness, run)

        # Assert
        assert group.init_at == [0.0, 1.0, 3.0]
        assert group.polls == {"broken": [3.0, 60.0], "ok": [0.0, 60.0]}
        availability = _availability(harness, "broken")
        assert availability[availability.index("offline") + 1] == "online"
        assert len(_payloads(harness, "testapp/broken/error")) == 2
        assert counters.restarts_in_window == 2
        assert counters.total_restarts == 2
        assert group_counters == TaskRestartCounters()

    async def test_exhausted_member_stays_offline_without_exit(
        self, supervisors: list[TaskSupervisor]
    ) -> None:
        """Budget spent: the member stays offline, the app keeps running."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, task_max_restarts=2, task_restart_window=1e9)
        group = _Group(harness, clock, lambda _attempt: True)
        with _recording("cosalette._supervisor") as supervisor_log:
            run = asyncio.create_task(harness.run())
            try:
                await _settle(clock, lambda: len(group.polls["ok"]) == 1)

                # Act
                await harness.advance_time(1)
                await harness.advance_time(2)
                for _ in range(10):
                    await harness.advance_time(60)
                await _settle(clock, lambda: len(group.polls["ok"]) == 11)
                running = not run.done()
                (supervisor,) = supervisors
                counters = supervisor.counters("group:g/broken")
            finally:
                await _stop(harness, run)

        # Assert
        assert group.init_at == [0.0, 1.0, 3.0]
        assert running is True
        assert supervisor.fatal_error is None
        assert counters.restarts_in_window == 2
        assert _availability(harness, "broken")[-1] == "offline"
        exhausted = [
            r
            for r in supervisor_log.records
            if "stays offline until the process restarts" in r.getMessage()
        ]
        assert len(exhausted) == 1

    async def test_retries_never_create_a_second_group_task(self) -> None:
        """Retries run inside the one group task (one live task invariant)."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, task_restart_window=1e9)
        group = _Group(harness, clock, lambda attempt: attempt <= 3)
        live: list[int] = []
        run = asyncio.create_task(harness.run())
        try:
            await _settle(clock, lambda: len(group.polls["ok"]) == 1)
            live.append(len(_group_tasks()))

            # Act
            for step in (1, 2, 4):
                await harness.advance_time(step)
                live.append(len(_group_tasks()))
            await _settle(clock, lambda: len(group.polls["broken"]) == 1)
        finally:
            await _stop(harness, run)

        # Assert
        assert group.init_at == [0.0, 1.0, 3.0, 7.0]
        assert live == [1, 1, 1, 1]


class TestAllMembersOffline:
    """Technique: Equivalence Partitioning — every member's init fails."""

    async def test_group_idles_until_shutdown(
        self, supervisors: list[TaskSupervisor]
    ) -> None:
        """No crash loop, no exit: the group task waits for shutdown."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, task_max_restarts=0)
        calls = 0

        def bad_init() -> _Probe:
            msg = "bus not found"
            raise RuntimeError(msg)

        @harness.app.telemetry("a", interval=60, group="g", init=bad_init)
        async def a(probe: _Probe) -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"value": 1}

        @harness.app.telemetry("b", interval=60, group="g", init=bad_init)
        async def b(probe: _Probe) -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"value": 2}

        with _recording("cosalette._runners._telemetry_runner") as runner_log:
            run = asyncio.create_task(harness.run())
            try:
                await _settle(
                    clock, lambda: len(_payloads(harness, "testapp/b/error")) == 1
                )

                # Act
                for _ in range(5):
                    await harness.advance_time(60)
                group_alive = len(_group_tasks()) == 1
                running = not run.done()
                (supervisor,) = supervisors
                group_counters = supervisor.counters("group:g")
            finally:
                await _stop(harness, run)

        # Assert
        assert calls == 0
        assert group_alive is True
        assert running is True
        assert group_counters == TaskRestartCounters()
        assert supervisor.fatal_error is None
        idle = [
            r for r in runner_log.records if "idles until shutdown" in r.getMessage()
        ]
        assert len(idle) == 1


class TestGroupRecreation:
    """Technique: State Transition Testing — member budgets survive the group."""

    async def test_supervisor_restart_keeps_the_member_budget(
        self, supervisors: list[TaskSupervisor]
    ) -> None:
        """A re-created group tries the init once; a spent budget stays spent."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock, task_max_restarts=1, task_restart_window=1e9)
        group = _Group(harness, clock, lambda _attempt: True)
        run = asyncio.create_task(harness.run())
        try:
            await _settle(clock, lambda: len(group.polls["ok"]) == 1)
            await harness.advance_time(1)  # the one granted retry
            await _settle(clock, lambda: len(group.init_at) == 2)

            # Act — kill the group from outside; the supervisor re-creates it
            _group_tasks()[0].cancel()
            await clock.settle()
            await harness.advance_time(1)  # group restart backoff
            await _settle(clock, lambda: len(group.init_at) == 3)
            for _ in range(5):
                await harness.advance_time(60)
            (supervisor,) = supervisors
            counters = supervisor.counters("group:g/broken")
            live = len(_group_tasks())
        finally:
            await _stop(harness, run)

        # Assert
        assert group.init_at == [0.0, 1.0, 2.0]
        assert counters.restarts_in_window == 1
        assert counters.total_restarts == 1
        assert supervisor.counters("group:g").total_restarts == 1
        assert live == 1


class _Sensor:
    """Port the failing member depends on (ADR-029 restart target)."""


class _SensorAdapter:
    async def __aenter__(self) -> _SensorAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def health_check(self) -> bool:
        return True


class TestAdapterRestartRecreation:
    """Technique: State Transition Testing — ADR-029 re-creates the group."""

    async def test_adapter_restart_keeps_the_member_budget(self) -> None:
        """The re-created group's init failure counts against the old budget."""
        # Arrange
        clock = ManualClock()
        mqtt = AsyncMock()
        reporter = HealthReporter(
            mqtt=mqtt, topic_prefix="test", version="0.1.0", clock=clock
        )
        errors = ErrorPublisher(mqtt=mqtt, topic_prefix="test")
        shutdown = asyncio.Event()
        settings = make_settings()
        adapter = _SensorAdapter()
        supervisor = TaskSupervisor(
            policy="restart",
            max_restarts=1,
            restart_window=1e9,
            clock=clock,
            shutdown_event=shutdown,
            health_reporter=reporter,
            error_publisher=errors,
        )
        attempts = 0
        ok_polls = 0

        def bad_init() -> _Probe:
            nonlocal attempts
            attempts += 1
            msg = "sensor out of range"
            raise RuntimeError(msg)

        async def broken(probe: _Probe) -> dict[str, object]:
            return {"value": 1}

        async def ok() -> dict[str, object]:
            nonlocal ok_polls
            ok_polls += 1
            return {"value": ok_polls}

        telemetry = [
            _TelemetryRegistration(
                name="broken",
                func=broken,
                injection_plan=build_injection_plan(broken),
                interval=60.0,
                group="g",
                init=bad_init,
                init_injection_plan=[],
            ),
            _TelemetryRegistration(
                name="ok", func=ok, injection_plan=[], interval=60.0, group="g"
            ),
        ]
        contexts = {
            n: DeviceContext(
                name=n,
                settings=settings,
                mqtt=mqtt,
                topic_prefix="test",
                shutdown_event=shutdown,
                adapters={},
                clock=clock,
                is_root=False,
            )
            for n in ("broken", "ok")
        }
        runner = HealthCheckRunner(
            health_checkables={_Sensor: adapter},
            adapter_device_map={_Sensor: [("broken", False)]},
            health_reporter=reporter,
            clock=clock,
            interval=3600.0,
            shutdown_event=shutdown,
            restart_after_failures=1,
        )
        wiring = asyncio.create_task(
            run_lifespan_and_devices(
                lifespan=_noop_lifespan,
                store=None,
                devices=[],
                telemetry=telemetry,
                heartbeat_interval=None,
                resolved_settings=settings,
                resolved_adapters={_Sensor: adapter},
                health_reporter=reporter,
                error_publisher=errors,
                contexts=contexts,
                shutdown_event=shutdown,
                health_check_runner=runner,
                restart_cooldown=1.0,
                adapter_device_map={_Sensor: [DeviceInfo("broken", False)]},
                resolved_clock=clock,
                supervisor=supervisor,
            )
        )
        try:
            await _settle(clock, lambda: attempts == 1 and ok_polls == 1)
            await clock.advance(1)  # the one granted retry
            await _settle(clock, lambda: attempts == 2)
            on_restart = runner._on_restart_needed  # noqa: SLF001
            assert on_restart is not None

            # Act — an ADR-029 adapter restart re-creates the whole group
            restart = asyncio.ensure_future(on_restart(_Sensor, adapter))
            await clock.settle()
            await clock.advance(1)  # restart cooldown
            restarted = await asyncio.wait_for(restart, timeout=5.0)
            await _settle(clock, lambda: attempts == 3 and ok_polls == 2)
            await clock.advance(120)
            await _settle(clock, lambda: ok_polls == 4)
            counters = supervisor.counters("group:g/broken")
            live = len(_group_tasks())
        finally:
            shutdown.set()
            await asyncio.wait_for(wiring, timeout=5.0)
            await supervisor.aclose()

        # Assert
        assert restarted is True
        assert attempts == 3
        assert counters.restarts_in_window == 1
        assert counters.total_restarts == 1
        assert supervisor.fatal_error is None
        assert live == 1
