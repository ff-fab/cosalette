"""Unit tests for connect-aware schema status publishing (cos-2r2r).

Verifies that ``_publish_schema_status`` registers a connect callback
when the MQTT adapter is connect-aware, and publishes eagerly when it
is not.

Test Techniques Used:
    - Specification-based Testing: connect-aware vs eager paths.
    - Error Guessing: callback fires on simulate_connect.
    - Boundary Value Analysis: validation active vs inactive.
"""

from __future__ import annotations

import pytest

from cosalette._app._helpers import _publish_schema_status
from cosalette._mqtt import MockMqttClient
from cosalette._schema import EnforcementConfig, SchemaRegistry

pytestmark = pytest.mark.unit


def _minimal_registry(enforcement: EnforcementConfig) -> SchemaRegistry:
    """Build a SchemaRegistry with all required fields for testing."""
    return SchemaRegistry(
        app_name="test",
        app_version="0.0.0",
        asyncapi_version="3.0.0",
        enforcement=enforcement,
        channels={},
        operations={},
        component_schemas={},
        device_names=frozenset(),
    )


@pytest.fixture
def enforcement() -> EnforcementConfig:
    return EnforcementConfig(mode="warn", on_publish=True)


@pytest.fixture
def schema_registry(enforcement: EnforcementConfig) -> SchemaRegistry:
    return _minimal_registry(enforcement)


def _make_fake_validating_port(inner, enforcement: EnforcementConfig):
    """Build a minimal ValidatingMqttPort for testing."""
    from cosalette._schema._validator import PayloadValidator, build_validating_port

    validator = PayloadValidator(_minimal_registry(enforcement))
    return build_validating_port(inner, validator, enforcement)


class TestPublishSchemaStatusConnectAware:
    """_publish_schema_status registers a callback when connect-aware."""

    async def test_connect_aware_registers_callback_not_eager(
        self,
        enforcement: EnforcementConfig,
        schema_registry: SchemaRegistry,
    ) -> None:
        """With a connect-aware adapter, no eager publish happens."""
        from tests.fixtures.mqtt import FakeConnectAwareMqttClient

        mqtt = FakeConnectAwareMqttClient()
        port = _make_fake_validating_port(mqtt, enforcement)

        await _publish_schema_status(
            port,
            port,
            schema_registry,
            "test",
            connect_aware=True,
        )

        assert mqtt.published == [], "Should not publish eagerly when connect-aware"
        assert len(mqtt._connect_callbacks) == 1, "Should register exactly one callback"

    async def test_connect_aware_callback_publishes_on_connect(
        self,
        enforcement: EnforcementConfig,
        schema_registry: SchemaRegistry,
    ) -> None:
        """The registered callback publishes schema status when connect fires."""
        from tests.fixtures.mqtt import FakeConnectAwareMqttClient

        mqtt = FakeConnectAwareMqttClient()
        port = _make_fake_validating_port(mqtt, enforcement)

        await _publish_schema_status(
            port,
            port,
            schema_registry,
            "test",
            connect_aware=True,
        )

        assert mqtt.published == []
        await mqtt.simulate_connect()

        status_msgs = mqtt.get_messages_for("test/schema/status")
        assert len(status_msgs) == 1, "Schema status should be published after connect"
        _payload, retain, _qos = status_msgs[0]
        assert retain is True

    async def test_connect_aware_callback_republishes_on_reconnect(
        self,
        enforcement: EnforcementConfig,
        schema_registry: SchemaRegistry,
    ) -> None:
        """Schema status is republished on every reconnect."""
        from tests.fixtures.mqtt import FakeConnectAwareMqttClient

        mqtt = FakeConnectAwareMqttClient()
        port = _make_fake_validating_port(mqtt, enforcement)

        await _publish_schema_status(
            port,
            port,
            schema_registry,
            "test",
            connect_aware=True,
        )

        await mqtt.simulate_connect()
        await mqtt.simulate_connect()

        status_msgs = mqtt.get_messages_for("test/schema/status")
        assert len(status_msgs) == 2


class TestPublishSchemaStatusEager:
    """_publish_schema_status publishes eagerly when not connect-aware."""

    async def test_non_connect_aware_publishes_eagerly(
        self,
        enforcement: EnforcementConfig,
        schema_registry: SchemaRegistry,
    ) -> None:
        """With a non-connect-aware adapter, schema status publishes eagerly."""
        mqtt = MockMqttClient()
        port = _make_fake_validating_port(mqtt, enforcement)

        await _publish_schema_status(
            port,
            port,
            schema_registry,
            "test",
            connect_aware=False,
        )

        status_msgs = [
            (t, p, r, q) for t, p, r, q in mqtt.published if t == "test/schema/status"
        ]
        assert len(status_msgs) == 1

    async def test_no_validating_port_is_noop(
        self,
        schema_registry: SchemaRegistry,
    ) -> None:
        """No publish when validating_port is None."""
        mqtt = MockMqttClient()

        await _publish_schema_status(
            mqtt,
            None,
            schema_registry,
            "test",
            connect_aware=False,
        )

        assert mqtt.published == []

    async def test_no_schema_registry_is_noop(
        self,
        enforcement: EnforcementConfig,
    ) -> None:
        """No publish or callback registration when schema_registry is None."""
        from tests.fixtures.mqtt import FakeConnectAwareMqttClient

        mqtt = FakeConnectAwareMqttClient()
        port = _make_fake_validating_port(mqtt, enforcement)

        await _publish_schema_status(
            mqtt,
            port,
            None,
            "test",
            connect_aware=True,
        )

        assert mqtt.published == []
        assert mqtt._connect_callbacks == []
