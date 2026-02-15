"""Tests for cosalette._health — health reporting and availability.

Test Techniques Used:
    - Specification-based Testing: DeviceStatus, HeartbeatPayload construction
    - State-based Testing: HealthReporter publishes to correct topics
    - Mock-based Isolation: MockMqttClient records publish calls
    - Clock Injection: Deterministic uptime via FakeClock
    - Exception Safety: _safe_publish swallows and logs errors
"""

from __future__ import annotations

import json
import logging
from dataclasses import FrozenInstanceError

import pytest

from cosalette._health import (
    DeviceStatus,
    HealthReporter,
    HeartbeatPayload,
    build_will_config,
)
from cosalette._mqtt import MockMqttClient
from tests.fixtures.clock import FakeClock

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_clock() -> FakeClock:
    """FakeClock starting at time 0."""
    return FakeClock()


@pytest.fixture
def mock_mqtt() -> MockMqttClient:
    """Fresh MockMqttClient for each test."""
    return MockMqttClient()


@pytest.fixture
def reporter(mock_mqtt: MockMqttClient, fake_clock: FakeClock) -> HealthReporter:
    """HealthReporter wired to MockMqttClient and FakeClock."""
    fake_clock._time = 100.0
    return HealthReporter(
        mqtt=mock_mqtt,
        topic_prefix="myapp",
        version="1.0.0",
        clock=fake_clock,
    )


# ---------------------------------------------------------------------------
# DeviceStatus
# ---------------------------------------------------------------------------


class TestDeviceStatus:
    """DeviceStatus value object tests.

    Technique: Specification-based Testing — verifying defaults,
    custom values, serialisation, and immutability.
    """

    async def test_default_status_is_ok(self) -> None:
        """Default DeviceStatus has status 'ok'."""
        ds = DeviceStatus()
        assert ds.status == "ok"

    async def test_custom_status(self) -> None:
        """DeviceStatus accepts a custom status string."""
        ds = DeviceStatus(status="degraded")
        assert ds.status == "degraded"

    async def test_to_dict_returns_status_mapping(self) -> None:
        """to_dict() returns a dict with the status key."""
        ds = DeviceStatus(status="ok")
        assert ds.to_dict() == {"status": "ok"}

    async def test_frozen_immutable(self) -> None:
        """Frozen dataclass raises on attribute assignment."""
        ds = DeviceStatus()
        with pytest.raises(FrozenInstanceError):
            ds.status = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# HeartbeatPayload
# ---------------------------------------------------------------------------


class TestHeartbeatPayload:
    """HeartbeatPayload value object tests.

    Technique: Specification-based Testing — verifying construction,
    JSON serialisation, defaults, and nested device serialisation.
    """

    async def test_construction_with_all_fields(self) -> None:
        """HeartbeatPayload stores all fields correctly."""
        devices = {"blind": DeviceStatus(status="ok")}
        hb = HeartbeatPayload(
            status="online",
            uptime_s=3600.0,
            version="1.0.0",
            devices=devices,
        )
        assert hb.status == "online"
        assert hb.uptime_s == 3600.0
        assert hb.version == "1.0.0"
        assert hb.devices == devices

    async def test_to_json_produces_valid_json(self) -> None:
        """to_json() returns valid JSON with expected top-level keys."""
        hb = HeartbeatPayload(
            status="online",
            uptime_s=60.0,
            version="2.0.0",
        )
        parsed = json.loads(hb.to_json())
        assert parsed["status"] == "online"
        assert parsed["uptime_s"] == 60.0
        assert parsed["version"] == "2.0.0"
        assert parsed["devices"] == {}

    async def test_default_devices_empty_dict(self) -> None:
        """devices defaults to an empty dict when not provided."""
        hb = HeartbeatPayload(
            status="online",
            uptime_s=0.0,
            version="0.1.0",
        )
        assert hb.devices == {}

    async def test_frozen_immutable(self) -> None:
        """Frozen dataclass raises on attribute assignment."""
        hb = HeartbeatPayload(status="online", uptime_s=0.0, version="1.0.0")
        with pytest.raises(FrozenInstanceError):
            hb.status = "changed"  # type: ignore[misc]

    async def test_devices_serialised_to_nested_json(self) -> None:
        """Devices are serialised as nested dicts in JSON output."""
        devices = {
            "blind": DeviceStatus(status="ok"),
            "window": DeviceStatus(status="degraded"),
        }
        hb = HeartbeatPayload(
            status="online",
            uptime_s=120.0,
            version="1.0.0",
            devices=devices,
        )
        parsed = json.loads(hb.to_json())
        assert parsed["devices"] == {
            "blind": {"status": "ok"},
            "window": {"status": "degraded"},
        }


# ---------------------------------------------------------------------------
# build_will_config
# ---------------------------------------------------------------------------


class TestBuildWillConfig:
    """build_will_config() function tests.

    Technique: Specification-based Testing — verifying WillConfig
    construction from a topic prefix.
    """

    async def test_creates_will_config_with_correct_topic(self) -> None:
        """Will topic is {prefix}/status."""
        wc = build_will_config("myapp")
        assert wc.topic == "myapp/status"

    async def test_will_config_payload_is_offline(self) -> None:
        """Will payload is the string 'offline'."""
        wc = build_will_config("myapp")
        assert wc.payload == "offline"

    async def test_will_config_retained_and_qos_1(self) -> None:
        """Will is retained with QoS 1."""
        wc = build_will_config("myapp")
        assert wc.retain is True
        assert wc.qos == 1


# ---------------------------------------------------------------------------
# HealthReporter
# ---------------------------------------------------------------------------


class TestHealthReporter:
    """HealthReporter service tests.

    Technique: State-based Testing with MockMqttClient — verify
    published topics, payloads, QoS, and retain flags.
    """

    async def test_publish_device_available_sends_online(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """publish_device_available sends 'online' to availability topic."""
        await reporter.publish_device_available("blind")
        topic, payload, _, _ = mock_mqtt.published[0]
        assert topic == "myapp/blind/availability"
        assert payload == "online"

    async def test_publish_device_available_retained_qos_1(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Device availability publishes are retained with QoS 1."""
        await reporter.publish_device_available("blind")
        _, _, retain, qos = mock_mqtt.published[0]
        assert retain is True
        assert qos == 1

    async def test_publish_device_available_sets_device_status(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """publish_device_available registers device as 'ok'."""
        await reporter.publish_device_available("sensor")
        assert reporter._devices["sensor"] == DeviceStatus(status="ok")

    async def test_publish_device_unavailable_sends_offline(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """publish_device_unavailable sends 'offline' to availability topic."""
        await reporter.publish_device_unavailable("blind")
        topic, payload, _, _ = mock_mqtt.published[0]
        assert topic == "myapp/blind/availability"
        assert payload == "offline"

    async def test_publish_device_unavailable_removes_device(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """publish_device_unavailable removes the device from tracking."""
        reporter.set_device_status("blind")
        await reporter.publish_device_unavailable("blind")
        assert "blind" not in reporter._devices

    async def test_publish_heartbeat_sends_json_to_status_topic(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """publish_heartbeat publishes JSON to {prefix}/status."""
        await reporter.publish_heartbeat()
        topic, payload_str, _, _ = mock_mqtt.published[0]
        assert topic == "myapp/status"
        parsed = json.loads(payload_str)
        assert parsed["status"] == "online"

    async def test_publish_heartbeat_includes_uptime(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Heartbeat uptime reflects elapsed time from clock."""
        fake_clock._time = 150.0  # started at 100.0
        await reporter.publish_heartbeat()
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert parsed["uptime_s"] == 50.0

    async def test_publish_heartbeat_includes_version(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Heartbeat includes the configured version string."""
        await reporter.publish_heartbeat()
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert parsed["version"] == "1.0.0"

    async def test_publish_heartbeat_includes_devices(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Heartbeat includes all tracked devices."""
        reporter.set_device_status("blind", "ok")
        reporter.set_device_status("window", "degraded")
        await reporter.publish_heartbeat()
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert parsed["devices"] == {
            "blind": {"status": "ok"},
            "window": {"status": "degraded"},
        }

    async def test_publish_heartbeat_retained(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Heartbeat publishes are retained with QoS 1."""
        await reporter.publish_heartbeat()
        _, _, retain, qos = mock_mqtt.published[0]
        assert retain is True
        assert qos == 1

    async def test_set_device_status_updates_internal_state(
        self,
        reporter: HealthReporter,
    ) -> None:
        """set_device_status adds or updates a device entry."""
        reporter.set_device_status("sensor", "ok")
        assert reporter._devices["sensor"] == DeviceStatus(status="ok")
        reporter.set_device_status("sensor", "degraded")
        assert reporter._devices["sensor"] == DeviceStatus(status="degraded")

    async def test_remove_device_removes_from_tracking(
        self,
        reporter: HealthReporter,
    ) -> None:
        """remove_device removes a device; no error if absent."""
        reporter.set_device_status("sensor")
        reporter.remove_device("sensor")
        assert "sensor" not in reporter._devices
        # Removing a non-existent device is a no-op
        reporter.remove_device("nonexistent")


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    """Shutdown behaviour tests.

    Technique: State-based Testing — verifying that shutdown publishes
    offline for all tracked devices, the app status, and clears state.
    """

    async def test_shutdown_publishes_offline_for_all_devices(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """shutdown() publishes 'offline' for every tracked device."""
        reporter.set_device_status("blind")
        reporter.set_device_status("window")
        await reporter.shutdown()
        device_publishes = [
            (t, p) for t, p, _, _ in mock_mqtt.published if "availability" in t
        ]
        assert ("myapp/blind/availability", "offline") in device_publishes
        assert ("myapp/window/availability", "offline") in device_publishes

    async def test_shutdown_publishes_devices_before_app_status(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Device offline messages are published before the app status offline."""
        reporter.set_device_status("blind")
        reporter.set_device_status("window")
        await reporter.shutdown()
        topics = [t for t, _, _, _ in mock_mqtt.published]
        status_index = topics.index("myapp/status")
        availability_indices = [i for i, t in enumerate(topics) if "availability" in t]
        assert all(i < status_index for i in availability_indices)

    async def test_shutdown_publishes_offline_status(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """shutdown() publishes 'offline' to {prefix}/status."""
        await reporter.shutdown()
        status_publishes = [
            (t, p) for t, p, _, _ in mock_mqtt.published if t == "myapp/status"
        ]
        assert ("myapp/status", "offline") in status_publishes

    async def test_shutdown_clears_device_tracking(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """shutdown() clears internal device tracking."""
        reporter.set_device_status("blind")
        reporter.set_device_status("window")
        await reporter.shutdown()
        assert reporter._devices == {}


# ---------------------------------------------------------------------------
# _safe_publish
# ---------------------------------------------------------------------------


class TestSafePublish:
    """_safe_publish exception-safety tests.

    Technique: Exception Safety — verifying fire-and-forget semantics
    when MQTT publication fails.
    """

    async def test_swallows_mqtt_exception(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """_safe_publish catches exceptions from mqtt.publish."""
        mock_mqtt.raise_on_publish = ConnectionError("broker down")
        reporter = HealthReporter(
            mqtt=mock_mqtt,
            topic_prefix="myapp",
            version="1.0.0",
            clock=fake_clock,
        )
        # Should not raise
        await reporter.publish_heartbeat()

    async def test_logs_swallowed_exception(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_safe_publish logs the swallowed exception at ERROR level."""
        mock_mqtt.raise_on_publish = ConnectionError("broker down")
        reporter = HealthReporter(
            mqtt=mock_mqtt,
            topic_prefix="myapp",
            version="1.0.0",
            clock=fake_clock,
        )
        with caplog.at_level(logging.ERROR, logger="cosalette._health"):
            await reporter.publish_heartbeat()
        assert any("Failed to publish health" in r.message for r in caplog.records)
