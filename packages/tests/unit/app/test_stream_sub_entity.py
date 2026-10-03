"""``ctx.sub_entity()`` inside a stream handler (ADR-031, cos-cpbq).

Sub-entity availability stays outside the health reporter for streams, as
it does for devices: the context manager publishes ``"online"`` on enter
and ``"offline"`` on exit, and the sub-entity never joins the reporter's
roster, so the heartbeat, reconnect re-announce and shutdown do not touch
it.  A crash still takes it offline, because the handler leaves the
``async with`` block.

Test Techniques Used:
    - Equivalence Partitioning: named vs root stream (topic base).
    - State Transition Testing: entered (online) -> crash (offline) ->
      re-created stream enters again (online) -> shutdown (offline).
    - Specification-based Testing: topics, payloads and heartbeat roster.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import pytest

from cosalette import App
from cosalette._context import DeviceContext
from cosalette._runners._stream_types import Stream, StreamablePort
from cosalette.testing import AppHarness, ManualClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit

PREFIX = "testapp"


class _Frame:
    """Stream item type."""


class _Port:
    """StreamablePort[_Frame] whose items the test pushes by hand."""

    def __init__(self) -> None:
        self._put: Callable[[_Frame], None] | None = None

    async def open(self) -> None:
        pass

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


def _harness(port: _Port) -> AppHarness:
    app = App(name=PREFIX, version="1.0.0", store=None, heartbeat_interval=1.0)
    app.adapter(StreamablePort[_Frame], lambda: port)
    return AppHarness(
        app=app,
        mqtt=MockMqttClient(),
        clock=ManualClock(),
        settings=make_settings(),
        shutdown_event=asyncio.Event(),
        run_streams=True,
    )


def _payloads(harness: AppHarness, topic: str) -> list[str]:
    return [p for p, _, _ in harness.messages_for(topic)]


class TestStreamSubEntity:
    """Stream sub-entities manage their own availability (ADR-031)."""

    @pytest.mark.parametrize(
        ("name", "base"), [("radio", f"{PREFIX}/radio"), (None, PREFIX)]
    )
    async def test_publishes_own_availability_outside_the_reporter(
        self, name: str | None, base: str
    ) -> None:
        # Arrange
        port = _Port()
        harness = _harness(port)

        @harness.app.stream(name)
        async def radio(
            stream: Stream[_Frame], ctx: DeviceContext
        ) -> AsyncIterator[None]:
            async with ctx.sub_entity("battery") as battery:
                async for _ in stream:
                    await battery.publish_state({"level": 90})
                    yield

        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count(f"{base}/battery/availability", 1)

        # Act
        port.push()
        await harness.wait_for_publish_count(f"{base}/battery/state", 1)
        await harness.advance_time(1.0)  # one heartbeat while it is online
        beats = [
            json.loads(p)
            for p in _payloads(harness, f"{PREFIX}/status")
            if p.startswith("{")
        ]
        harness.trigger_shutdown()
        await task

        # Assert
        assert _payloads(harness, f"{base}/battery/availability") == [
            "online",
            "offline",
        ]
        assert _payloads(harness, f"{base}/battery/state")[-1] == ""
        rosters = [set(beat["devices"]) for beat in beats]
        assert rosters
        assert all(not any("battery" in d for d in r) for r in rosters)

    async def test_crash_takes_sub_entity_offline_until_reentered(self) -> None:
        # Arrange
        port = _Port()
        harness = _harness(port)
        attempts = 0

        @harness.app.stream("radio")
        async def radio(
            stream: Stream[_Frame], ctx: DeviceContext
        ) -> AsyncIterator[None]:
            nonlocal attempts
            attempts += 1
            async with ctx.sub_entity("battery"):
                async for _ in stream:
                    if attempts == 1:
                        msg = "decoder glitch"
                        raise RuntimeError(msg)
                    yield

        topic = f"{PREFIX}/radio/battery/availability"
        task = asyncio.create_task(harness.run())
        await harness.wait_for_publish_count(topic, 1)

        # Act
        port.push()
        clock = harness.clock
        assert isinstance(clock, ManualClock)
        await clock.settle(until=lambda: bool(harness.messages_for(f"{PREFIX}/error")))
        after_crash = _payloads(harness, topic)
        await harness.advance_time(1.0)  # supervisor restart backoff
        await harness.wait_for_publish_count(topic, 3)
        harness.trigger_shutdown()
        await task

        # Assert
        assert attempts == 2
        assert after_crash == ["online", "offline"]
        assert _payloads(harness, topic) == ["online", "offline", "online", "offline"]
