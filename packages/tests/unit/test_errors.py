"""Tests for cosalette._errors — structured error publication.

Test Techniques Used:
    - Specification-based Testing: ErrorPayload construction and serialisation
    - State-based Testing: ErrorPublisher publication to correct topics
    - Mock-based Isolation: MockMqttClient records publish calls
    - Clock Injection: Deterministic timestamps via injected clock callable
    - Exception Safety: _safe_publish swallows and logs errors
"""

from __future__ import annotations

import json
import logging
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from cosalette._errors import ErrorPayload, ErrorPublisher, build_error_payload
from cosalette.testing import MockMqttClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXED_DT = datetime(2026, 2, 14, 12, 0, 0, tzinfo=UTC)
FIXED_ISO = FIXED_DT.isoformat()


def _fixed_clock() -> datetime:
    """Return a deterministic datetime for testing."""
    return FIXED_DT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# mock_mqtt fixture provided by cosalette.testing._plugin


@pytest.fixture
def publisher(mock_mqtt: MockMqttClient) -> ErrorPublisher:
    """ErrorPublisher wired to a MockMqttClient with a fixed clock."""
    return ErrorPublisher(
        mqtt=mock_mqtt,
        topic_prefix="test/app",
        clock=_fixed_clock,
    )


# ---------------------------------------------------------------------------
# ErrorPayload
# ---------------------------------------------------------------------------


class TestErrorPayload:
    """ErrorPayload value object tests.

    Technique: Specification-based Testing — verifying immutability,
    defaults, and JSON serialisation.
    """

    async def test_construction_with_all_fields(self) -> None:
        """ErrorPayload stores all fields correctly."""
        payload = ErrorPayload(
            error_type="sensor_failure",
            message="Sensor offline",
            device="temp_1",
            timestamp=FIXED_ISO,
            details={"code": 42},
        )
        assert payload.error_type == "sensor_failure"
        assert payload.message == "Sensor offline"
        assert payload.device == "temp_1"
        assert payload.timestamp == FIXED_ISO
        assert payload.details == {"code": 42}

    async def test_default_details_is_empty_dict(self) -> None:
        """details defaults to an empty dict when not provided."""
        payload = ErrorPayload(
            error_type="error",
            message="boom",
            device=None,
            timestamp=FIXED_ISO,
        )
        assert payload.details == {}

    async def test_device_can_be_none(self) -> None:
        """device=None is a valid value (no device context)."""
        payload = ErrorPayload(
            error_type="error",
            message="boom",
            device=None,
            timestamp=FIXED_ISO,
        )
        assert payload.device is None

    async def test_to_json_produces_valid_json_with_correct_keys(self) -> None:
        """to_json() returns valid JSON containing all payload fields."""
        payload = ErrorPayload(
            error_type="timeout",
            message="Request timed out",
            device="gateway",
            timestamp=FIXED_ISO,
            details={"elapsed_ms": 5000},
        )
        raw = payload.to_json()
        parsed = json.loads(raw)
        assert parsed == {
            "error_type": "timeout",
            "message": "Request timed out",
            "device": "gateway",
            "timestamp": FIXED_ISO,
            "details": {"elapsed_ms": 5000},
        }

    async def test_to_json_device_none_serialised_as_null(self) -> None:
        """When device is None, JSON serialises it as null."""
        payload = ErrorPayload(
            error_type="error",
            message="oops",
            device=None,
            timestamp=FIXED_ISO,
        )
        parsed = json.loads(payload.to_json())
        assert parsed["device"] is None

    async def test_frozen_immutable(self) -> None:
        """Frozen dataclass raises on attribute assignment."""
        payload = ErrorPayload(
            error_type="error",
            message="boom",
            device=None,
            timestamp=FIXED_ISO,
        )
        with pytest.raises(FrozenInstanceError):
            payload.error_type = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_error_payload
# ---------------------------------------------------------------------------


class TestBuildErrorPayload:
    """build_error_payload() function tests.

    Technique: Specification-based Testing — verifying mapping logic,
    fallbacks, and parameter pass-through.
    """

    async def test_basic_error_fallback_type(self) -> None:
        """Unknown exception type falls back to 'error'."""
        payload = build_error_payload(
            RuntimeError("something broke"),
            clock=_fixed_clock,
        )
        assert payload.error_type == "error"
        assert payload.message == "something broke"

    async def test_custom_error_type_map(self) -> None:
        """error_type_map maps exception class to custom type string."""

        class SensorError(Exception): ...

        payload = build_error_payload(
            SensorError("sensor offline"),
            error_type_map={SensorError: "sensor_failure"},
            clock=_fixed_clock,
        )
        assert payload.error_type == "sensor_failure"

    async def test_unknown_error_type_falls_back(self) -> None:
        """Exception not in error_type_map falls back to 'error'."""

        class SensorError(Exception): ...

        class OtherError(Exception): ...

        payload = build_error_payload(
            OtherError("unknown"),
            error_type_map={SensorError: "sensor_failure"},
            clock=_fixed_clock,
        )
        assert payload.error_type == "error"

    async def test_device_parameter_passed_through(self) -> None:
        """device kwarg appears in the resulting payload."""
        payload = build_error_payload(
            ValueError("bad value"),
            device="actuator_1",
            clock=_fixed_clock,
        )
        assert payload.device == "actuator_1"

    async def test_details_passed_through_to_payload(self) -> None:
        """Caller-supplied details appear in the payload."""
        error = ValueError("sensor fault")
        payload = build_error_payload(error, details={"sensor_id": "temp-01"})
        assert payload.details == {"sensor_id": "temp-01"}

    async def test_clock_injection_deterministic_timestamp(self) -> None:
        """Injected clock produces an exact, deterministic timestamp."""
        payload = build_error_payload(
            RuntimeError("tick"),
            clock=_fixed_clock,
        )
        assert payload.timestamp == FIXED_ISO

    async def test_default_clock_timestamp_close_to_now(self) -> None:
        """Without clock injection, timestamp is close to current time."""
        before = datetime.now(UTC)
        payload = build_error_payload(RuntimeError("now"))
        after = datetime.now(UTC)

        ts = datetime.fromisoformat(payload.timestamp)
        assert before <= ts <= after


# ---------------------------------------------------------------------------
# ErrorPublisher
# ---------------------------------------------------------------------------


class TestErrorPublisher:
    """ErrorPublisher service tests.

    Technique: State-based Testing with MockMqttClient — verify
    published topics, payloads, QoS, and retain flags.
    """

    async def test_publishes_to_global_topic(
        self,
        publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Always publishes to {prefix}/error."""
        await publisher.publish(RuntimeError("boom"))
        topics = [t for t, _, _, _ in mock_mqtt.published]
        assert "test/app/error" in topics

    async def test_publishes_to_device_topic_when_device_provided(
        self,
        publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Publishes to {prefix}/{device}/error when device is given."""
        await publisher.publish(RuntimeError("boom"), device="blind")
        topics = [t for t, _, _, _ in mock_mqtt.published]
        assert "test/app/error" in topics
        assert "test/app/blind/error" in topics

    async def test_no_device_topic_when_device_is_none(
        self,
        publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Does NOT publish device topic when device is None."""
        await publisher.publish(RuntimeError("boom"))
        assert len(mock_mqtt.published) == 1
        assert mock_mqtt.published[0][0] == "test/app/error"

    async def test_qos_1_and_retain_false(
        self,
        publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """All publishes use QoS 1 and retain=False."""
        await publisher.publish(RuntimeError("boom"), device="sensor")
        for _, _, retain, qos in mock_mqtt.published:
            assert retain is False
            assert qos == 1

    async def test_payload_is_valid_json_matching_schema(
        self,
        publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Published payload is valid JSON with expected keys."""
        await publisher.publish(RuntimeError("boom"), device="blind")
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert parsed["error_type"] == "error"
        assert parsed["message"] == "boom"
        assert parsed["device"] == "blind"
        assert parsed["timestamp"] == FIXED_ISO
        assert parsed["details"] == {}

    async def test_pluggable_error_type_map_flows_through(
        self,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """error_type_map on publisher flows through to payload."""

        class CustomError(Exception): ...

        pub = ErrorPublisher(
            mqtt=mock_mqtt,
            topic_prefix="app",
            error_type_map={CustomError: "custom_error"},
            clock=_fixed_clock,
        )
        await pub.publish(CustomError("custom fail"))
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert parsed["error_type"] == "custom_error"

    async def test_clock_injection_flows_through(
        self,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Clock on publisher flows through to payload timestamp."""
        custom_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        pub = ErrorPublisher(
            mqtt=mock_mqtt,
            topic_prefix="app",
            clock=lambda: custom_dt,
        )
        await pub.publish(RuntimeError("tick"))
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert parsed["timestamp"] == custom_dt.isoformat()

    async def test_logs_warning_when_publishing(
        self,
        publisher: ErrorPublisher,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """publish() logs at WARNING level."""
        with caplog.at_level(logging.WARNING, logger="cosalette._errors"):
            await publisher.publish(RuntimeError("warn me"))
        assert any("warn me" in r.message for r in caplog.records)
        assert any(r.levelno == logging.WARNING for r in caplog.records)


class TestSafePublish:
    """_safe_publish exception-safety tests.

    Technique: Exception Safety — verifying fire-and-forget semantics.
    """

    async def test_swallows_mqtt_publish_exception(
        self,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """_safe_publish catches exceptions from mqtt.publish."""
        mock_mqtt.raise_on_publish = ConnectionError("broker down")
        pub = ErrorPublisher(
            mqtt=mock_mqtt,
            topic_prefix="app",
            clock=_fixed_clock,
        )
        # Should not raise
        await pub.publish(RuntimeError("boom"))

    async def test_logs_swallowed_exception(
        self,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_safe_publish logs the swallowed exception."""
        mock_mqtt.raise_on_publish = ConnectionError("broker down")
        pub = ErrorPublisher(
            mqtt=mock_mqtt,
            topic_prefix="app",
            clock=_fixed_clock,
        )
        with caplog.at_level(logging.ERROR, logger="cosalette._errors"):
            await pub.publish(RuntimeError("boom"))
        assert any("Failed to publish error" in r.message for r in caplog.records)

    async def test_swallows_payload_build_failure(
        self,
        mock_mqtt: MockMqttClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """publish() swallows exceptions from payload building/serialisation.

        Technique: Error Guessing — the clock callable raises, simulating
        a failure *before* the MQTT layer is reached.
        """

        def _broken_clock() -> datetime:
            raise RuntimeError("clock exploded")

        pub = ErrorPublisher(
            mqtt=mock_mqtt,
            topic_prefix="app",
            clock=_broken_clock,
        )
        # Should not raise — fire-and-forget covers the full pipeline
        with caplog.at_level(logging.ERROR, logger="cosalette._errors"):
            await pub.publish(RuntimeError("boom"))
        assert mock_mqtt.publish_count == 0
        assert any("Failed to build error payload" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# TestRootDeviceErrors — root device error publication
# ---------------------------------------------------------------------------


class TestRootDeviceErrors:
    """Tests for root-level device error publication.

    When ``is_root=True``, the per-device error topic is skipped
    because it would be identical to the global error topic.

    Technique: State-based Testing — MockMqttClient records
    published topics for assertion.
    """

    async def test_root_device_skips_per_device_topic(
        self,
        publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Root device only publishes to global error topic."""
        await publisher.publish(
            RuntimeError("oops"),
            device="sensor",
            is_root=True,
        )
        topics = [t for t, _, _, _ in mock_mqtt.published]
        assert "test/app/error" in topics
        # Per-device topic should be skipped for root devices
        assert "test/app/sensor/error" not in topics
        assert len(topics) == 1

    async def test_named_device_still_publishes_both(
        self,
        publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Named device still publishes to both global and per-device topics."""
        await publisher.publish(
            RuntimeError("oops"),
            device="blind",
            is_root=False,
        )
        topics = [t for t, _, _, _ in mock_mqtt.published]
        assert "test/app/error" in topics
        assert "test/app/blind/error" in topics

    async def test_root_device_payload_still_includes_device_name(
        self,
        publisher: ErrorPublisher,
        mock_mqtt: MockMqttClient,
    ) -> None:
        """Root device error payload JSON still contains the device name."""
        await publisher.publish(
            RuntimeError("oops"),
            device="sensor",
            is_root=True,
        )
        _, payload_str, _, _ = mock_mqtt.published[0]
        parsed = json.loads(payload_str)
        assert parsed["device"] == "sensor"
