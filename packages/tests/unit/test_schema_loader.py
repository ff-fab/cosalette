"""Unit tests for cosalette._schema_loader — Schema loading and parsing.

Test Techniques Used:
- Specification-based Testing: Verifying load pipeline contracts
- Equivalence Partitioning: Valid schemas, invalid versions, malformed YAML
- Error Guessing: Circular refs, missing keys, bad extensions
- Round-trip Testing: YAML → SchemaRegistry field verification
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cosalette._schema_loader import (
    FileSchemaSource,
    InlineSchemaSource,
    SchemaLoadError,
    load_schema,
)


@pytest.fixture
def schemas_dir() -> Path:
    return Path(__file__).parent.parent / "fixtures" / "schemas"


class TestInlineSchemaSource:
    async def test_load_returns_content(self) -> None:
        source = InlineSchemaSource("test content")
        content = await source.load()
        assert content == "test content"

    def test_description(self) -> None:
        source = InlineSchemaSource("test")
        assert source.description == "<inline>"


class TestFileSchemaSource:
    async def test_load_reads_file(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "valid_basic.yaml")
        content = await source.load()
        assert "asyncapi: 3.0.0" in content
        assert "vito2mqtt" in content

    async def test_load_nonexistent_raises(self) -> None:
        source = FileSchemaSource(Path("nonexistent.yaml"))
        with pytest.raises(FileNotFoundError):
            await source.load()


class TestRefResolution:
    async def test_internal_ref_resolved(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "valid_with_refs.yaml")
        registry = await load_schema(source)

        # Verify $ref was resolved in payload schema
        temp_channel = registry.channels["temperatureState"]
        assert temp_channel.payload_schema is not None
        assert "$ref" not in str(temp_channel.payload_schema)
        assert temp_channel.payload_schema["type"] == "object"
        assert "temperature" in temp_channel.payload_schema["properties"]

    async def test_circular_ref_raises(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "invalid_circular_ref.yaml")

        with pytest.raises(SchemaLoadError) as exc_info:
            await load_schema(source)

        assert "Circular reference" in str(exc_info.value)


class TestExtensionValidation:
    async def test_valid_extensions_pass(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "valid_basic.yaml")
        # Should not raise
        await load_schema(source)

    async def test_invalid_enforcement_mode(self) -> None:
        yaml_content = """
asyncapi: 3.0.0
info:
  title: test
  version: 1.0.0
x-cosalette-enforcement:
  mode: invalid_mode
channels: {}
        """
        source = InlineSchemaSource(yaml_content.strip())

        with pytest.raises(SchemaLoadError) as exc_info:
            await load_schema(source)

        assert "mode must be" in str(exc_info.value)

    async def test_missing_requires_tag(self) -> None:
        yaml_content = """
asyncapi: 3.0.0
info:
  title: test
  version: 1.0.0
channels:
  test:
    address: test/topic
    x-cosalette-requires:
      - description: "missing tag field"
        """
        source = InlineSchemaSource(yaml_content.strip())

        with pytest.raises(SchemaLoadError) as exc_info:
            await load_schema(source)

        assert "must have 'tag' field" in str(exc_info.value)


class TestLoadSchema:
    async def test_load_basic_per_app_schema(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "valid_basic.yaml")
        registry = await load_schema(source)

        assert registry.app_name == "vito2mqtt"
        assert registry.app_version == "0.2.0"
        assert registry.asyncapi_version == "3.0.0"
        assert registry.enforcement.mode == "warn"
        assert "temperatureState" in registry.channels
        assert "publishTemperature" in registry.operations

        # Verify operation links to channel
        op = registry.operations["publishTemperature"]
        assert op.channel_ref == "temperatureState"
        assert op.archetype == "telemetry"

    async def test_load_schema_with_refs(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "valid_with_refs.yaml")
        registry = await load_schema(source)

        # Verify component schemas are preserved
        assert "TemperaturePayload" in registry.component_schemas

        # Verify $ref was resolved in channel payload
        temp_channel = registry.channels["temperatureState"]
        payload = temp_channel.payload_schema
        assert payload is not None
        assert payload["type"] == "object"
        assert "temperature" in payload["properties"]
        assert "unit" in payload["properties"]

    async def test_load_network_schema(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "network_basic.yaml")
        registry = await load_schema(source)

        # Network schema should have app_name=None due to network_level=True
        assert registry.app_name is None
        assert registry.enforcement.network_level is True
        assert registry.enforcement.mode == "strict"

        # Should include channels from both apps + shared
        assert "vitoTemperature" in registry.channels
        assert "airthingsAirQuality" in registry.channels
        assert "appStatus" in registry.channels

        # Verify app-specific channels have correct app_name
        vito_channel = registry.channels["vitoTemperature"]
        assert vito_channel.app_name == "vito2mqtt"

        airthings_channel = registry.channels["airthingsAirQuality"]
        assert airthings_channel.app_name == "airthings2mqtt"

        # Verify shared channel has scope
        status_channel = registry.channels["appStatus"]
        assert status_channel.scope == "all_apps"

    async def test_load_invalid_version(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "invalid_version.yaml")

        with pytest.raises(SchemaLoadError) as exc_info:
            await load_schema(source)

        assert "Unsupported AsyncAPI version" in str(exc_info.value)

    async def test_load_invalid_yaml(self) -> None:
        malformed_yaml = "{ invalid: yaml: content }"
        source = InlineSchemaSource(malformed_yaml)

        with pytest.raises(SchemaLoadError) as exc_info:
            await load_schema(source)

        assert "Failed to parse YAML" in str(exc_info.value)

    async def test_load_consumer_metadata_extracted(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "network_basic.yaml")
        registry = await load_schema(source)

        # Check vito temperature channel has consumer metadata
        vito_channel = registry.channels["vitoTemperature"]
        assert "temperature" in vito_channel.properties

        temp_prop = vito_channel.properties["temperature"]
        assert temp_prop.consumer is not None
        assert temp_prop.consumer.device_class == "temperature"
        assert temp_prop.consumer.unit == "°C"
        assert temp_prop.consumer.display_name == "Heating Water Temperature"
        assert temp_prop.consumer.state_class == "measurement"

        # Check airthings channel has multiple properties with metadata
        air_channel = registry.channels["airthingsAirQuality"]
        assert "co2" in air_channel.properties

        co2_prop = air_channel.properties["co2"]
        assert co2_prop.consumer is not None
        assert co2_prop.consumer.device_class == "carbon_dioxide"
        assert co2_prop.consumer.unit == "ppm"
        assert co2_prop.consumer.display_name == "CO₂"

    async def test_load_operations_linked(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "network_basic.yaml")
        registry = await load_schema(source)

        # Verify operations properly link to channels
        pub_vito_op = registry.operations["publishVitoTemperature"]
        assert pub_vito_op.action == "send"
        assert pub_vito_op.channel_ref == "vitoTemperature"

        recv_vito_op = registry.operations["receiveVitoValveCommand"]
        assert recv_vito_op.action == "receive"
        assert recv_vito_op.channel_ref == "vitoValveCommand"

        # Verify channel directions were inferred from operations
        vito_temp_channel = registry.channels["vitoTemperature"]
        assert vito_temp_channel.direction == "send"

        vito_valve_channel = registry.channels["vitoValveCommand"]
        assert vito_valve_channel.direction == "receive"


class TestFilterForAppIntegration:
    async def test_filter_vito2mqtt_from_network(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "network_basic.yaml")
        registry = await load_schema(source)

        vito_registry = registry.filter_for_app("vito2mqtt")

        # Should include vito channels + shared appStatus
        assert "vitoTemperature" in vito_registry.channels
        assert "vitoValveCommand" in vito_registry.channels
        assert "appStatus" in vito_registry.channels  # scope="all_apps"

        # Should NOT include airthings channels
        assert "airthingsAirQuality" not in vito_registry.channels

        # Should filter operations
        assert "publishVitoTemperature" in vito_registry.operations
        assert "receiveVitoValveCommand" in vito_registry.operations
        assert "publishAirthingsAirQuality" not in vito_registry.operations

    async def test_filter_airthings_from_network(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "network_basic.yaml")
        registry = await load_schema(source)

        airthings_registry = registry.filter_for_app("airthings2mqtt")

        # Should include airthings channels + shared appStatus
        assert "airthingsAirQuality" in airthings_registry.channels
        assert "appStatus" in airthings_registry.channels  # scope="all_apps"

        # Should NOT include vito channels
        assert "vitoTemperature" not in airthings_registry.channels
        assert "vitoValveCommand" not in airthings_registry.channels

        # Should filter operations
        assert "publishAirthingsAirQuality" in airthings_registry.operations
        assert "publishVitoTemperature" not in airthings_registry.operations

    async def test_filter_unknown_app(self, schemas_dir: Path) -> None:
        source = FileSchemaSource(schemas_dir / "network_basic.yaml")
        registry = await load_schema(source)

        unknown_registry = registry.filter_for_app("unknown_app")

        # Should only include shared channels (scope="all_apps")
        assert "appStatus" in unknown_registry.channels
        assert "vitoTemperature" not in unknown_registry.channels
        assert "airthingsAirQuality" not in unknown_registry.channels

        # No operations should remain since no matching channels
        assert len(unknown_registry.operations) == 0
