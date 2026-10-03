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
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal, override

import pytest

from cosalette import TaskSupervisionError
from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._mqtt import MqttClient
from cosalette._runners._stream_types import Stream, StreamablePort
from cosalette._settings import MqttSettings
from cosalette.testing import (
    AppHarness,
    FakeClock,
    ManualClock,
    MockMqttClient,
    make_settings,
)

pytestmark = pytest.mark.unit


def _harness(
    clock: Any = None, *, run_streams: bool = False, **app_kwargs: Any
) -> AppHarness:
    """AppHarness whose App takes the supervision parameters."""
    return AppHarness(
        app=App(name="testapp", version="1.0.0", store=None, **app_kwargs),
        mqtt=MockMqttClient(),
        clock=clock if clock is not None else FakeClock(),
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
        run_streams=run_streams,
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


# ---------------------------------------------------------------------------
# Telemetry init= failures (cos-eslb)
# ---------------------------------------------------------------------------


class _Filter:
    """Init result injected into the telemetry handler."""


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class TestTelemetryInitFailure:
    """A raising telemetry ``init=`` is a task failure (ADR-081 section 1).

    Technique: Specification-based Testing and State Transition Testing.
    """

    async def test_init_failure_is_reported_once_and_escalates(self) -> None:
        """One CRITICAL line, one task-failure payload, offline, then exit."""
        # Arrange
        harness = _harness(on_task_failure="exit")
        # configure_logging() replaces root handlers, so caplog cannot see
        # the record; listen on the supervisor's logger directly.
        records = _RecordingHandler()
        supervisor_logger = logging.getLogger("cosalette._supervisor")
        supervisor_logger.addHandler(records)

        def bad_init() -> _Filter:
            msg = "sensor not found"
            raise RuntimeError(msg)

        @harness.app.telemetry("temp", interval=60, init=bad_init)
        async def temp(f: _Filter) -> dict[str, int]:
            return {"value": 1}

        # Act
        try:
            with pytest.raises(TaskSupervisionError) as caught:
                await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            supervisor_logger.removeHandler(records)

        # Assert
        assert caught.value.task_name == "telemetry:temp"
        assert isinstance(caught.value.__cause__, RuntimeError)
        payloads = _payloads(harness, "testapp/temp/error")
        assert [p["details"] for p in payloads] == [
            {"task_failure": True, "task": "telemetry:temp"}
        ]
        assert "offline" in _availability(harness, "temp")
        critical = [
            r
            for r in records.records
            if r.levelname == "CRITICAL" and "died" in r.getMessage()
        ]
        assert len(critical) == 1
        assert critical[0].exc_info is not None

    async def test_deterministic_init_failure_uses_up_the_budget(self) -> None:
        """Every restart re-runs init; the budget then ends the app."""
        # Arrange
        harness = _harness(task_max_restarts=2, task_restart_window=1e9)
        attempts = 0

        def bad_init() -> _Filter:
            nonlocal attempts
            attempts += 1
            msg = "always"
            raise RuntimeError(msg)

        @harness.app.telemetry("temp", interval=60, init=bad_init)
        async def temp(f: _Filter) -> dict[str, int]:
            return {"value": 1}

        # Act
        with pytest.raises(TaskSupervisionError) as caught:
            await asyncio.wait_for(harness.run(), timeout=5.0)

        # Assert
        assert attempts == 3
        assert caught.value.restart_count == 2
        assert len(_payloads(harness, "testapp/temp/error")) == 3

    async def test_transient_init_failure_recovers_after_restart(self) -> None:
        """An init that succeeds on the restart brings the entity back online."""
        # Arrange
        clock = ManualClock()
        harness = _harness(clock)
        attempts = 0
        calls = 0

        def flaky_init() -> _Filter:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                msg = "not ready yet"
                raise RuntimeError(msg)
            return _Filter()

        @harness.app.telemetry("temp", interval=60, init=flaky_init)
        async def temp(f: _Filter) -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"value": calls}

        run = asyncio.create_task(harness.run())
        try:
            # Act
            await clock.settle(until=lambda: attempts == 1)
            await clock.settle()
            await harness.advance_time(1)  # restart backoff
            await harness.advance_time(60)  # deferred first poll
            await clock.settle(until=lambda: _recovered(harness, "temp"))
            recovered = _recovered(harness, "temp")
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert attempts == 2
        assert calls >= 1
        assert recovered is True
        assert len(_payloads(harness, "testapp/temp/error")) == 1


# ---------------------------------------------------------------------------
# Stream failures (cos-pbd8): named availability and root heartbeat status
# ---------------------------------------------------------------------------


class _Reading:
    """Stream item."""


class _Port:
    """Fake ``StreamablePort[_Reading]`` pushing one item at every scan start."""

    def __init__(self) -> None:
        self._put: Callable[[_Reading], None] | None = None

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def start_scan(self) -> None:
        if self._put is not None:
            self._put(_Reading())

    async def stop_scan(self) -> None:
        pass

    def register_callback(self, cb: Callable[[_Reading], None]) -> None:
        self._put = cb


def _stream_harness(clock: Any = None, **app_kwargs: Any) -> tuple[AppHarness, _Port]:
    harness = _harness(clock, run_streams=True, **app_kwargs)
    port = _Port()
    harness.app.adapter(StreamablePort[_Reading], lambda: port)
    return harness, port


def _availability_topics(harness: AppHarness) -> list[str]:
    return [
        topic
        for topic, _, _, _ in harness.published()
        if topic.endswith("availability")
    ]


def _heartbeat_status(harness: AppHarness, entity: str) -> str | None:
    """*entity*'s status in the latest ``{prefix}/status`` heartbeat."""
    beats = [
        json.loads(payload)
        for payload, _, _ in harness.messages_for("testapp/status")
        if payload.startswith("{")
    ]
    if not beats:
        return None
    status = beats[-1]["devices"].get(entity, {}).get("status")
    assert status is None or isinstance(status, str)
    return status


class TestStreamFailure:
    """A stream crash is logged, published and reflected in availability.

    A named stream owns ``{prefix}/{stream}/availability``: online at
    startup, offline on a crash, online again at the first item after the
    restart and offline at shutdown.  A root stream is heartbeat-only and
    never touches ``{prefix}/availability`` (ADR-081 amendment).

    Technique: Specification-based Testing and State Transition Testing.
    """

    @pytest.mark.parametrize("root", [False, True], ids=["named", "root"])
    async def test_crash_publishes_payload_and_marks_stream(self, root: bool) -> None:
        """Exit policy: one payload, escalation; only a named stream goes offline.

        Technique: Equivalence Partitioning — named stream vs root stream,
        whose payload goes to ``{prefix}/error`` only.
        """
        # Arrange
        harness, _ = _stream_harness(on_task_failure="exit")

        async def feed(stream: Stream[_Reading]) -> AsyncIterator[None]:
            msg = "decoder crashed"
            raise RuntimeError(msg)
            yield  # pragma: no cover

        harness.app.stream(None if root else "feed")(feed)

        # Act
        with pytest.raises(TaskSupervisionError) as caught:
            await asyncio.wait_for(harness.run(), timeout=5.0)

        # Assert
        assert caught.value.task_name == "stream:feed"
        payloads = _payloads(harness, "testapp/error")
        assert [p["details"] for p in payloads] == [
            {"task_failure": True, "task": "stream:feed"}
        ]
        per_stream = _payloads(harness, "testapp/feed/error")
        assert len(per_stream) == (0 if root else 1)
        assert "testapp/availability" not in _availability_topics(harness)
        if root:
            assert _availability_topics(harness) == []
        else:
            assert _availability(harness, "feed")[:2] == [
                "online",
                "offline",
            ]

    async def test_offline_on_crash_then_online_at_first_item(self) -> None:
        """Heartbeat and availability follow the crash and the recovery.

        Technique: State Transition Testing — running (online, ok) ->
        failed (offline, error) -> restarted -> first item (online, ok) ->
        shutdown (offline).
        """
        # Arrange
        clock = ManualClock()
        harness, _ = _stream_harness(clock, heartbeat_interval=0.5)
        attempts = 0
        items = 0

        @harness.app.stream("feed")
        async def feed(stream: Stream[_Reading]) -> AsyncIterator[None]:
            nonlocal attempts, items
            attempts += 1
            if attempts == 1:
                msg = "decoder crashed"
                raise RuntimeError(msg)
            async for _ in stream:
                items += 1
                yield

        run = asyncio.create_task(harness.run())
        try:
            # Act
            await clock.settle(
                until=lambda: bool(harness.messages_for("testapp/feed/error"))
            )
            await harness.advance_time(0.5)  # heartbeat before the restart
            status_after_crash = _heartbeat_status(harness, "feed")
            await harness.advance_time(0.5)  # restart backoff (1 s)
            await clock.settle(until=lambda: items >= 1)
            await harness.advance_time(0.5)  # next heartbeat
            status_after_recovery = _heartbeat_status(harness, "feed")
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert attempts == 2
        assert status_after_crash == "error"
        assert status_after_recovery == "ok"
        assert _availability(harness, "feed") == [
            "online",
            "offline",
            "online",
            "offline",
        ]

    async def test_root_stream_never_touches_app_availability(self) -> None:
        """A root stream's crash and recovery stay in the heartbeat.

        Technique: Equivalence Partitioning — the root-stream partition.
        """
        # Arrange
        clock = ManualClock()
        harness, _ = _stream_harness(clock, heartbeat_interval=0.5)
        attempts = 0
        items = 0

        @harness.app.stream()
        async def feed(stream: Stream[_Reading]) -> AsyncIterator[None]:
            nonlocal attempts, items
            attempts += 1
            if attempts == 1:
                msg = "decoder crashed"
                raise RuntimeError(msg)
            async for _ in stream:
                items += 1
                yield

        run = asyncio.create_task(harness.run())
        try:
            # Act
            await clock.settle(
                until=lambda: bool(harness.messages_for("testapp/error"))
            )
            await harness.advance_time(0.5)
            status_after_crash = _heartbeat_status(harness, "feed")
            await harness.advance_time(0.5)
            await clock.settle(until=lambda: items >= 1)
            await harness.advance_time(0.5)
            status_after_recovery = _heartbeat_status(harness, "feed")
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert status_after_crash == "error"
        assert status_after_recovery == "ok"
        assert _availability_topics(harness) == []

    async def test_deterministic_crash_uses_up_the_budget(self) -> None:
        """The restart budget and escalation are unchanged for streams."""
        # Arrange
        harness, _ = _stream_harness(task_max_restarts=2, task_restart_window=1e9)
        attempts = 0

        @harness.app.stream("feed")
        async def feed(stream: Stream[_Reading]) -> AsyncIterator[None]:
            nonlocal attempts
            attempts += 1
            msg = "always"
            raise RuntimeError(msg)
            yield  # pragma: no cover

        # Act
        with pytest.raises(TaskSupervisionError) as caught:
            await asyncio.wait_for(harness.run(), timeout=5.0)

        # Assert
        assert attempts == 3
        assert caught.value.restart_count == 2
        assert len(_payloads(harness, "testapp/feed/error")) == 3
        # Offline once on the first crash; later crashes are not transitions.
        assert _availability(harness, "feed") == [
            "online",
            "offline",
            "offline",
        ]


# ---------------------------------------------------------------------------
# Production MQTT client loops
# ---------------------------------------------------------------------------


class _ScriptedMqttClient(MqttClient):
    """A real :class:`MqttClient` whose background loops never dial a broker.

    The lifecycle supervises the loops only for the production client, so
    the test needs an ``MqttClient`` instance.  Each loop waits for its own
    event and then raises, letting tests prove either task returned by
    :meth:`MqttClient.supervised_tasks` is supervised.
    """

    def __init__(self, *, protocol_version: str = "3.1.1") -> None:
        super().__init__(settings=MqttSettings(protocol_version=protocol_version))
        self.connection_die = asyncio.Event()
        self.refresh_die = asyncio.Event()

    @override
    async def _connection_loop(self) -> None:
        await self.connection_die.wait()
        msg = "connection loop broke"
        raise RuntimeError(msg)

    @override
    async def _refresh_loop(self) -> None:
        await self.refresh_die.wait()
        msg = "refresh loop broke"
        raise RuntimeError(msg)


def _mqtt_harness(mqtt: MqttClient, **app_kwargs: Any) -> AppHarness:
    """Harness running the app on *mqtt* without a first-connect wait."""
    return AppHarness(
        app=App(
            name="testapp",
            version="1.0.0",
            store=None,
            startup_connect_timeout=None,
            **app_kwargs,
        ),
        mqtt=mqtt,  # ty: ignore[invalid-argument-type]
        clock=FakeClock(),
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
    )


class TestMqttLoopSupervision:
    """The production client's loops are framework-internal (ADR-081 §7).

    Technique: Specification-based Testing — a dead loop ends the run with
    :class:`TaskSupervisionError` (exit code 4) even though stopping the
    client re-raises the loop's exception; a normal shutdown cancels the
    loops without a report.
    """

    @pytest.mark.parametrize(
        ("loop", "protocol_version"),
        [("connection", "3.1.1"), ("refresh", "5")],
    )
    async def test_dead_mqtt_loop_raises_task_supervision_error(
        self,
        loop: Literal["connection", "refresh"],
        protocol_version: Literal["3.1.1", "5"],
    ) -> None:
        """A crashed supervised MQTT loop escalates; teardown does not mask it."""
        # Arrange
        mqtt = _ScriptedMqttClient(protocol_version=protocol_version)
        harness = _mqtt_harness(mqtt, on_task_failure="ignore")
        started = asyncio.Event()

        @harness.app.device("dev")
        async def dev(ctx: DeviceContext) -> AsyncIterator[None]:
            started.set()
            await asyncio.Event().wait()
            yield  # noqa: PGH004

        run = asyncio.create_task(harness.run())
        await asyncio.wait_for(started.wait(), timeout=5.0)

        # Act
        getattr(mqtt, f"{loop}_die").set()

        # Assert
        with pytest.raises(TaskSupervisionError) as caught:
            await asyncio.wait_for(run, timeout=5.0)
        assert caught.value.internal is True
        assert caught.value.task_name == f"cosalette-mqtt-{loop}-loop"
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert str(caught.value.__cause__) == f"{loop} loop broke"

    async def test_normal_shutdown_cancels_loops_without_a_report(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Shutdown stops both MQTT loops; the supervisor stays silent."""
        # Arrange
        caplog.set_level(logging.CRITICAL, logger="cosalette")
        mqtt = _ScriptedMqttClient(protocol_version="5")
        harness = _mqtt_harness(mqtt, on_task_failure="exit")
        started = asyncio.Event()

        @harness.app.device("dev")
        async def dev(ctx: DeviceContext) -> AsyncIterator[None]:
            started.set()
            await asyncio.Event().wait()
            yield  # noqa: PGH004

        run = asyncio.create_task(harness.run())
        await asyncio.wait_for(started.wait(), timeout=5.0)
        loops = {
            t.get_name()
            for t in asyncio.all_tasks()
            if t.get_name().startswith("cosalette-mqtt-")
        }

        # Act
        harness.trigger_shutdown()
        await asyncio.wait_for(run, timeout=5.0)

        # Assert
        assert loops == {
            "cosalette-mqtt-connection-loop",
            "cosalette-mqtt-refresh-loop",
        }
        assert mqtt.supervised_tasks() == ()
        assert not [r for r in caplog.records if r.levelno >= logging.CRITICAL]
