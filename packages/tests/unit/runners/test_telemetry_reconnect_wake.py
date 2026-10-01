"""Tests for re-running telemetry whose state an MQTT outage deferred (cos-wjil).

A telemetry state publish that hits ``MqttNotConnectedError`` used to wait for
the entity's next interval tick.  The framework now arms a reconnect wake, so
the entity reads and publishes again right after the broker reconnects.

Test Techniques Used:
    - State Transition Testing: connected -> outage (deferred) -> reconnect
    - Equivalence Partitioning: ungrouped / triggerable / grouped entities
    - Error Guessing: a reconnect that lands before the deferral is recorded
    - Mock-based Isolation: connect-aware MockMqttClient subclass, ManualClock
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, override

import pytest

from cosalette import MqttNotConnectedError, TriggerPayload
from cosalette._mqtt import ConnectCallback
from cosalette._runners._telemetry_types import _ReconnectWake
from cosalette.testing import AppHarness, ManualClock, MockMqttClient

pytestmark = pytest.mark.unit

INTERVAL = 60


@dataclass
class _OutageMqtt(MockMqttClient):
    """Connect-aware mock whose ``/state`` publishes fail while disconnected."""

    connected: bool = False
    connect_callbacks: list[ConnectCallback] = field(default_factory=list)

    def add_connect_callback(self, callback: ConnectCallback) -> None:
        self.connect_callbacks.append(callback)

    async def connect(self) -> None:
        """Mark connected and run the connect callbacks, like a CONNACK."""
        self.connected = True
        for callback in list(self.connect_callbacks):
            await callback()

    @override
    async def publish(
        self,
        topic: str,
        payload: str | dict[str, Any],
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        if not self.connected and topic.endswith("/state"):
            raise MqttNotConnectedError("MqttClient is not connected")
        await super().publish(topic, payload, retain=retain, qos=qos)


class _Run:
    """Run a harness app through connect, outage and reconnect."""

    def __init__(self) -> None:
        self.clock = ManualClock()
        self.harness = AppHarness.create(clock=self.clock)
        self.mqtt = _OutageMqtt()
        self.harness.mqtt = self.mqtt
        self.reads: list[str] = []

    async def start(self) -> asyncio.Task[None]:
        task = asyncio.create_task(self.harness.run())
        await self.clock.settle(until=lambda: bool(self.mqtt.connect_callbacks))
        await self.mqtt.connect()
        return task

    async def outage_tick(self, expected_reads: int) -> None:
        """Drop the broker, then let one interval tick fail its publish."""
        await self.clock.settle()  # let the next sleep register first
        self.mqtt.connected = False
        await self.harness.advance_time(INTERVAL)
        await self.clock.settle(until=lambda: len(self.reads) >= expected_reads)
        await self.clock.settle()

    def state(self, name: str) -> list[tuple[str, bool, int]]:
        return self.harness.messages_for(f"testapp/{name}/state")


async def test_ungrouped_entity_reruns_on_reconnect() -> None:
    """The deferred reading is retaken right after reconnect, not a tick later.

    Technique: State Transition Testing — connected, outage, reconnect.
    """
    # Arrange
    run = _Run()

    @run.harness.app.telemetry("sensor", interval=INTERVAL)
    async def sensor() -> dict[str, int]:
        run.reads.append("sensor")
        return {"n": len(run.reads)}

    task = await run.start()
    try:
        await run.clock.settle(until=lambda: len(run.state("sensor")) == 1)
        await run.outage_tick(expected_reads=2)
        now = run.clock.now()

        # Act
        await run.mqtt.connect()
        await run.clock.settle(until=lambda: len(run.state("sensor")) == 2)
    finally:
        run.harness.trigger_shutdown()
        await task

    # Assert
    assert run.reads == ["sensor"] * 3
    assert run.clock.now() == now
    assert run.state("sensor")[-1][0] == '{"n":3}'


async def test_triggerable_entity_reruns_as_scheduled_on_reconnect() -> None:
    """A triggerable entity wakes through its slot with a scheduled payload.

    Technique: Equivalence Partitioning — the triggerable partition.
    """
    # Arrange
    run = _Run()
    sources: list[str] = []

    @run.harness.app.telemetry("sensor", interval=INTERVAL, triggerable=True)
    async def sensor(trigger: TriggerPayload) -> dict[str, int]:
        run.reads.append("sensor")
        sources.append(trigger.source)
        return {"n": len(run.reads)}

    task = await run.start()
    try:
        await run.clock.settle(until=lambda: len(run.state("sensor")) == 1)
        await run.outage_tick(expected_reads=2)

        # Act
        await run.mqtt.connect()
        await run.clock.settle(until=lambda: len(run.state("sensor")) == 2)
    finally:
        run.harness.trigger_shutdown()
        await task

    # Assert
    assert sources == ["scheduled"] * 3


async def test_group_members_rerun_on_reconnect_without_moving_ticks() -> None:
    """Deferred group members run out of cycle; their ticks stay on the epoch.

    Technique: Equivalence Partitioning — the coalescing-group partition.
    """
    # Arrange
    run = _Run()

    @run.harness.app.telemetry("a", interval=INTERVAL, group="bus")
    async def a() -> dict[str, int]:
        run.reads.append("a")
        return {"n": len(run.reads)}

    @run.harness.app.telemetry("b", interval=INTERVAL, group="bus")
    async def b() -> dict[str, int]:
        run.reads.append("b")
        return {"n": len(run.reads)}

    task = await run.start()
    try:
        await run.clock.settle(until=lambda: len(run.state("b")) == 1)
        await run.outage_tick(expected_reads=4)
        await run.harness.advance_time(INTERVAL / 2)

        # Act — reconnect mid-interval, then reach the next epoch tick.
        await run.mqtt.connect()
        await run.clock.settle(
            until=lambda: len(run.state("a")) == 2 and len(run.state("b")) == 2
        )
        await run.clock.settle()
        await run.harness.advance_time(INTERVAL / 2)
        await run.clock.settle(until=lambda: len(run.reads) == 8)
    finally:
        run.harness.trigger_shutdown()
        await task

    # Assert — one catch-up batch, then the next tick on the group epoch.
    assert run.reads == ["a", "b"] * 4


async def test_reconnect_without_deferral_does_not_rerun() -> None:
    """An entity whose publish landed is not re-run by a later reconnect.

    Technique: State Transition Testing — reconnect from a clean state.
    """
    # Arrange
    run = _Run()

    @run.harness.app.telemetry("sensor", interval=INTERVAL)
    async def sensor() -> dict[str, int]:
        run.reads.append("sensor")
        return {"n": len(run.reads)}

    task = await run.start()
    try:
        await run.clock.settle(until=lambda: len(run.state("sensor")) == 1)

        # Act
        await run.mqtt.connect()
        await run.clock.settle()
    finally:
        run.harness.trigger_shutdown()
        await task

    # Assert
    assert run.reads == ["sensor"]


async def test_deferred_intervals_reuse_one_reconnect_waiter() -> None:
    """Repeated outages retain one waiter and cancel it during shutdown.

    Technique: Resource Leak Testing — several timeout intervals must reuse
    the same pending ``Event.wait`` task until reconnect or teardown.
    """
    run = _Run()

    @run.harness.app.telemetry("sensor", interval=INTERVAL)
    async def sensor() -> dict[str, int]:
        run.reads.append("sensor")
        return {"n": len(run.reads)}

    def reconnect_waiters() -> set[asyncio.Task[Any]]:
        current = asyncio.current_task()
        return {
            task
            for task in asyncio.all_tasks()
            if task is not current
            and not task.done()
            and getattr(task.get_coro(), "__qualname__", "") == "Event.wait"
        }

    task = await run.start()
    try:
        await run.clock.settle(until=lambda: len(run.state("sensor")) == 1)
        baseline_waiters = reconnect_waiters()
        await run.outage_tick(expected_reads=2)
        # Let each interval's owned shutdown waiter finish its cancellation;
        # the reconnect waiter remains pending and is the object under test.
        await asyncio.sleep(0)
        waiter_count = len(reconnect_waiters() - baseline_waiters)
        assert waiter_count

        for expected_reads in (3, 4):
            await run.outage_tick(expected_reads=expected_reads)
            await asyncio.sleep(0)
            assert len(reconnect_waiters() - baseline_waiters) == waiter_count
    finally:
        run.harness.trigger_shutdown()
        await task

    assert len(reconnect_waiters() - baseline_waiters) == 0


class TestReconnectWake:
    """The connect-count bookkeeping behind the wake.

    Technique: State Transition Testing and Error Guessing.
    """

    async def test_connect_wakes_each_deferred_entity_once(self) -> None:
        wake = _ReconnectWake()
        woken: list[str] = []
        wake.defer("a", 0, lambda: woken.append("a"))
        wake.defer("b", 0, lambda: woken.append("b"))

        await wake.on_connect()
        await wake.on_connect()

        assert woken == ["a", "b"]

    async def test_discarded_entity_is_not_woken(self) -> None:
        wake = _ReconnectWake()
        woken: list[str] = []
        wake.defer("a", 0, lambda: woken.append("a"))

        wake.discard("a")
        await wake.on_connect()

        assert woken == []

    async def test_reconnect_before_deferral_wakes_at_once(self) -> None:
        """A cycle that straddled the reconnect must not wait for the next one.

        Technique: Error Guessing — the connect callback ran mid-cycle.
        """
        wake = _ReconnectWake()
        woken: list[str] = []
        since = wake.generation
        await wake.on_connect()

        wake.defer("a", since, lambda: woken.append("a"))

        assert woken == ["a"]
