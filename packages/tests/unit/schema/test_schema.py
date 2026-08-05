"""Unit tests for cosalette._schema — Schema data model.

Test Techniques Used:
- Specification-based Testing: Verifying dataclass construction contracts
- Equivalence Partitioning: Valid/invalid modes, directions, archetypes
- Error Guessing: Frozen mutation attempts
"""

from __future__ import annotations

import dataclasses

import pytest

from cosalette._schema import (
    CapabilityRequirement,
    ChannelSchema,
    ConsumerMetadata,
    EnforcementConfig,
    MqttBinding,
    OperationSchema,
    PropertySchema,
    SchemaRegistry,
    _extract_device_names,
    _topic_matches,
)


class TestEnforcementConfig:
    def test_enforcement_config_defaults(self) -> None:
        config = EnforcementConfig()
        assert config.mode == "off"
        assert config.on_configure is True
        assert config.on_publish is False
        assert config.network_level is False

    def test_enforcement_config_custom_values(self) -> None:
        config = EnforcementConfig(
            mode="strict",
            on_configure=False,
            on_publish=True,
            network_level=True,
        )
        assert config.mode == "strict"
        assert config.on_configure is False
        assert config.on_publish is True
        assert config.network_level is True

    def test_enforcement_config_is_frozen(self) -> None:
        config = EnforcementConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.mode = "strict"  # ty: ignore[invalid-assignment]


class TestMqttBinding:
    def test_mqtt_binding_defaults(self) -> None:
        binding = MqttBinding()
        assert binding.qos == 1
        assert binding.retain is False

    def test_mqtt_binding_custom(self) -> None:
        binding = MqttBinding(qos=0, retain=True)
        assert binding.qos == 0
        assert binding.retain is True


class TestConsumerMetadata:
    def test_consumer_metadata_defaults(self) -> None:
        metadata = ConsumerMetadata()
        assert metadata.device_class is None
        assert metadata.unit is None
        assert metadata.display_name is None
        assert metadata.icon is None
        assert metadata.state_class is None
        assert metadata.read_only is False

    def test_consumer_metadata_full(self) -> None:
        metadata = ConsumerMetadata(
            device_class="temperature",
            unit="°C",
            display_name="Room Temperature",
            icon="mdi:thermometer",
            state_class="measurement",
            read_only=True,
        )
        assert metadata.device_class == "temperature"
        assert metadata.unit == "°C"
        assert metadata.display_name == "Room Temperature"
        assert metadata.icon == "mdi:thermometer"
        assert metadata.state_class == "measurement"
        assert metadata.read_only is True


class TestPropertySchema:
    def test_property_schema_minimal(self) -> None:
        prop = PropertySchema(
            name="temperature",
            json_schema={"type": "number"},
        )
        assert prop.name == "temperature"
        assert prop.json_schema == {"type": "number"}
        assert prop.consumer is None
        assert prop.ha_discovery is None
        assert prop.openhab is None

    def test_property_schema_with_consumer(self) -> None:
        consumer = ConsumerMetadata(device_class="temperature")
        prop = PropertySchema(
            name="temperature",
            json_schema={"type": "number"},
            consumer=consumer,
        )
        assert prop.consumer is consumer


class TestChannelSchema:
    def test_channel_schema_minimal(self) -> None:
        channel = ChannelSchema(
            address="test/topic",
            address_template="test/topic",
            direction="send",
        )
        assert channel.address == "test/topic"
        assert channel.address_template == "test/topic"
        assert channel.direction == "send"
        assert channel.payload_schema is None
        assert isinstance(channel.mqtt_binding, MqttBinding)
        assert channel.capability_requirements == ()
        assert channel.archetype is None
        assert channel.coalescing_group is None
        assert channel.message_name is None
        assert channel.app_name is None
        assert channel.scope is None
        assert channel.properties == {}

    def test_channel_schema_full(self) -> None:
        requirements = (CapabilityRequirement(tag="test"),)
        properties = {"temp": PropertySchema("temp", {"type": "number"})}

        channel = ChannelSchema(
            address="app/device/state",
            address_template="app/{deviceName}/state",
            direction="both",
            payload_schema={"type": "object"},
            capability_requirements=requirements,
            archetype="telemetry",
            coalescing_group="sensors",
            message_name="reading",
            app_name="test_app",
            scope="all_apps",
            properties=properties,
        )
        assert channel.app_name == "test_app"
        assert channel.scope == "all_apps"
        assert channel.properties == properties


class TestOperationSchema:
    def test_operation_schema_defaults(self) -> None:
        operation = OperationSchema(
            action="send",
            channel_ref="test_channel",
        )
        assert operation.action == "send"
        assert operation.channel_ref == "test_channel"
        assert operation.archetype is None
        assert operation.coalescing_group is None
        assert isinstance(operation.mqtt_binding, MqttBinding)


class TestSchemaRegistry:
    def test_filter_for_app_includes_matching_app(self) -> None:
        channels = {
            "app1": ChannelSchema("app1/topic", "app1/topic", "send", app_name="app1"),
            "app2": ChannelSchema("app2/topic", "app2/topic", "send", app_name="app2"),
        }
        operations = {
            "op1": OperationSchema("send", "app1"),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations=operations,
            component_schemas={},
            device_names=frozenset(),
        )

        filtered = registry.filter_for_app("app1")
        assert "app1" in filtered.channels
        assert "app2" not in filtered.channels
        assert "op1" in filtered.operations

    def test_filter_for_app_includes_all_apps_scope(self) -> None:
        channels = {
            "app1": ChannelSchema(
                "app1/topic",
                "app1/topic",
                "send",
                app_name="app1",
            ),
            "shared": ChannelSchema(
                "shared/topic",
                "shared/topic",
                "send",
                scope="all_apps",
            ),
        }
        operations = {}

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations=operations,
            component_schemas={},
            device_names=frozenset(),
        )

        filtered = registry.filter_for_app("app1")
        assert "app1" in filtered.channels
        assert "shared" in filtered.channels

    def test_filter_for_app_excludes_other_apps(self) -> None:
        channels = {
            "app1": ChannelSchema(
                "app1/topic",
                "app1/topic",
                "send",
                app_name="app1",
            ),
            "app2": ChannelSchema(
                "app2/topic",
                "app2/topic",
                "send",
                app_name="app2",
            ),
            "app3": ChannelSchema(
                "app3/topic",
                "app3/topic",
                "send",
                app_name="app3",
            ),
        }
        operations = {}

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations=operations,
            component_schemas={},
            device_names=frozenset(),
        )

        filtered = registry.filter_for_app("app1")
        assert "app1" in filtered.channels
        assert "app2" not in filtered.channels
        assert "app3" not in filtered.channels

    def test_filter_for_app_filters_operations(self) -> None:
        channels = {
            "app1_ch": ChannelSchema(
                "app1/topic",
                "app1/topic",
                "send",
                app_name="app1",
            ),
            "app2_ch": ChannelSchema(
                "app2/topic",
                "app2/topic",
                "send",
                app_name="app2",
            ),
        }
        operations = {
            "op1": OperationSchema("send", "app1_ch"),
            "op2": OperationSchema("send", "app2_ch"),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations=operations,
            component_schemas={},
            device_names=frozenset(),
        )

        filtered = registry.filter_for_app("app1")
        assert "op1" in filtered.operations
        assert "op2" not in filtered.operations

    def test_all_app_names(self) -> None:
        channels = {
            "ch1": ChannelSchema("topic1", "topic1", "send", app_name="app1"),
            "ch2": ChannelSchema("topic2", "topic2", "send", app_name="app2"),
            "ch3": ChannelSchema("topic3", "topic3", "send", app_name="app1"),
            "ch4": ChannelSchema("topic4", "topic4", "send", app_name=None),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations={},
            component_schemas={},
            device_names=frozenset(),
        )

        app_names = registry.all_app_names()
        assert app_names == {"app1", "app2"}

    def test_channels_for_device_template_match(self) -> None:
        channels = {
            "ch1": ChannelSchema("app/device1/state", "app/{deviceName}/state", "send"),
            "ch2": ChannelSchema("app/static/topic", "app/static/topic", "send"),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations={},
            component_schemas={},
            device_names=frozenset(),
        )

        device_channels = registry.channels_for_device("device1")
        assert len(device_channels) == 1
        assert device_channels[0].address == "app/device1/state"

    def test_channels_for_device_concrete_match(self) -> None:
        channels = {
            "ch1": ChannelSchema("app/device1/state", "app/device1/state", "send"),
            "ch2": ChannelSchema("app/device2/state", "app/device2/state", "send"),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations={},
            component_schemas={},
            device_names=frozenset(),
        )

        device_channels = registry.channels_for_device("device1")
        assert len(device_channels) == 1
        assert device_channels[0].address == "app/device1/state"

    def test_required_channels_for_tag(self) -> None:
        requirements = (CapabilityRequirement(tag="heating"),)
        channels = {
            "ch1": ChannelSchema(
                "topic1",
                "topic1",
                "send",
                capability_requirements=requirements,
            ),
            "ch2": ChannelSchema(
                "topic2",
                "topic2",
                "send",
            ),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations={},
            component_schemas={},
            device_names=frozenset(),
        )

        tagged_channels = registry.required_channels_for_tag("heating")
        assert len(tagged_channels) == 1
        assert tagged_channels[0].address == "topic1"

    def test_required_channels_for_tag_no_match(self) -> None:
        requirements = (CapabilityRequirement(tag="heating"),)
        channels = {
            "ch1": ChannelSchema(
                "topic1",
                "topic1",
                "send",
                capability_requirements=requirements,
            ),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations={},
            component_schemas={},
            device_names=frozenset(),
        )

        tagged_channels = registry.required_channels_for_tag("cooling")
        assert tagged_channels == []

    def test_payload_schema_for_topic_exact_match(self) -> None:
        schema = {"type": "object", "properties": {"value": {"type": "number"}}}
        channels = {
            "ch1": ChannelSchema(
                "app/device/state",
                "app/device/state",
                "send",
                payload_schema=schema,
            ),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations={},
            component_schemas={},
            device_names=frozenset(),
        )

        result = registry.payload_schema_for_topic("app/device/state")
        assert result == schema

    def test_payload_schema_for_topic_template_match(self) -> None:
        schema = {"type": "object", "properties": {"value": {"type": "number"}}}
        channels = {
            "ch1": ChannelSchema(
                "app/device1/state",
                "app/{deviceName}/state",
                "send",
                payload_schema=schema,
            ),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations={},
            component_schemas={},
            device_names=frozenset(),
        )

        result = registry.payload_schema_for_topic("app/device2/state")
        assert result == schema

    def test_payload_schema_for_topic_no_match(self) -> None:
        channels = {
            "ch1": ChannelSchema(
                "app/device/state",
                "app/device/state",
                "send",
                payload_schema={"type": "object"},
            ),
        }

        registry = SchemaRegistry(
            app_name=None,
            app_version="1.0.0",
            asyncapi_version="3.0.0",
            enforcement=EnforcementConfig(),
            channels=channels,
            operations={},
            component_schemas={},
            device_names=frozenset(),
        )

        result = registry.payload_schema_for_topic("different/topic")
        assert result is None


class TestTopicMatches:
    def test_exact_match(self) -> None:
        assert _topic_matches("app/device/state", "app/device/state") is True

    def test_template_match(self) -> None:
        result = _topic_matches(
            "{appName}/{deviceName}/state",
            "vito2mqtt/temperature/state",
        )
        assert result is True

    def test_no_match(self) -> None:
        assert _topic_matches("app/device/state", "other/topic") is False

    def test_partial_segment_no_match(self) -> None:
        result = _topic_matches(
            "app/{deviceName}/state",
            "app/device/extra/state",
        )
        assert result is False


class TestExtractDeviceNames:
    def test_extract_from_template(self) -> None:
        channels = {
            "ch1": ChannelSchema("app/device1/state", "app/{deviceName}/state", "send"),
            "ch2": ChannelSchema("app/device2/cmd", "app/{deviceName}/cmd", "receive"),
        }

        device_names = _extract_device_names(channels)
        assert device_names == {"device1", "device2"}

    def test_extract_from_archetype_channel(self) -> None:
        channels = {
            "ch1": ChannelSchema(
                "app/sensor1/reading",
                "app/sensor1/reading",
                "send",
                archetype="telemetry",
            ),
            "ch2": ChannelSchema(
                "app/actuator1/cmd",
                "app/actuator1/cmd",
                "receive",
                archetype="command",
            ),
        }

        device_names = _extract_device_names(channels)
        assert device_names == {"sensor1", "actuator1"}

    def test_extract_from_archetype_nested_path(self) -> None:
        """4-part address: middle segments joined as device name.

        ``app/zone/sensor/reading`` → ``'zone/sensor'``.

        Technique: Equivalence Partitioning — paths with 4 segments form a
        distinct class where all middle segments are joined with '/'.
        """
        channels = {
            "ch1": ChannelSchema(
                "app/zone/sensor/reading",
                "app/zone/sensor/reading",
                "send",
                archetype="telemetry",
            ),
        }

        device_names = _extract_device_names(channels)
        assert device_names == {"zone/sensor"}

    def test_extract_from_archetype_two_segment_returns_none(self) -> None:
        """2-segment addresses return no device (below the 3-part floor).

        Technique: Boundary Value Analysis — len==2 is one below the minimum
        valid depth (3); must return None per ADR-002 contract.
        """
        channels = {
            "ch1": ChannelSchema(
                "app/device",
                "app/device",
                "send",
                archetype="telemetry",
            ),
        }

        device_names = _extract_device_names(channels)
        assert device_names == frozenset()

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("app", frozenset()),  # len=1 — single segment
            ("app/device", frozenset()),  # len=2 — below floor
            ("app/device/state", frozenset({"device"})),  # len=3 — standard floor
            ("app/zone/sensor/reading", frozenset({"zone/sensor"})),  # len=4
            ("app/a/b/c/state", frozenset({"a/b/c"})),  # len=5 — deep nesting
        ],
    )
    def test_extract_from_archetype_bva_segment_counts(
        self, address: str, expected: frozenset[str]
    ) -> None:
        """Technique: Boundary Value Analysis — segment counts around the 3-part floor.

        ADR-002 requires {app}/{device…}/{signal}. len=1..5 covers all
        boundary classes: below-floor (1,2), floor (3), nested (4), deep (5).
        """
        channels = {
            "ch1": ChannelSchema(address, address, "send", archetype="telemetry"),
        }

        device_names = _extract_device_names(channels)
        assert device_names == expected

    def test_no_device_names(self) -> None:
        channels = {
            "ch1": ChannelSchema("global/status", "global/status", "send"),
        }

        device_names = _extract_device_names(channels)
        assert device_names == frozenset()
