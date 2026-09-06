"""Unit tests for testing/_harness.py — the ``run_streams=`` opt-in.

``AppHarness.run()`` used to empty ``app._streams`` unconditionally, so a
``@app.stream`` handler could never run its real lifecycle beside a device.
``run_streams=True`` opts that lifecycle back in, mirroring ``run_periodic=``,
and fails fast when the stream's ``StreamablePort`` adapter is missing.

Test Techniques Used:
- State Transition Testing: the registration list's suppressed → running →
  restored cycle across ``run()``, including the exception exit.
- Decision Table: ``run_streams`` x ``run_periodic`` → which handler classes
  actually start under ``run()``.
- Boundary Value Analysis: the fail-fast boundary — the same missing-adapter
  app raises under ``run_streams=True`` and stays silent under the ``False``
  default, and a deferred ``enabled=`` spec is never pre-judged.
- Error Guessing: a forgotten ``app.adapter(StreamablePort[T], ...)`` is the
  expected mistake; it must raise from ``run()`` rather than hang later in
  ``wait_for_publish_count``.
- Integration Testing: the motivating case — a stream handler arming a
  concurrently running device, both publishing into one ``MockMqttClient``
  under one ``ManualClock``.

See Also:
    ADR-045 — Stateful stream receiver semantics (+ ``run_streams=`` amendment).
    ADR-041 — Periodic tasks, the ``run_periodic=`` precedent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence

import pytest

from cosalette import DeviceContext, DeviceTrigger, EntityNotifier
from cosalette._runners._stream_types import Stream, StreamablePort
from cosalette.testing import AppHarness, ManualClock

pytestmark = pytest.mark.unit

_PROBE_STATE = "testapp/probe/state"
_READINGS_STATE = "testapp/readings/state"
_GADGET_STATE = "testapp/gadget/state"


# =============================================================================
# Helpers
# =============================================================================


class _Reading:
    """Minimal stream item type carrying one value."""

    def __init__(self, value: int) -> None:
        self.value = value


class _Signal:
    """A second stream item type with no registered port.

    Used to prove the fail-fast preflight scans past the first satisfied
    stream to an unsatisfied one — see
    ``test_fail_fast_checks_every_stream_not_just_the_first``.
    """


class _RecordingPort:
    """Fake ``StreamablePort[_Reading]`` that records its lifecycle calls.

    Items passed to the constructor are pushed through the framework-supplied
    callback during ``start_scan()``, which is the same moment the real
    hardware port would start emitting.
    """

    def __init__(self, items: Sequence[_Reading] = ()) -> None:
        self.calls: list[str] = []
        self._items = list(items)
        self._put: Callable[[_Reading], None] | None = None

    async def open(self) -> None:
        self.calls.append("open")

    async def close(self) -> None:
        self.calls.append("close")

    async def start_scan(self) -> None:
        self.calls.append("start_scan")
        if self._put is not None:
            for item in self._items:
                self._put(item)

    async def stop_scan(self) -> None:
        self.calls.append("stop_scan")

    def register_callback(self, cb: Callable[[_Reading], None]) -> None:
        self.calls.append("register_callback")
        self._put = cb


class _FrameBus:
    """Shared state the stream fills and the device it arms drains.

    The jeelink2mqtt shape: a ``@app.state`` factory takes the
    :class:`EntityNotifier`, the stream stores each decoded frame here and
    arms the entity by name, and the device reads the frame back out.
    """

    def __init__(self, notify: EntityNotifier) -> None:
        self.notify = notify
        self.latest: int | None = None


class _BootFailure(RuntimeError):
    """Raised from a state factory to force ``run()`` to exit by exception."""


class _NeverBuilt:
    """Return type of the exploding state factory — never instantiated."""


def _register_port(harness: AppHarness, port: _RecordingPort) -> None:
    """Register *port* itself as the ``StreamablePort[_Reading]`` adapter."""

    def factory() -> _RecordingPort:
        return port

    harness.app.adapter(StreamablePort[_Reading], factory)


def _register_probe_device(harness: AppHarness) -> None:
    """Register a device that publishes once, then parks until shutdown.

    Its single publish is the deterministic "the app has booted" marker used
    instead of a wall-clock sleep.
    """

    @harness.app.device("probe")
    async def probe(ctx: DeviceContext) -> AsyncIterator[None]:
        await ctx.publish_state({"ready": True})
        await harness.shutdown_event.wait()
        yield


def _register_reading_stream(
    harness: AppHarness,
    received: list[int],
    started: asyncio.Event,
    consumed: asyncio.Event | None = None,
) -> None:
    """Register a stream handler that records every item it consumes."""

    @harness.app.stream("readings")
    async def readings(stream: Stream[_Reading]) -> AsyncIterator[None]:
        started.set()
        async for item in stream:
            received.append(item.value)
            if consumed is not None:
                consumed.set()
            yield


async def _run_until_booted_then_shutdown(harness: AppHarness) -> None:
    """Run the harness until the probe device publishes, then shut it down."""
    task = asyncio.create_task(harness.run())
    try:
        await harness.wait_for_publish_count(_PROBE_STATE, 1)
    finally:
        harness.trigger_shutdown()
        await asyncio.wait_for(task, timeout=10.0)


# =============================================================================
# Tests
# =============================================================================


class TestStreamSuppression:
    """``run_streams`` decides whether the stream lifecycle runs at all.

    Technique: State Transition Testing — the registration list moves
    suppressed → running → restored across a single ``run()``.
    """

    async def test_run_streams_false_suppresses_stream_lifecycle(self) -> None:
        """The default harness never opens the port or starts the handler."""
        # Arrange
        harness = AppHarness.create()
        port = _RecordingPort([_Reading(1)])
        _register_port(harness, port)
        received: list[int] = []
        started = asyncio.Event()
        _register_reading_stream(harness, received, started)
        _register_probe_device(harness)

        # Act
        await _run_until_booted_then_shutdown(harness)

        # Assert
        assert port.calls == []
        assert received == []
        assert started.is_set() is False

    async def test_run_streams_true_starts_handler_under_run(self) -> None:
        """``run_streams=True`` opens the port and feeds the handler."""
        # Arrange
        harness = AppHarness.create(run_streams=True)
        port = _RecordingPort([_Reading(42)])
        _register_port(harness, port)
        received: list[int] = []
        started = asyncio.Event()
        consumed = asyncio.Event()
        _register_reading_stream(harness, received, started, consumed)

        # Act
        task = asyncio.create_task(harness.run())
        try:
            await asyncio.wait_for(started.wait(), timeout=5.0)
            await asyncio.wait_for(consumed.wait(), timeout=5.0)
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(task, timeout=10.0)

        # Assert
        assert received == [42]
        assert port.calls[:3] == ["open", "register_callback", "start_scan"]
        assert port.calls[-2:] == ["stop_scan", "close"]

    @pytest.mark.parametrize("run_streams", [False, True], ids=["off", "on"])
    async def test_run_restores_stream_registrations(self, run_streams: bool) -> None:
        """Both paths hand ``app._streams`` back untouched after ``run()``."""
        # Arrange
        harness = AppHarness.create(run_streams=run_streams)
        port = _RecordingPort()
        _register_port(harness, port)
        _register_reading_stream(harness, [], asyncio.Event())
        _register_probe_device(harness)
        registered_before = list(harness.app._streams)

        # Act
        await _run_until_booted_then_shutdown(harness)

        # Assert
        assert list(harness.app._streams) == registered_before
        assert harness.app._streams[0].name == "readings"

    @pytest.mark.parametrize("run_streams", [False, True], ids=["off", "on"])
    async def test_run_restores_stream_registrations_on_exception(
        self, run_streams: bool
    ) -> None:
        """A failing bootstrap still restores the registration list.

        Technique: Error Guessing — the ``finally`` restore must survive an
        exception escaping ``_run_async``, not just the happy path.
        """
        # Arrange
        harness = AppHarness.create(run_streams=run_streams)
        port = _RecordingPort()
        _register_port(harness, port)
        _register_reading_stream(harness, [], asyncio.Event())
        registered_before = list(harness.app._streams)

        @harness.app.state
        def exploding_state() -> _NeverBuilt:
            raise _BootFailure("bootstrap exploded")

        # Act
        with pytest.raises(_BootFailure, match="bootstrap exploded"):
            await harness.run()

        # Assert
        assert list(harness.app._streams) == registered_before


class TestRunStreamsDecisionTable:
    """``run_streams`` x ``run_periodic`` → which handler classes start.

    Technique: Decision Table — the two independent opt-ins are covered in
    all four combinations, pinning that neither knob leaks into the other.
    """

    @pytest.mark.parametrize(
        ("run_streams", "run_periodic", "expect_stream", "expect_periodic"),
        [
            (False, False, False, False),
            (False, True, False, True),
            (True, False, True, False),
            (True, True, True, True),
        ],
        ids=["neither", "periodic_only", "streams_only", "both"],
    )
    async def test_opt_ins_start_only_their_own_handlers(
        self,
        run_streams: bool,
        run_periodic: bool,
        expect_stream: bool,
        expect_periodic: bool,
    ) -> None:
        """Each opt-in starts its own handler class and nothing else."""
        # Arrange
        harness = AppHarness.create(run_streams=run_streams, run_periodic=run_periodic)
        port = _RecordingPort([_Reading(7)])
        _register_port(harness, port)
        stream_ran = asyncio.Event()
        periodic_ran = asyncio.Event()
        _register_probe_device(harness)

        @harness.app.stream("readings")
        async def readings(stream: Stream[_Reading]) -> AsyncIterator[None]:
            async for _item in stream:
                stream_ran.set()
                yield

        @harness.app.periodic("counter", interval=0.001)
        async def counter() -> None:
            periodic_ran.set()

        # Act
        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_PROBE_STATE, 1)
            if expect_stream:
                await asyncio.wait_for(stream_ran.wait(), timeout=5.0)
            if expect_periodic:
                await asyncio.wait_for(periodic_ran.wait(), timeout=5.0)
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(task, timeout=10.0)

        # Assert
        assert stream_ran.is_set() is expect_stream
        assert periodic_ran.is_set() is expect_periodic


class TestMissingStreamPortFailFast:
    """A missing ``StreamablePort`` adapter must raise, not hang.

    Technique: Error Guessing + Boundary Value Analysis — the forgotten fake
    port is the expected mistake, and the ``run_streams`` flag is the exact
    boundary between raising and staying silent.
    """

    async def test_run_raises_when_stream_port_is_not_registered(self) -> None:
        """``run_streams=True`` without a matching adapter raises from ``run()``."""
        # Arrange
        harness = AppHarness.create(run_streams=True)
        _register_reading_stream(harness, [], asyncio.Event())

        # Act & Assert
        with pytest.raises(
            RuntimeError, match=r"Stream 'readings' requires StreamablePort\[_Reading\]"
        ) as excinfo:
            await harness.run()

        # The message carries the actionable remediation hint, not just the
        # diagnosis — a regression dropping it would otherwise pass silently.
        assert "app.adapter(StreamablePort[_Reading]" in str(excinfo.value)

    async def test_missing_port_raises_before_the_app_connects(self) -> None:
        """The check is eager — nothing is published before it raises."""
        # Arrange
        harness = AppHarness.create(run_streams=True)
        _register_reading_stream(harness, [], asyncio.Event())

        # Act
        with pytest.raises(RuntimeError, match="no matching adapter was registered"):
            await harness.run()

        # Assert
        assert harness.mqtt.published == []

    async def test_missing_port_is_ignored_when_streams_are_suppressed(self) -> None:
        """The default ``run_streams=False`` still tolerates a missing adapter."""
        # Arrange
        harness = AppHarness.create()
        _register_reading_stream(harness, [], asyncio.Event())
        _register_probe_device(harness)

        # Act
        await _run_until_booted_then_shutdown(harness)

        # Assert
        assert harness.messages_for(_PROBE_STATE)[0][0] == '{"ready":true}'
        assert harness.messages_for(_READINGS_STATE) == []

    async def test_deferred_enabled_stream_is_not_pre_judged(self) -> None:
        """A stream disabled by a deferred ``enabled=`` spec never fails fast.

        Technique: Boundary Value Analysis — the callable spec is resolved
        during bootstrap, so the eager check must skip it rather than raise
        for a stream that is about to be dropped.
        """
        # Arrange
        harness = AppHarness.create(run_streams=True)
        _register_probe_device(harness)

        @harness.app.stream("readings", enabled=lambda _settings: False)
        async def readings(stream: Stream[_Reading]) -> AsyncIterator[None]:
            async for _item in stream:
                yield

        # Act
        await _run_until_booted_then_shutdown(harness)

        # Assert
        assert [reg.name for reg in harness.app._streams] == ["readings"]
        assert harness.messages_for(_PROBE_STATE)[0][0] == '{"ready":true}'

    async def test_static_disabled_stream_is_never_registered(self) -> None:
        """A static ``enabled=False`` stream is dropped at decoration time.

        Technique: Boundary Value Analysis — the preflight's callable guard
        need not special-case a static ``False`` because such a stream never
        enters ``app._streams`` at all, so ``run_streams=True`` cannot fail
        fast on its (absent) port.
        """
        # Arrange
        harness = AppHarness.create(run_streams=True)
        _register_probe_device(harness)

        @harness.app.stream("readings", enabled=False)
        async def readings(stream: Stream[_Reading]) -> AsyncIterator[None]:
            async for _item in stream:
                yield

        # Assert — decoration dropped it, so the preflight has nothing to judge
        assert harness.app._streams == []

        # Act — no port registered, yet run() does not fail fast
        await _run_until_booted_then_shutdown(harness)

        # Assert
        assert harness.messages_for(_PROBE_STATE)[0][0] == '{"ready":true}'

    async def test_fail_fast_checks_every_stream_not_just_the_first(self) -> None:
        """With two streams, the one missing its port still raises.

        Technique: Boundary Value Analysis — the preflight loop must visit
        past the first (satisfied) registration to reach the unsatisfied one.
        """
        # Arrange
        harness = AppHarness.create(run_streams=True)
        _register_port(harness, _RecordingPort())  # satisfies StreamablePort[_Reading]
        _register_reading_stream(harness, [], asyncio.Event())

        @harness.app.stream("signals")
        async def signals(stream: Stream[_Signal]) -> AsyncIterator[None]:
            async for _item in stream:
                yield

        # Act & Assert — the second stream's port is the missing one
        with pytest.raises(
            RuntimeError, match=r"Stream 'signals' requires StreamablePort\[_Signal\]"
        ):
            await harness.run()

    async def test_fail_fast_leaves_registration_lists_untouched(self) -> None:
        """A preflight raise mutates neither ``_streams`` nor ``_periodic``.

        Technique: State Transition Testing — the fail-fast exit happens
        before ``run()`` empties ``app._periodic``, so both lists must equal
        their pre-``run()`` snapshots. Regression guard against a raise that
        skips the ``finally`` restore and leaks empty state into a later test.
        """
        # Arrange
        harness = AppHarness.create(run_streams=True)
        _register_reading_stream(harness, [], asyncio.Event())

        @harness.app.periodic("counter", interval=0.001)
        async def counter() -> None: ...

        streams_before = list(harness.app._streams)
        periodic_before = list(harness.app._periodic)

        # Act
        with pytest.raises(RuntimeError, match="no matching adapter was registered"):
            await harness.run()

        # Assert
        assert list(harness.app._streams) == streams_before
        assert list(harness.app._periodic) == periodic_before


class TestStreamArmsDeviceIntegration:
    """The motivating shape: a stream arms a device and both publish.

    Technique: Integration Testing — the jeelink2mqtt case from the source
    proposal, end to end through one app: the port pushes a reading, the
    stream handler stores it and arms the device by name, and the device
    drains it.  Both halves share the harness's single ``MockMqttClient``
    and single :class:`ManualClock`, which is what makes "published within
    one virtual tick" assertable at all — no ``advance_time`` is called,
    so ``clock.now()`` must still read its starting value.
    """

    async def test_stream_armed_device_publishes_within_one_virtual_tick(
        self,
    ) -> None:
        """A stream-armed device publishes the reading without time moving."""
        # Arrange
        clock = ManualClock()
        harness = AppHarness.create(clock=clock, run_streams=True)
        _register_port(harness, _RecordingPort([_Reading(21)]))

        @harness.app.state
        def frame_bus(notify: EntityNotifier) -> _FrameBus:
            return _FrameBus(notify)

        @harness.app.stream("readings")
        async def readings(
            stream: Stream[_Reading], ctx: DeviceContext, bus: _FrameBus
        ) -> AsyncIterator[None]:
            async for item in stream:
                bus.latest = item.value
                await ctx.publish_state({"seen": item.value})
                bus.notify("gadget")
                yield

        @harness.app.device("gadget", triggerable="local")
        async def gadget(
            ctx: DeviceContext, trigger: DeviceTrigger, bus: _FrameBus
        ) -> AsyncIterator[None]:
            while True:
                payload = await trigger.wait()
                await ctx.publish_state({"value": bus.latest, "source": payload.source})
                yield

        # Act
        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_GADGET_STATE, 1)
        finally:
            harness.trigger_shutdown()
            await asyncio.wait_for(task, timeout=10.0)

        # Assert
        assert harness.messages_for(_READINGS_STATE)[0][0] == '{"seen":21}'
        assert (
            harness.messages_for(_GADGET_STATE)[0][0] == '{"value":21,"source":"local"}'
        )
        topics = [topic for topic, _payload, _retain, _qos in harness.mqtt.published]
        assert topics.index(_READINGS_STATE) < topics.index(_GADGET_STATE)
        assert clock.now() == 0.0
