"""Tests for telemetry publishes that fail because MQTT is not connected.

A missing broker connection is a transport condition, not a handler failure:
the framework must not report it on the error topics, mark health as
``error``, or publish ``"offline"`` — the next tick simply retries.

Test Techniques Used:
    - Decision Table Testing: failure cause x (error publish, status, availability)
    - State Transition Testing: disconnected tick -> connected tick publishes
    - Mock-based Isolation: MockMqttClient subclass, ManualClock via AppHarness
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, override

import pytest

from cosalette import MqttNotConnectedError, OnChange
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._registration import _TelemetryRegistration
from cosalette._runners._telemetry_runner import TelemetryRunner
from cosalette.testing import AppHarness, FakeClock, ManualClock, MockMqttClient

pytestmark = pytest.mark.unit

PREFIX = "myapp"


class _DisconnectedStatePublish(MockMqttClient):
    """Raises ``MqttNotConnectedError`` for one topic while ``connected`` is False."""

    connected: bool = False
    fail_topic: str = ""
    failed_attempts: int = 0

    @override
    async def publish(
        self,
        topic: str,
        payload: str | dict[str, Any],
        *,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        if not self.connected and topic == self.fail_topic:
            self.failed_attempts += 1
            raise MqttNotConnectedError("MqttClient is not connected")
        await super().publish(topic, payload, retain=retain, qos=qos)


async def _never_called() -> dict[str, object] | None:
    """Handler stub: the decision-table tests drive the helper directly."""
    return None  # pragma: no cover


def _reg() -> _TelemetryRegistration:
    return _TelemetryRegistration(
        name="sensor",
        func=_never_called,
        injection_plan=[],
        interval=60,
    )


@pytest.mark.asyncio
async def test_not_connected_state_publish_is_silent_and_retried(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """No error topic, no WARNING+, and the next tick publishes the state.

    Technique: State Transition Testing — disconnected tick, then connected.
    """
    # Arrange
    clock = ManualClock()
    harness = AppHarness.create(clock=clock)
    mqtt = _DisconnectedStatePublish()
    mqtt.fail_topic = "testapp/sensor/state"
    harness.mqtt = mqtt
    reads = asyncio.Queue[None]()

    @harness.app.telemetry("sensor", interval=60, publish=OnChange())
    async def sensor() -> dict[str, int]:
        reads.put_nowait(None)
        return {"value": 1}

    # Act
    task = asyncio.create_task(harness.run())
    try:
        await reads.get()
        await clock.settle()
        failed_while_disconnected = mqtt.failed_attempts
        mqtt.connected = True
        await harness.advance_time(60)
        await harness.wait_for_publish_count("testapp/sensor/state", 1)
    finally:
        harness.trigger_shutdown()
        await task

    # Assert — the app installs its own JSON handler on stderr, so read that.
    records = [
        json.loads(line)
        for line in capfd.readouterr().err.splitlines()
        if line.startswith("{")
    ]
    assert failed_while_disconnected >= 1
    assert harness.messages_for("testapp/error") == []
    assert harness.messages_for("testapp/sensor/error") == []
    noisy = [
        r
        for r in records
        if r["level"] in {"WARNING", "ERROR", "CRITICAL"}
        and r["logger"].startswith(("cosalette._runners", "cosalette._errors"))
    ]
    assert noisy == []
    # Guard against a vacuous check: the INFO-level log stream was captured.
    assert any(r["message"] == "Shutdown complete" for r in records)


class TestHandleTelemetryErrorByCause:
    """Decision table: failure cause -> error publish, status, availability.

    Technique: Decision Table Testing.

    | cause                 | error topic | status  | availability |
    |-----------------------|-------------|---------|--------------|
    | MqttNotConnectedError | none        | kept    | none         |
    | other RuntimeError    | published   | "error" | "offline"    |
    """

    @pytest.fixture
    def mock_mqtt(self) -> MockMqttClient:
        return MockMqttClient()

    @pytest.fixture
    def reporter(self, mock_mqtt: MockMqttClient) -> HealthReporter:
        reporter = HealthReporter(
            mqtt=mock_mqtt, topic_prefix=PREFIX, version="1.0.0", clock=FakeClock()
        )
        reporter.set_device_status("sensor", "ok")
        return reporter

    @pytest.fixture
    def error_publisher(self, mock_mqtt: MockMqttClient) -> ErrorPublisher:
        return ErrorPublisher(mqtt=mock_mqtt, topic_prefix=PREFIX)

    async def test_not_connected_is_a_transport_condition(
        self,
        mock_mqtt: MockMqttClient,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
    ) -> None:
        """Nothing is published, status and dedup state are unchanged."""
        # Act
        last = await TelemetryRunner._handle_telemetry_error(
            _reg(),
            MqttNotConnectedError("MqttClient is not connected"),
            ValueError,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        # Assert
        assert last is ValueError
        assert mock_mqtt.published == []
        assert reporter._devices["sensor"].status == "ok"
        assert not reporter.is_unavailable("sensor")

    async def test_other_runtime_error_is_a_handler_failure(
        self,
        mock_mqtt: MockMqttClient,
        reporter: HealthReporter,
        error_publisher: ErrorPublisher,
    ) -> None:
        """A plain RuntimeError still reports, marks error, and goes offline."""
        # Act
        last = await TelemetryRunner._handle_telemetry_error(
            _reg(),
            RuntimeError("boom"),
            None,
            error_publisher,
            reporter,
            mark_unavailable=True,
        )

        # Assert
        assert last is RuntimeError
        assert mock_mqtt.get_messages_for(f"{PREFIX}/error")
        assert reporter._devices["sensor"].status == "error"
        assert reporter.is_unavailable("sensor")
