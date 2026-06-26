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
        """A device made unavailable (removed from tracking) is NOT re-onlined."""
        await reporter.publish_device_available("sensor", is_root=False)
        await reporter.publish_device_unavailable("sensor", is_root=False)
        mock_mqtt.reset()

        await reporter.reannounce()

        # sensor was removed from tracking — no re-online publish
        assert mock_mqtt.get_messages_for(f"{PREFIX}/sensor/availability") == []

    async def test_reannounce_empty_is_noop(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """reannounce() with no tracked devices publishes nothing."""
        await reporter.reannounce()
        assert mock_mqtt.publish_count == 0
