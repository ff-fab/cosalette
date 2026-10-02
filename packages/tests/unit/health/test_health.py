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
from typing import Any

import pytest

from cosalette._health import (
    DeviceStatus,
    HealthReporter,
    HeartbeatPayload,
    build_will_config,
)
from cosalette._mqtt import MqttNotConnectedError
from cosalette.testing import FakeClock, MockMqttClient

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# mock_mqtt and fake_clock fixtures provided by cosalette.testing._plugin


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
            ds.status = "changed"  # ty: ignore[invalid-assignment]


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
            uptime_s=3600,
            version="1.0.0",
            devices=devices,
        )
        assert hb.status == "online"
        assert hb.uptime_s == 3600
        assert hb.version == "1.0.0"
        assert hb.devices == devices

    async def test_to_json_produces_valid_json(self) -> None:
        """to_json() returns valid JSON with expected top-level keys."""
        hb = HeartbeatPayload(
            status="online",
            uptime_s=60,
            version="2.0.0",
        )
        parsed = json.loads(hb.to_json())
        assert parsed["status"] == "online"
        assert parsed["uptime_s"] == 60
        assert parsed["version"] == "2.0.0"
        assert parsed["devices"] == {}

    async def test_default_devices_empty_dict(self) -> None:
        """devices defaults to an empty dict when not provided."""
        hb = HeartbeatPayload(
            status="online",
            uptime_s=0,
            version="0.1.0",
        )
        assert hb.devices == {}

    async def test_frozen_immutable(self) -> None:
        """Frozen dataclass raises on attribute assignment."""
        hb = HeartbeatPayload(status="online", uptime_s=0, version="1.0.0")
        with pytest.raises(FrozenInstanceError):
            hb.status = "changed"  # ty: ignore[invalid-assignment]

    async def test_devices_serialised_to_nested_json(self) -> None:
        """Devices are serialised as nested dicts in JSON output."""
        devices = {
            "blind": DeviceStatus(status="ok"),
            "window": DeviceStatus(status="degraded"),
        }
        hb = HeartbeatPayload(
            status="online",
            uptime_s=120,
            version="1.0.0",
            devices=devices,
        )
        parsed = json.loads(hb.to_json())
        assert parsed["devices"] == {
            "blind": {"status": "ok"},
            "window": {"status": "degraded"},
        }

    async def test_to_json_omits_version_when_disabled(self) -> None:
        """to_json(include_version=False) omits the key entirely (F-DP6).

        Technique: Specification-based Testing — the CVE-fingerprinting
        mitigation must remove the key entirely, not blank it.
        """
        hb = HeartbeatPayload(status="online", uptime_s=1, version="9.9.9")
        parsed = json.loads(hb.to_json(include_version=False))
        assert "version" not in parsed
        assert parsed["status"] == "online"


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

    async def test_publish_device_unavailable_marks_without_untracking(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """The device stays in the roster, marked unavailable (ADR-077).

        Unavailability used to be encoded as absence from ``_devices``, which
        also drives the heartbeat roster — so a failing device vanished from
        ``{prefix}/status`` exactly when an operator needed to read why.
        """
        reporter.set_device_status("blind")
        await reporter.publish_device_unavailable("blind")

        assert "blind" in reporter._devices
        assert reporter._devices["blind"].status == "unavailable"
        assert "blind" in reporter._unavailable

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
        assert parsed["uptime_s"] == 50

    async def test_publish_heartbeat_uptime_is_whole_seconds(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Heartbeat uptime is published as a truncated JSON integer."""
        fake_clock._time = 100.0 + 88564.06941311399  # started at 100.0
        await reporter.publish_heartbeat()
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert parsed["uptime_s"] == 88564
        assert isinstance(parsed["uptime_s"], int)

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

    async def test_include_version_false_omits_field(
        self, mock_mqtt: MockMqttClient, fake_clock: FakeClock
    ) -> None:
        """include_version=False omits 'version' from heartbeat JSON (F-DP6).

        Technique: Specification-based Testing — the CVE-fingerprinting
        mitigation must remove the key entirely, not blank it.
        """
        reporter = HealthReporter(
            mqtt=mock_mqtt,
            topic_prefix="myapp",
            version="1.0.0",
            clock=fake_clock,
            include_version=False,
        )
        await reporter.publish_heartbeat()
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert "version" not in parsed
        assert parsed["status"] == "online"
        assert "uptime_s" in parsed

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

    async def test_logs_not_connected_at_debug(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Expected broker outages do not produce health error logs."""
        mock_mqtt.raise_on_publish = MqttNotConnectedError("broker disconnected")
        reporter = HealthReporter(
            mqtt=mock_mqtt,
            topic_prefix="myapp",
            version="1.0.0",
            clock=fake_clock,
        )

        with caplog.at_level(logging.DEBUG, logger="cosalette._health"):
            await reporter.publish_heartbeat()

        assert "MQTT not connected, dropped health publish" in caplog.text
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)


# ---------------------------------------------------------------------------
# TestRootDeviceAvailability — root-level device availability topics
# ---------------------------------------------------------------------------


class TestRootDeviceAvailability:
    """Tests for root-level device availability topics.

    Root devices publish availability to ``{prefix}/availability``
    instead of ``{prefix}/{device}/availability``.

    Technique: State-based Testing — MockMqttClient records
    published topics for assertion.
    """

    async def test_root_device_publishes_to_prefix_availability(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Root device availability is {prefix}/availability."""
        await reporter.publish_device_available("sensor", is_root=True)
        topic, payload, _, _ = mock_mqtt.published[0]
        assert topic == "myapp/availability"
        assert payload == "online"

    async def test_root_device_tracked_in_root_set(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Root device is added to _root_devices set."""
        await reporter.publish_device_available("sensor", is_root=True)
        assert "sensor" in reporter._root_devices

    async def test_root_unavailable_publishes_to_prefix_availability(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Root device unavailability is {prefix}/availability."""
        await reporter.publish_device_unavailable("sensor", is_root=True)
        topic, payload, _, _ = mock_mqtt.published[0]
        assert topic == "myapp/availability"
        assert payload == "offline"

    async def test_shutdown_root_device_correct_topic(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Shutdown publishes offline to {prefix}/availability for root devices."""
        await reporter.publish_device_available("sensor", is_root=True)
        mock_mqtt.published.clear()

        await reporter.shutdown()
        availability_publishes = [
            (t, p) for t, p, _, _ in mock_mqtt.published if "availability" in t
        ]
        assert ("myapp/availability", "offline") in availability_publishes

    async def test_shutdown_mixed_root_and_named(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Shutdown handles both root and named devices correctly."""
        await reporter.publish_device_available("root_dev", is_root=True)
        await reporter.publish_device_available("blind")
        mock_mqtt.published.clear()

        await reporter.shutdown()
        availability_publishes = [
            (t, p) for t, p, _, _ in mock_mqtt.published if "availability" in t
        ]
        assert ("myapp/availability", "offline") in availability_publishes
        assert ("myapp/blind/availability", "offline") in availability_publishes

    async def test_shutdown_clears_root_devices(
        self,
        reporter: HealthReporter,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Shutdown clears _root_devices set."""
        await reporter.publish_device_available("sensor", is_root=True)
        await reporter.shutdown()
        assert reporter._root_devices == set()


# ---------------------------------------------------------------------------
# Stream failure status (ADR-081, cos-pbd8)
# ---------------------------------------------------------------------------


class TestStreamFailureStatus:
    """A stream's task failure lives in the heartbeat only.

    Technique: State Transition Testing — unmarked -> error -> ok; the
    stream never joins the availability roster.
    """

    async def _heartbeat_devices(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> dict[str, Any]:
        mock_mqtt.reset()
        await reporter.publish_heartbeat()
        ((payload, _, _),) = mock_mqtt.get_messages_for("myapp/status")
        return json.loads(payload)["devices"]

    async def test_heartbeat_shows_error_then_ok(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """mark -> ``error`` in the heartbeat; clear -> ``ok``."""
        # Act
        reporter.mark_stream_failed("feed")
        after_failure = await self._heartbeat_devices(reporter, mock_mqtt)
        reporter.clear_stream_failure("feed")
        after_recovery = await self._heartbeat_devices(reporter, mock_mqtt)

        # Assert
        assert after_failure == {"feed": {"status": "error"}}
        assert after_recovery == {"feed": {"status": "ok"}}

    async def test_clear_is_noop_when_never_marked(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """A stream that never failed does not appear in the heartbeat."""
        # Act
        reporter.clear_stream_failure("feed")
        devices = await self._heartbeat_devices(reporter, mock_mqtt)

        # Assert
        assert devices == {}

    async def test_never_publishes_availability(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """Failure, reannounce and shutdown publish no stream availability."""
        # Act
        reporter.mark_stream_failed("feed")
        await reporter.reannounce()
        reporter.clear_stream_failure("feed")
        await reporter.reannounce()
        await reporter.shutdown()

        # Assert
        topics = [topic for topic, _, _, _ in mock_mqtt.published]
        assert not [t for t in topics if t.endswith("availability")]
        assert "feed" not in reporter._devices  # noqa: SLF001


# ---------------------------------------------------------------------------
# clear_task_failure (ADR-081)
# ---------------------------------------------------------------------------


class TestClearTaskFailure:
    """Recovery of the task supervisor's availability mark.

    Technique: State Transition Testing — marked -> cleared, with and
    without another unavailable source (ADR-077).
    """

    async def test_noop_when_supervisor_never_marked(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """Without a supervisor mark nothing is published."""
        # Act
        await reporter.clear_task_failure("radon")

        # Assert
        assert mock_mqtt.published == []

    async def test_clears_mark_and_error_status(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """The entity is republished online and its status returns to ok."""
        # Arrange
        await reporter.publish_device_unavailable("radon", source="supervisor")
        reporter.set_device_status("radon", "error")
        mock_mqtt.reset()

        # Act
        await reporter.clear_task_failure("radon")

        # Assert
        assert mock_mqtt.get_messages_for("myapp/radon/availability") == [
            ("online", True, 1)
        ]
        assert reporter.is_unavailable("radon") is False
        assert reporter._devices["radon"].status == "ok"  # noqa: SLF001

    async def test_other_source_keeps_entity_offline(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """Another source still holding the entity offline wins."""
        # Arrange
        await reporter.publish_device_unavailable("radon", source="supervisor")
        await reporter.publish_device_unavailable("radon", source="adapter")
        reporter.set_device_status("radon", "error")
        mock_mqtt.reset()

        # Act
        await reporter.clear_task_failure("radon")

        # Assert
        assert mock_mqtt.get_messages_for("myapp/radon/availability") == []
        assert reporter.is_unavailable("radon", source="supervisor") is False
        assert reporter.is_unavailable("radon", source="adapter") is True
        assert reporter._devices["radon"].status == "unavailable"  # noqa: SLF001

    async def test_root_entity_uses_root_topic(
        self, reporter: HealthReporter, mock_mqtt: MockMqttClient
    ) -> None:
        """A root entity's recovery goes to {prefix}/availability."""
        # Arrange
        await reporter.publish_device_unavailable(
            "hub", is_root=True, source="supervisor"
        )
        mock_mqtt.reset()

        # Act
        await reporter.clear_task_failure("hub", is_root=True)

        # Assert
        assert mock_mqtt.get_messages_for("myapp/availability") == [("online", True, 1)]
