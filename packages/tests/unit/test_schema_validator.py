"""Unit tests for MQTT schema validation components."""

from __future__ import annotations

import pytest

from cosalette._mqtt import MockMqttClient
from cosalette._schema import (
    ChannelSchema,
    EnforcementConfig,
    SchemaRegistry,
)
from cosalette._schema_validator import (
    PayloadValidator,
    ValidatingMqttPort,
    build_skip_topics,
)

# Test fixtures


def _make_registry(*, mode: str = "warn", on_publish: bool = True) -> SchemaRegistry:
    """Create test schema registry with temperature channel."""
    return SchemaRegistry(
        app_name="testapp",
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode=mode, on_publish=on_publish),
        channels={
            "temperatureState": ChannelSchema(
                address="testapp/temperature/state",
                address_template="{appName}/{deviceName}/state",
                direction="send",
                payload_schema={
                    "type": "object",
                    "required": ["temperature"],
                    "properties": {"temperature": {"type": "number"}},
                },
            ),
        },
        operations={},
        component_schemas={},
        device_names=frozenset({"temperature"}),
    )


def _make_registry_multi_errors() -> SchemaRegistry:
    """Create test registry with schema that can have multiple violations."""
    return SchemaRegistry(
        app_name="testapp",
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="warn", on_publish=True),
        channels={
            "sensorData": ChannelSchema(
                address="testapp/sensor/data",
                address_template="{appName}/{deviceName}/data",
                direction="send",
                payload_schema={
                    "type": "object",
                    "required": ["temperature", "humidity"],
                    "properties": {
                        "temperature": {"type": "number"},
                        "humidity": {"type": "number", "minimum": 0, "maximum": 100},
                    },
                },
            ),
        },
        operations={},
        component_schemas={},
        device_names=frozenset({"sensor"}),
    )


def _make_registry_no_schema() -> SchemaRegistry:
    """Create test registry with channel that has no payload schema."""
    return SchemaRegistry(
        app_name="testapp",
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="warn", on_publish=True),
        channels={
            "noSchemaChannel": ChannelSchema(
                address="testapp/device/noschemachannel",
                address_template="{appName}/{deviceName}/noschemachannel",
                direction="send",
                payload_schema=None,  # No schema
            ),
        },
        operations={},
        component_schemas={},
        device_names=frozenset({"device"}),
    )


def _make_registry_invalid_schema() -> SchemaRegistry:
    """Create test registry with malformed JSON Schema."""
    return SchemaRegistry(
        app_name="testapp",
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="warn", on_publish=True),
        channels={
            "invalidSchemaChannel": ChannelSchema(
                address="testapp/device/invalid",
                address_template="{appName}/{deviceName}/invalid",
                direction="send",
                payload_schema={
                    "type": "invalid_type",  # Invalid schema
                },
            ),
        },
        operations={},
        component_schemas={},
        device_names=frozenset({"device"}),
    )


# PayloadValidator Tests


@pytest.mark.unit
def test_validate_valid_payload_returns_empty():
    """Valid payload matching schema returns no issues."""
    registry = _make_registry()
    validator = PayloadValidator(registry)

    payload = {"temperature": 23.5}
    issues = validator.validate("testapp/temperature/state", payload)

    assert issues == []


@pytest.mark.unit
def test_validate_invalid_payload_returns_issues():
    """Invalid payload (missing required field) returns issues."""
    registry = _make_registry()
    validator = PayloadValidator(registry)

    payload = {}  # Missing required 'temperature' field
    issues = validator.validate("testapp/temperature/state", payload)

    assert len(issues) == 1
    assert issues[0].channel_name == "temperatureState"
    assert issues[0].topic == "testapp/temperature/state"
    assert "'temperature' is a required property" in issues[0].message


@pytest.mark.unit
def test_validate_no_matching_schema_returns_empty():
    """Topic with no matching schema returns no issues."""
    registry = _make_registry()
    validator = PayloadValidator(registry)

    payload = {"any": "data"}
    issues = validator.validate("unrelated/topic", payload)

    assert issues == []


@pytest.mark.unit
def test_validate_multiple_errors():
    """Payload with multiple violations returns multiple issues."""
    registry = _make_registry_multi_errors()
    validator = PayloadValidator(registry)

    payload = {"humidity": 150}  # Missing temperature, humidity out of range
    issues = validator.validate("testapp/sensor/data", payload)

    assert len(issues) == 2

    # Check that both violations are reported
    messages = [issue.message for issue in issues]
    assert any("'temperature' is a required property" in msg for msg in messages)
    assert any("150 is greater than the maximum of 100" in msg for msg in messages)


@pytest.mark.unit
def test_invalid_schema_raises_at_construction():
    """Malformed JSON Schema raises exception during PayloadValidator construction."""
    registry = _make_registry_invalid_schema()

    with pytest.raises(Exception):  # noqa: B017 (jsonschema.SchemaError or similar)
        PayloadValidator(registry)


# ValidatingMqttPort Tests


@pytest.mark.unit
async def test_off_mode_delegates_without_validation():
    """Enforcement mode 'off' delegates directly without validation."""
    registry = _make_registry(mode="off")
    validator = PayloadValidator(registry)
    inner = MockMqttClient()

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
    )

    # Invalid payload should still be published in off mode
    invalid_payload = {}  # Missing required temperature
    await port.publish("testapp/temperature/state", invalid_payload)

    assert inner.publish_count == 1
    assert port.violation_count == 0  # No validation happened


@pytest.mark.unit
async def test_warn_mode_publishes_with_warning(caplog):
    """Warn mode publishes invalid payload but logs warning."""
    registry = _make_registry(mode="warn")
    validator = PayloadValidator(registry)
    inner = MockMqttClient()

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
    )

    invalid_payload = {}  # Missing required temperature
    await port.publish("testapp/temperature/state", invalid_payload)

    # Should publish anyway
    assert inner.publish_count == 1
    assert port.violation_count == 1

    # Should log warning
    expected_msg = (
        "Schema violation on testapp/temperature/state: 1 issue(s) — publishing anyway"
    )
    assert expected_msg in caplog.text


@pytest.mark.unit
async def test_strict_mode_suppresses_invalid_publish(caplog):
    """Strict mode suppresses publish for invalid payload."""
    registry = _make_registry(mode="strict")
    validator = PayloadValidator(registry)
    inner = MockMqttClient()

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
    )

    invalid_payload = {}  # Missing required temperature
    await port.publish("testapp/temperature/state", invalid_payload)

    # Should NOT publish
    assert inner.publish_count == 0
    assert port.violation_count == 1

    # Should log error
    expected_msg = (
        "Schema violation on testapp/temperature/state: 1 issue(s) — publish suppressed"
    )
    assert expected_msg in caplog.text


@pytest.mark.unit
async def test_str_payload_passes_through():
    """String payloads bypass validation and are published directly."""
    registry = _make_registry(mode="strict")
    validator = PayloadValidator(registry)
    inner = MockMqttClient()

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
    )

    # String payload (pre-serialized) should pass through
    await port.publish("testapp/temperature/state", "invalid json")

    assert inner.publish_count == 1
    assert port.violation_count == 0


@pytest.mark.unit
async def test_skip_topics_bypass_validation():
    """Topics in skip_topics set bypass validation."""
    registry = _make_registry(mode="strict")
    validator = PayloadValidator(registry)
    inner = MockMqttClient()
    skip_topics = frozenset(["testapp/temperature/state"])

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
        skip_topics=skip_topics,
    )

    # Invalid payload on skipped topic should publish
    invalid_payload = {}  # Missing required temperature
    await port.publish("testapp/temperature/state", invalid_payload)

    assert inner.publish_count == 1
    assert port.violation_count == 0  # No validation


@pytest.mark.unit
async def test_valid_dict_publishes_without_warning():
    """Valid dict payload publishes successfully without warnings."""
    registry = _make_registry(mode="strict")
    validator = PayloadValidator(registry)
    inner = MockMqttClient()

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
    )

    valid_payload = {"temperature": 23.5}
    await port.publish("testapp/temperature/state", valid_payload)

    assert inner.publish_count == 1
    assert port.violation_count == 0


@pytest.mark.unit
async def test_violation_count_increments():
    """Violation count increases on each schema violation."""
    registry = _make_registry(mode="warn")
    validator = PayloadValidator(registry)
    inner = MockMqttClient()

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
    )

    invalid_payload = {}  # Missing required temperature

    # Publish twice
    await port.publish("testapp/temperature/state", invalid_payload)
    await port.publish("testapp/temperature/state", invalid_payload)

    assert port.violation_count == 2


@pytest.mark.unit
async def test_subscribe_delegates_to_inner():
    """subscribe() method delegates to inner port."""
    registry = _make_registry()
    validator = PayloadValidator(registry)
    inner = MockMqttClient()

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
    )

    await port.subscribe("test/topic")

    assert "test/topic" in inner.subscriptions


@pytest.mark.unit
def test_on_message_delegates_to_inner():
    """on_message() method delegates to inner port if supported."""
    registry = _make_registry()
    validator = PayloadValidator(registry)
    inner = MockMqttClient()

    port = ValidatingMqttPort(
        inner=inner,
        validator=validator,
        enforcement=registry.enforcement,
    )

    async def test_callback(topic: str, payload: str) -> None:
        pass

    # Should not raise (MockMqttClient has on_message)
    port.on_message(test_callback)


# build_skip_topics Tests


@pytest.mark.unit
def test_includes_error_and_status_topics():
    """build_skip_topics includes error and status topics."""
    skip_topics = build_skip_topics("myapp", frozenset())

    expected = {
        "myapp/error",
        "myapp/status",
        "myapp/_meta/registry",
    }

    assert expected.issubset(skip_topics)


@pytest.mark.unit
def test_includes_device_error_and_availability():
    """build_skip_topics includes per-device error and availability topics."""
    device_names = frozenset({"sensor1", "sensor2"})
    skip_topics = build_skip_topics("myapp", device_names)

    expected = {
        "myapp/sensor1/error",
        "myapp/sensor1/availability",
        "myapp/sensor2/error",
        "myapp/sensor2/availability",
    }

    assert expected.issubset(skip_topics)
