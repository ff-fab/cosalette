"""Unit tests for HealthReporter.reannounce().

Test Techniques Used:
    - State-based Testing: reannounce republishes 'online' for tracked devices.
    - Boundary Value Analysis: root device uses flat availability topic.
    - Specification-based Testing: removed devices are NOT re-onlined.
"""

from __future__ import annotations

import pytest

from cosalette._health import HealthReporter
from cosalette.testing import FakeClock, MockMqttClient

pytestmark = pytest.mark.unit

PREFIX = "myapp"


@pytest.fixture
def mock_mqtt() -> MockMqttClient:
    return MockMqttClient()


@pytest.fixture
def reporter(mock_mqtt: MockMqttClient) -> HealthReporter:
    clock = FakeClock()
    clock._time = 0.0
    return HealthReporter(
        mqtt=mock_mqtt,
        topic_prefix=PREFIX,
        version="1.0.0",
        clock=clock,
    )


class TestReannounce:
    """HealthReporter.reannounce() re-publishes 'online' for tracked devices."""

    async def test_reannounce_root_device(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Root device is re-published to <prefix>/availability."""
        await reporter.publish_device_available("root_dev", is_root=True)
        mock_mqtt.reset()

        await reporter.reannounce()

        msgs = mock_mqtt.get_messages_for(f"{PREFIX}/availability")
        assert len(msgs) == 1
        assert msgs[0][0] == "online"

    async def test_reannounce_non_root_device(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Non-root device is re-published to <prefix>/<device>/availability."""
        await reporter.publish_device_available("sensor", is_root=False)
        mock_mqtt.reset()

        await reporter.reannounce()

        msgs = mock_mqtt.get_messages_for(f"{PREFIX}/sensor/availability")
        assert len(msgs) == 1
        assert msgs[0][0] == "online"

    async def test_reannounce_both_root_and_non_root(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Both root and non-root devices are re-announced."""
        await reporter.publish_device_available("root_dev", is_root=True)
        await reporter.publish_device_available("sensor", is_root=False)
        mock_mqtt.reset()

        await reporter.reannounce()

        assert len(mock_mqtt.get_messages_for(f"{PREFIX}/availability")) == 1
        assert len(mock_mqtt.get_messages_for(f"{PREFIX}/sensor/availability")) == 1

    async def test_removed_device_not_reannounced(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """A device made unavailable is NOT re-onlined."""
        await reporter.publish_device_available("sensor", is_root=False)
        await reporter.publish_device_unavailable("sensor", is_root=False)
        mock_mqtt.reset()

        await reporter.reannounce()

        # sensor is marked unavailable — no re-online publish
        assert mock_mqtt.get_messages_for(f"{PREFIX}/sensor/availability") == []

    async def test_reannounce_empty_is_noop(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """reannounce() with no tracked devices publishes nothing."""
        await reporter.reannounce()
        assert mock_mqtt.publish_count == 0

    async def test_status_update_does_not_resurrect_unavailable_device(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """A status update after going offline must not re-online on reconnect.

        Technique: State Transition Testing — this is the zombie-resurrection
        path ADR-077 had to close before availability could be published
        automatically. The telemetry runner records ``set_device_status(name,
        "error")`` on a failed poll, which re-adds the device to ``_devices``.
        While unavailability was encoded as *absence* from that same dict, the
        next MQTT reconnect republished ``"online"`` for a device that was
        still failing — and did so again on every reconnect, so the symptom
        was intermittent and looked like a broker fault.
        """
        await reporter.publish_device_available("sensor", is_root=False)
        await reporter.publish_device_unavailable("sensor", is_root=False)
        reporter.set_device_status("sensor", "error")  # what the runner does
        mock_mqtt.reset()

        await reporter.reannounce()

        assert mock_mqtt.get_messages_for(f"{PREFIX}/sensor/availability") == []

    async def test_unavailable_device_kept_in_heartbeat_roster(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Going offline must not drop the device from {prefix}/status.

        Technique: Specification-based Testing — the status blob is where an
        operator reads *why* a device failed, so the entry has to survive
        exactly the transition that makes them look (ADR-077).
        """
        import json

        await reporter.publish_device_available("sensor", is_root=False)
        await reporter.publish_device_unavailable("sensor", is_root=False)
        reporter.set_device_status("sensor", "error")
        mock_mqtt.reset()

        await reporter.publish_heartbeat()

        blob, _retain, _qos = mock_mqtt.get_messages_for(f"{PREFIX}/status")[0]
        payload = json.loads(blob)
        assert payload["devices"]["sensor"] == {"status": "error"}

    async def test_recovery_clears_the_unavailable_mark(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Once back online, a reconnect reannounces the device again."""
        await reporter.publish_device_unavailable("sensor", is_root=False)
        await reporter.publish_device_available("sensor", is_root=False)
        mock_mqtt.reset()

        await reporter.reannounce()

        published = mock_mqtt.get_messages_for(f"{PREFIX}/sensor/availability")
        assert [payload for payload, *_ in published] == ["online"]

    async def test_unavailable_root_device_keeps_flat_topic(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """A root device stays root-addressed after going offline.

        Technique: Boundary Value Analysis — root identity is held in a
        separate set consulted by ``_availability_topic()``, so dropping it on
        the way offline would send the shutdown ``"offline"`` to the per-device
        topic instead of the flat one.
        """
        await reporter.publish_device_available("app", is_root=True)
        await reporter.publish_device_unavailable("app", is_root=True)
        mock_mqtt.reset()

        await reporter.shutdown()

        published = mock_mqtt.get_messages_for(f"{PREFIX}/availability")
        assert "offline" in [payload for payload, *_ in published]
