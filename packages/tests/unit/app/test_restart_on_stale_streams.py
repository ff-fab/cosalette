"""restart_on_stale for streams with ``stale_after=`` (ADR-084 amendment).

A stream that declares ``stale_after=`` and goes stale requests a restart of
the restartable ``StreamablePort[T]`` adapter behind its ``Stream[T]``
parameter, with the telemetry semantics: no failure threshold, the
``max_restarts`` budget, the cooldown and one request per stale episode.

Test Techniques Used:
    - State Transition Testing: fresh -> stale -> restarted -> fresh -> stale
    - Equivalence Partitioning: stream with and without ``stale_after``,
      ``restart_on_stale`` on and off
    - Boundary Value Analysis: ``max_restarts=1`` exhausted by the first
      stale episode; the cooldown just before and after it elapses
    - Mock-based Isolation: ManualClock, MockMqttClient
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


class _QuietRadio:
    """Health check passes, but no frame arrives until reset() (ADR-084)."""

    def __init__(self) -> None:
        self.resets = 0
        self.opens = 0
        self._put: Callable[[_Frame], None] | None = None

    async def health_check(self) -> bool:
        return True

    async def reset(self) -> None:
        self.resets += 1

    async def open(self) -> None:
        self.opens += 1

    async def close(self) -> None:
        pass

    async def start_scan(self) -> None:
        pass

    async def stop_scan(self) -> None:
        pass

    def register_callback(self, cb: Callable[[_Frame], None]) -> None:
        self._put = cb

    def push(self) -> None:
        assert self._put is not None
        self._put(_Frame())


def _app(
    port: _QuietRadio,
    *,
    stale_after: float | None = 20.0,
    restart_on_stale: bool = True,
    restart_cooldown: float = 1.0,
    max_restarts: int = 3,
    name: str | None = "radio",
    heartbeat_interval: float = 60.0,
) -> App:
    app = App(
        name=PREFIX,
        version="1.0.0",
        store=None,
        health_check_interval=10.0,
        restart_cooldown=restart_cooldown,
        max_restarts=max_restarts,
        restart_on_stale=restart_on_stale,
        heartbeat_interval=heartbeat_interval,
    )
    app.adapter(StreamablePort[_Frame], lambda: port)

    @app.stream(name, stale_after=stale_after)
    async def radio(stream: Stream[_Frame]) -> AsyncIterator[None]:
        async for _ in stream:
            yield

    return app


async def _start(app: App) -> tuple[AppHarness, asyncio.Task[None]]:
    harness = AppHarness(
        app=app,
        mqtt=MockMqttClient(),
        clock=ManualClock(),
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
        run_streams=True,
    )
    task = asyncio.create_task(harness.run())
    await harness.wait_for_publish_count(f"{PREFIX}/status", 1)
    return harness, task


async def _advance(harness: AppHarness, seconds: float, step: float = 10.0) -> None:
    for _ in range(round(seconds / step)):
        await harness.advance_time(step)


async def _stop(harness: AppHarness, task: asyncio.Task[None]) -> None:
    harness.trigger_shutdown()
    await task


class TestStaleStreamRestartsItsAdapter:
    """A stale stream with stale_after= restarts its StreamablePort."""

    async def test_root_stream_restarts_without_publishing_availability(self) -> None:
        """Technique: State Transition Testing and Equivalence Partitioning."""
        # Arrange
        port = _QuietRadio()
        harness, task = await _start(_app(port, name=None, heartbeat_interval=1.0))

        # Act: observe staleness and recovery before shutdown.
        await _advance(harness, 60.0)
        stale = json.loads(harness.messages_for(f"{PREFIX}/status")[-1][0])
        port.push()
        await harness.advance_time(1.0)
        recovered = json.loads(harness.messages_for(f"{PREFIX}/status")[-1][0])
        await _stop(harness, task)

        # Assert
        assert (port.resets, port.opens) == (1, 2)
        assert stale["devices"]["radio"]["status"] == "stale"
        assert recovered["devices"]["radio"]["status"] == "ok"
        assert harness.messages_for(f"{PREFIX}/availability") == []
        assert harness.messages_for(f"{PREFIX}/radio/availability") == []

    async def test_stale_stream_restarts_its_injected_adapter_too(self) -> None:
        """Technique: State Transition Testing and Condition Coverage."""
        # Arrange: the stream has both a source and an injected dependency.
        port = _QuietRadio()
        auxiliary = _QuietRadio()
        app = App(
            name=PREFIX,
            store=None,
            health_check_interval=10.0,
            restart_cooldown=1.0,
            restart_on_stale=True,
        )
        app.adapter(StreamablePort[_Frame], lambda: port)
        app.adapter(_QuietRadio, lambda: auxiliary)
        handled: list[_Frame] = []

        @app.stream("radio", stale_after=20.0)
        async def radio(
            stream: Stream[_Frame], dependency: _QuietRadio
        ) -> AsyncIterator[None]:
            assert await dependency.health_check() is True
            async for frame in stream:
                handled.append(frame)
                yield

        harness, task = await _start(app)

        # Act
        await _advance(harness, 60.0)
        port.push()
        await harness.advance_time(0.0)
        await _stop(harness, task)

        # Assert: both restarts recreate the stream and it delivers data again.
        assert (port.resets, auxiliary.resets) == (1, 1)
        assert port.opens == 3
        assert len(handled) == 1

    @pytest.mark.parametrize(
        ("stale_after", "restart_on_stale", "expected_resets"),
        [(20.0, True, 1), (20.0, False, 0), (None, True, 0)],
        ids=["opted-in", "restart-off", "no-stale_after"],
    )
    async def test_restart_only_for_a_tracked_stream_with_the_option_on(
        self,
        stale_after: float | None,
        restart_on_stale: bool,
        expected_resets: int,
    ) -> None:
        # Arrange: the radio never delivers a frame
        port = _QuietRadio()
        app = _app(port, stale_after=stale_after, restart_on_stale=restart_on_stale)
        harness, task = await _start(app)

        # Act
        await _advance(harness, 80.0)
        await _stop(harness, task)

        # Assert
        assert port.resets == expected_resets

    async def test_restart_reopens_the_stream_and_logs_a_stream_reason(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Arrange
        port = _QuietRadio()
        harness, task = await _start(_app(port))

        # Act
        await _advance(harness, 40.0)
        await _stop(harness, task)
        logs = capsys.readouterr().err

        # Assert: the restart re-created the stream task, which re-opened it
        assert port.resets == 1
        assert port.opens == 2
        assert "after stale stream 'radio'" in logs
        assert "stale telemetry" not in logs

    async def test_once_per_stale_episode(self) -> None:
        # Arrange
        port = _QuietRadio()
        harness, task = await _start(_app(port))
        await _advance(harness, 40.0)
        first_episode = port.resets

        # Act: still stale for a long time, then fresh, then stale again
        await _advance(harness, 100.0)
        still_stale = port.resets
        port.push()
        await harness.advance_time(0.0)
        await _advance(harness, 40.0)
        await _stop(harness, task)

        # Assert
        assert (first_episode, still_stale, port.resets) == (1, 1, 2)

    async def test_cooldown_precedes_the_reset(self) -> None:
        # Arrange: a 15 s cooldown makes the wait observable
        port = _QuietRadio()
        harness, task = await _start(_app(port, restart_cooldown=15.0))
        await _advance(harness, 20.0)  # stale transition and restart request

        # Act
        await harness.advance_time(10.0)
        during_cooldown = port.resets
        await harness.advance_time(10.0)
        after_cooldown = port.resets
        await _stop(harness, task)

        # Assert
        assert (during_cooldown, after_cooldown) == (0, 1)

    async def test_max_restarts_exhaustion_stops_further_restarts(self) -> None:
        # Arrange: max_restarts=1, so the first episode spends the budget
        port = _QuietRadio()
        harness, task = await _start(_app(port, max_restarts=1))
        await _advance(harness, 40.0)

        # Act: two more stale episodes
        for _ in range(2):
            port.push()
            await harness.advance_time(0.0)
            await _advance(harness, 40.0)
        await _stop(harness, task)

        # Assert
        assert port.resets == 1
