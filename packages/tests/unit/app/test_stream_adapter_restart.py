"""Streams in the ADR-029 adapter-to-device map (cos-kg37).

A stream depends on the ``StreamablePort[T]`` adapter behind its
``Stream[T]`` parameter.  When that adapter fails its health check the
stream goes offline, and an adapter restart cancels the stream task and
re-creates it, so the handler re-opens the restarted port instead of
staying bound to the stale session.

Test Techniques Used:
    - State Transition Testing: healthy -> unhealthy -> restarted -> healthy,
      seen through the port's open/close calls and the stream availability.
    - Equivalence Partitioning: named vs root stream.
    - Error Guessing: a supervisor restart of the stream before the adapter
      restart must not leave two live stream handlers.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import pytest

from cosalette import App
from cosalette._runners._stream_types import Stream, StreamablePort
from cosalette.testing import AppHarness, ManualClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit

PREFIX = "testapp"


class _Frame:
    """Stream item type."""


class _RadioPort:
    """Health-checkable StreamablePort[_Frame] restarted through reset()."""

    def __init__(self) -> None:
        self.healthy = True
        self.opens = 0
        self.closes = 0
        self.resets = 0
        self._put: Callable[[_Frame], None] | None = None

    async def health_check(self) -> bool:
        return self.healthy

    async def reset(self) -> None:
        self.resets += 1
        self.healthy = True

    async def open(self) -> None:
        self.opens += 1

    async def close(self) -> None:
        self.closes += 1

    async def start_scan(self) -> None:
        pass

    async def stop_scan(self) -> None:
        pass

    def register_callback(self, cb: Callable[[_Frame], None]) -> None:
        self._put = cb

    def push(self) -> None:
        assert self._put is not None
        self._put(_Frame())


def _app(port: _RadioPort) -> App:
    app = App(
        name=PREFIX,
        version="1.0.0",
        store=None,
        health_check_interval=10.0,
        restart_after_failures=1,
        restart_cooldown=1.0,
    )
    app.adapter(StreamablePort[_Frame], lambda: port)
    return app


def _harness(app: App) -> AppHarness:
    return AppHarness(
        app=app,
        mqtt=MockMqttClient(),
        clock=ManualClock(),
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
        run_streams=True,
    )


async def _fail_one_probe(harness: AppHarness, port: _RadioPort) -> None:
    """Fail one health check, then let the restart and next probe run."""
    port.healthy = False
    for _ in range(3):
        await harness.advance_time(10.0)


class TestStreamAdapterRestart:
    """An adapter restart re-creates the streams that use the adapter."""

    @pytest.mark.parametrize("name", ["radio", None])
    async def test_restart_reopens_the_stream_port(self, name: str | None) -> None:
        # Arrange
        port = _RadioPort()
        app = _app(port)
        handled: list[int] = []
        starts: list[int] = []

        @app.stream(name)
        async def radio(stream: Stream[_Frame]) -> AsyncIterator[None]:
            starts.append(1)
            async for _ in stream:
                handled.append(len(starts))
                yield

        harness = _harness(app)
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count(f"{PREFIX}/status", 1)

        # Act
        await _fail_one_probe(harness, port)
        port.push()
        await harness.advance_time(0.0)
        harness.trigger_shutdown()
        await task

        # Assert: the old session was closed and a new task opened it again
        assert port.resets == 1
        assert port.opens == 2
        assert port.closes == 2
        assert starts == [1, 1]
        assert handled == [2]

    async def test_named_stream_goes_offline_and_back_online(self) -> None:
        # Arrange
        port = _RadioPort()
        app = _app(port)

        @app.stream("radio")
        async def radio(stream: Stream[_Frame]) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        harness = _harness(app)
        task = asyncio.create_task(harness.run())
        topic = f"{PREFIX}/radio/availability"
        await harness.wait_for_publish_count(topic, 1)

        # Act
        await _fail_one_probe(harness, port)
        harness.trigger_shutdown()
        await task

        # Assert: online -> offline (health) -> online (recovered) -> shutdown
        payloads = [p for p, _, _ in harness.messages_for(topic)]
        assert payloads == ["online", "offline", "online", "offline"]

    @pytest.mark.parametrize("name", ["radio", None], ids=["named", "root"])
    async def test_stream_health_failure_and_recovery(self, name: str | None) -> None:
        # Arrange
        port = _RadioPort()
        app = App(
            name=PREFIX,
            version="1.0.0",
            store=None,
            heartbeat_interval=1.0,
            health_check_interval=10.0,
            restart_after_failures=0,
        )
        app.adapter(StreamablePort[_Frame], lambda: port)

        @app.stream(name)
        async def radio(stream: Stream[_Frame]) -> AsyncIterator[None]:
            async for _ in stream:
                yield

        harness = _harness(app)
        task = asyncio.create_task(harness.run())
        topic = f"{PREFIX}/radio/availability"
        await harness.wait_for_publish_count(f"{PREFIX}/status", 1)
        clock = harness.clock
        assert isinstance(clock, ManualClock)
        await clock.settle(until=lambda: port.opens == 1)

        # Act
        port.healthy = False
        await harness.advance_time(10.0)
        await harness.advance_time(1.0)  # heartbeat after the failed probe settles
        failed = json.loads(harness.messages_for(f"{PREFIX}/status")[-1][0])
        port.healthy = True
        await harness.advance_time(10.0)
        await harness.advance_time(1.0)  # heartbeat after the recovery settles
        recovered = json.loads(harness.messages_for(f"{PREFIX}/status")[-1][0])
        harness.trigger_shutdown()
        await task

        # Assert: root health changes the heartbeat without an availability topic.
        assert failed["devices"]["radio"]["status"] == "unavailable"
        assert recovered["devices"]["radio"]["status"] == "ok"
        assert harness.messages_for(f"{PREFIX}/availability") == []
        payloads = [p for p, _, _ in harness.messages_for(topic)]
        expected = [] if name is None else ["online", "offline", "online", "offline"]
        assert payloads == expected

    async def test_supervisor_restart_then_adapter_restart_keeps_one_handler(
        self,
    ) -> None:
        # Arrange: the stream crashes once, so the supervisor re-creates it
        # before the adapter restart has to cancel the re-created task.
        port = _RadioPort()
        app = _app(port)
        live: list[int] = []
        crashed: list[bool] = []

        @app.stream("radio")
        async def radio(stream: Stream[_Frame]) -> AsyncIterator[None]:
            live.append(1)
            try:
                async for _ in stream:
                    if not crashed:
                        crashed.append(True)
                        msg = "decoder glitch"
                        raise RuntimeError(msg)
                    yield
            finally:
                live.pop()

        harness = _harness(app)
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count(f"{PREFIX}/status", 1)
        port.push()
        clock = harness.clock
        assert isinstance(clock, ManualClock)
        await clock.settle(until=lambda: bool(harness.messages_for(f"{PREFIX}/error")))
        await harness.advance_time(1.0)  # supervisor restart backoff
        await clock.settle(until=lambda: port.opens == 2)

        # Act
        await _fail_one_probe(harness, port)
        live_after_restart = len(live)
        harness.trigger_shutdown()
        await task

        # Assert
        assert port.resets == 1
        assert port.opens == 3
        assert live_after_restart == 1
