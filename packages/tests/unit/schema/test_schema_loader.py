"""Unit tests for cosalette._schema._loader — Schema loading and parsing.

Test Techniques Used:
- Specification-based Testing: Verifying load pipeline contracts
- Equivalence Partitioning: Valid schemas, invalid versions, malformed YAML
- Error Guessing: Circular refs, missing keys, bad extensions
- Round-trip Testing: YAML → SchemaRegistry field verification
- Decision Table: _collect_properties variant kinds
  (flat, oneOf, anyOf, allOf, nested, empty, collision)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cosalette._schema._loader import (
    FileSchemaSource,
    InlineSchemaSource,
    SchemaLoadError,
    load_schema,
)
from cosalette._schema._loader_helpers import _collect_properties, _extract_properties


@pytest.fixture
def schemas_dir() -> Path:
    return Path(__file__).parent.parent.parent / "fixtures" / "schemas"


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

    @pytest.mark.parametrize(
        ("yaml_input", "description"),
        [
            ("", "empty document (None)"),
            ("- item1\n- item2", "list instead of mapping"),
            ("42", "scalar integer"),
            ('"just a string"', "scalar string"),
        ],
    )
    async def test_load_non_dict_yaml_raises(
        self, yaml_input: str, description: str
    ) -> None:
        source = InlineSchemaSource(yaml_input)

        with pytest.raises(SchemaLoadError) as exc_info:
            await load_schema(source)

        assert "must be a YAML mapping" in str(exc_info.value), description

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

    async def test_stream_archetype_loads_successfully(self) -> None:
        """x-cosalette-archetype: stream round-trips through the loader."""
        yaml_content = """
asyncapi: 3.0.0
info:
  title: test
  version: 1.0.0
channels:
  readingsState:
    address: myapp/readings/state
    x-cosalette-archetype: stream
    messages:
      message:
        payload:
          type: object
operations:
  publishReadingsState:
    action: send
    channel:
      $ref: "#/channels/readingsState"
    x-cosalette-archetype: stream
""".strip()
        source = InlineSchemaSource(yaml_content)
        registry = await load_schema(source)

        channel = registry.channels["readingsState"]
        assert channel.archetype == "stream"
        op = registry.operations["publishReadingsState"]
        assert op.archetype == "stream"

    async def test_unknown_archetype_raises_schema_load_error(self) -> None:
        """An unknown x-cosalette-archetype value raises SchemaLoadError."""
        yaml_content = """
asyncapi: 3.0.0
info:
  title: test
  version: 1.0.0
channels:
  bogusChannel:
    address: myapp/bogus/state
    x-cosalette-archetype: bogus
""".strip()
        source = InlineSchemaSource(yaml_content)

        with pytest.raises(SchemaLoadError) as exc_info:
            await load_schema(source)

        assert "bogus" in str(exc_info.value)  # rejected value named in error
        assert "stream" in str(exc_info.value)  # valid set listed in error


class TestCollectProperties:
    """Unit tests for the _collect_properties recursive helper.

    Test Techniques Used:
    - Decision Table: each composition keyword (oneOf/anyOf/allOf) and the
      co-presence of direct properties are independent conditions;
      empty/None are boundary cases.
    - Equivalence Partitioning: flat-object, union-only, mixed
      (properties + composition), empty/None.
    - Error Guessing: name collisions, nested composition, non-object
      variants, empty top-level properties alongside composition.
    """

    def test_flat_object_fast_path(self) -> None:
        """Flat object payload returns its properties directly."""
        schema = {"type": "object", "properties": {"temp": {"type": "number"}}}
        result = _collect_properties(schema)
        assert list(result) == ["temp"]
        assert result["temp"] == {"type": "number"}

    def test_none_returns_empty(self) -> None:
        """None input returns empty dict."""
        assert _collect_properties(None) == {}

    def test_empty_dict_returns_empty(self) -> None:
        """Empty dict (falsy) returns empty dict."""
        assert _collect_properties({}) == {}

    def test_oneof_typed_plus_null_variant(self) -> None:
        """oneOf with typed + null-ish variant exposes typed properties."""
        schema = {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "temperature": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                },
                {"type": "null"},  # no properties — contributes nothing
            ]
        }
        result = _collect_properties(schema)
        assert set(result) == {"temperature", "unit"}

    def test_anyof_merges_properties(self) -> None:
        """anyOf merges all variant properties."""
        schema = {
            "anyOf": [
                {"type": "object", "properties": {"a": {"type": "string"}}},
                {"type": "object", "properties": {"b": {"type": "integer"}}},
            ]
        }
        result = _collect_properties(schema)
        assert set(result) == {"a", "b"}

    def test_allof_merges_properties(self) -> None:
        """allOf merges all variant properties."""
        schema = {
            "allOf": [
                {"type": "object", "properties": {"x": {"type": "number"}}},
                {"type": "object", "properties": {"y": {"type": "boolean"}}},
            ]
        }
        result = _collect_properties(schema)
        assert set(result) == {"x", "y"}

    def test_nested_composition_oneof_containing_anyof(self) -> None:
        """Nested: oneOf[typed_model, anyOf[dict, null]] — the vito pattern."""
        # Mirrors real `schema init` output for telemetry+command shared channels.
        schema = {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"flow_temp": {"type": "number"}},
                },
                {
                    "anyOf": [
                        {"type": "object", "additionalProperties": True},
                        {"type": "null"},
                    ]
                },
            ]
        }
        result = _collect_properties(schema)
        # Only the typed variant contributes; the anyOf variants have no properties.
        assert set(result) == {"flow_temp"}

    def test_first_writer_wins_on_collision(self) -> None:
        """When two variants declare the same property name, the first schema wins."""
        typed_schema = {"type": "string", "title": "TypedString"}
        loose_schema = {"type": "string"}  # looser — no title
        schema = {
            "oneOf": [
                {"type": "object", "properties": {"value": typed_schema}},
                {"type": "object", "properties": {"value": loose_schema}},
            ]
        }
        result = _collect_properties(schema)
        # First variant wins.
        assert result["value"] == typed_schema

    def test_no_properties_anywhere_returns_empty(self) -> None:
        """Composition with no variant carrying properties yields empty dict."""
        schema = {
            "oneOf": [
                {"type": "null"},
                {"type": "object", "additionalProperties": True},
            ]
        }
        assert _collect_properties(schema) == {}

    def test_properties_and_oneof_at_same_level_merges_both(self) -> None:
        """Direct properties + oneOf at the same level — both are collected.

        Previously the early-return on 'properties in schema' silently dropped
        the oneOf variants; this test guards that regression.
        Technique: Error Guessing — the exact shape that triggered the bug.
        """
        schema = {
            "properties": {"base": {"type": "string"}},
            "oneOf": [{"type": "object", "properties": {"extra": {"type": "number"}}}],
        }
        result = _collect_properties(schema)
        assert set(result) == {"base", "extra"}

    def test_empty_properties_and_oneof_still_traverses_variants(self) -> None:
        """properties: {} plus oneOf — variants must still be traversed.

        Technique: Boundary Value Analysis — empty direct-properties map
        is the corner-case that silently returned {} before the fix.
        """
        schema = {
            "properties": {},
            "oneOf": [{"type": "object", "properties": {"value": {"type": "integer"}}}],
        }
        result = _collect_properties(schema)
        assert set(result) == {"value"}

    def test_direct_properties_take_precedence_over_variant_on_collision(self) -> None:
        """Direct properties win over same-named variant properties (first-writer).

        Technique: Decision Table — collision between direct-level and
        composition-level properties.
        """
        schema = {
            "properties": {"x": {"type": "string", "title": "Direct"}},
            "allOf": [{"properties": {"x": {"type": "integer", "title": "Variant"}}}],
        }
        result = _collect_properties(schema)
        assert result["x"]["title"] == "Direct"

    def test_allof_flat_and_sub_composition(self) -> None:
        """allOf with a flat variant and a nested anyOf variant.

        Technique: Error Guessing — allOf containing sub-composition
        (mirrors the command-echo anyOf nesting for a different keyword).
        """
        schema = {
            "allOf": [
                {"type": "object", "properties": {"x": {"type": "number"}}},
                {
                    "anyOf": [
                        {"type": "object", "additionalProperties": True},
                        {"type": "null"},
                    ]
                },
            ]
        }
        result = _collect_properties(schema)
        assert set(result) == {"x"}


class TestExtractPropertiesUnionPayload:
    """_extract_properties descends into oneOf/anyOf/allOf for full
    PropertySchema output.

    Test Techniques Used:
    - Specification-based: confirms x-cosalette-consumer metadata is reachable
      through oneOf/anyOf/allOf composition keywords.
    - Equivalence Partitioning: None / flat-object / oneOf / anyOf / allOf
      union-payload input classes.
    - Boundary Value Analysis: empty dict yields empty result.
    - Regression: flat-object produces identical output to pre-FEP behaviour;
      consumer annotations inside oneOf[0] variants are now surfaced (was
      silently dropped before the union-payload traversal fix).
    """

    def test_flat_object_unchanged(self) -> None:
        """Flat-object payload produces PropertySchema exactly as before."""
        schema = {
            "type": "object",
            "properties": {
                "temp": {
                    "type": "number",
                    "x-cosalette-consumer": {
                        "display_name": "Temperature",
                        "device_class": "temperature",
                        "unit": "°C",
                        "state_class": "measurement",
                    },
                }
            },
        }
        result = _extract_properties(schema)
        assert "temp" in result
        assert result["temp"].consumer is not None
        assert result["temp"].consumer.device_class == "temperature"

    def test_oneof_consumer_metadata_reachable(self) -> None:
        """x-cosalette-consumer inside a oneOf variant is extracted into PropertySchema.

        Regression guard: consumer annotations nested under oneOf[0].properties
        were previously silently dropped by the early-return fast-path.
        """
        schema = {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "hot_water_temperature": {
                            "type": "number",
                            "x-cosalette-consumer": {
                                "display_name": "Hot Water Temperature",
                                "device_class": "temperature",
                                "unit": "\u00b0C",
                                "state_class": "measurement",
                            },
                        }
                    },
                },
                {
                    "anyOf": [
                        {"type": "object", "additionalProperties": True},
                        {"type": "null"},
                    ]
                },
            ]
        }
        result = _extract_properties(schema)
        assert "hot_water_temperature" in result, (
            "Property inside oneOf[0] must be reachable by _extract_properties"
        )
        prop = result["hot_water_temperature"]
        assert prop.consumer is not None
        assert prop.consumer.display_name == "Hot Water Temperature"
        assert prop.consumer.device_class == "temperature"
        assert prop.consumer.unit == "\u00b0C"
        assert prop.consumer.state_class == "measurement"

    def test_anyof_consumer_metadata_reachable(self) -> None:
        """x-cosalette-consumer inside an anyOf variant is extracted."""
        schema = {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "flow_temperature": {
                            "type": "number",
                            "x-cosalette-consumer": {
                                "display_name": "Flow Temperature",
                                "device_class": "temperature",
                                "unit": "\u00b0C",
                                "state_class": "measurement",
                            },
                        }
                    },
                },
                {"type": "null"},
            ]
        }
        result = _extract_properties(schema)
        assert "flow_temperature" in result
        prop = result["flow_temperature"]
        assert prop.consumer is not None
        assert prop.consumer.device_class == "temperature"

    def test_allof_consumer_metadata_reachable(self) -> None:
        """x-cosalette-consumer inside an allOf variant is extracted."""
        schema = {
            "allOf": [
                {
                    "type": "object",
                    "properties": {
                        "pump_speed": {
                            "type": "integer",
                            "x-cosalette-consumer": {
                                "display_name": "Pump Speed",
                                "device_class": "power_factor",
                                "unit": "%",
                                "state_class": "measurement",
                            },
                        }
                    },
                }
            ]
        }
        result = _extract_properties(schema)
        assert "pump_speed" in result
        prop = result["pump_speed"]
        assert prop.consumer is not None
        assert prop.consumer.display_name == "Pump Speed"

    def test_none_payload_returns_empty(self) -> None:
        """None payload_schema produces no PropertySchema objects."""
        assert _extract_properties(None) == {}

    def test_empty_dict_returns_empty(self) -> None:
        """Empty dict payload produces no PropertySchema objects.

        Technique: Boundary Value Analysis — empty schema at the function
        boundary confirms the not-schema guard propagates correctly.
        """
        assert _extract_properties({}) == {}


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
