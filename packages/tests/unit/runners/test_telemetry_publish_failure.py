"""Regression tests for MQTT failures after a successful telemetry read."""

from __future__ import annotations

import asyncio
from typing import Any, override

import pytest
from aiomqtt import MqttError

from cosalette import OnChange
from cosalette.testing import AppHarness, ManualClock, MockMqttClient

pytestmark = pytest.mark.unit


class _FailPublish(MockMqttClient):
    failure: Exception | None = None
    failed_attempts: int = 0
    fail_topic: str = ""
    fail_all_after_state: bool = False

    @override
    async def publish(
        self,
        topic: str,
        payload: str | dict[str, Any],
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        if self.failure is not None and (
            topic == self.fail_topic
            or (self.fail_all_after_state and self.failed_attempts > 0)
        ):
            self.failed_attempts += 1
            raise self.failure
        await super().publish(topic, payload, retain=retain, qos=qos)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,fail_all_after_state",
    [
        (RuntimeError("MqttClient is not connected"), True),
        (MqttError("Operation timed out"), False),
    ],
)
async def test_single_telemetry_retries_unchanged_state_after_publish_failure(
    failure: Exception,
    fail_all_after_state: bool,
) -> None:
    clock = ManualClock()
    harness = AppHarness.create(clock=clock)
    mqtt = _FailPublish()
    mqtt.fail_topic = "testapp/sensor/state"
    mqtt.fail_all_after_state = fail_all_after_state
    mqtt.failure = failure
    harness.mqtt = mqtt
    reads = asyncio.Queue[None]()

    @harness.app.telemetry("sensor", interval=60, publish=OnChange())
    async def sensor() -> dict[str, int]:
        reads.put_nowait(None)
        return {"value": 1}

    task = asyncio.create_task(harness.run())
    try:
        await reads.get()
        await clock.settle()
        assert mqtt.failed_attempts >= 1
        assert harness.messages_for("testapp/sensor/state") == []
        if not fail_all_after_state:
            assert harness.messages_for("testapp/error")
        assert not task.done()

        mqtt.failure = None
        await harness.advance_time(60)
        await harness.wait_for_publish_count("testapp/sensor/state", 1)
        assert len(harness.messages_for("testapp/sensor/state")) == 1
    finally:
        harness.trigger_shutdown()
        await task


@pytest.mark.asyncio
async def test_group_publish_failure_does_not_stop_sibling_or_next_tick() -> None:
    clock = ManualClock()
    harness = AppHarness.create(clock=clock)
    mqtt = _FailPublish()
    mqtt.fail_topic = "testapp/broken/state"
    mqtt.failure = MqttError("Operation timed out")
    harness.mqtt = mqtt

    @harness.app.telemetry("broken", interval=60, group="sensors")
    async def broken() -> dict[str, int]:
        return {"value": 1}

    @harness.app.telemetry("healthy", interval=60, group="sensors")
    async def healthy() -> dict[str, int]:
        return {"value": 2}

    task = asyncio.create_task(harness.run())
    try:
        await harness.wait_for_publish_count("testapp/healthy/state", 1)
        assert mqtt.failed_attempts == 1
        assert not task.done()

        mqtt.failure = None
        await harness.advance_time(60)
        await harness.advance_time(60)
        await harness.wait_for_publish_count("testapp/broken/state", 1)
        await harness.wait_for_publish_count("testapp/healthy/state", 2)
    finally:
        harness.trigger_shutdown()
        await task
