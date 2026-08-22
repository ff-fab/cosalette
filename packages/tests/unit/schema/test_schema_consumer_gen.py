"""Unit tests for cosalette._schema._consumer_gen — consumer code generation.

Test Techniques Used:
    - Equivalence Partitioning: telemetry/command archetypes, JSON types,
      device classes for component inference and OpenHAB type mapping
    - Boundary Value Analysis: empty registries, missing consumer metadata,
      missing overrides, explicit vs inferred values
    - Specification-based Testing: HA discovery payload structure, device
      grouping, unique ID format, value template auto-generation
    - Branch Coverage: send vs receive direction, explicit vs inferred
      component, with/without expire_after and command_template
    - Round-trip Testing: CLI output format verification
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosalette._constants import EXIT_CONFIG_ERROR, EXIT_OK
from cosalette._schema import (
    ChannelSchema,
    ConsumerMetadata,
    EnforcementConfig,
    HaDiscoveryOverrides,
    HaEntitySpec,
    OpenHabOverrides,
    PropertySchema,
    SchemaRegistry,
)
from cosalette._schema._cli import schema_app
from cosalette._schema._consumer_gen import (
    HaDiscoveryGenerator,
    HaDiscoveryPayload,
    OpenHabGenerator,
    _openhab_format_before_publish,
    ha_discovery_to_json,
)
from cosalette._schema._loader import InlineSchemaSource, load_schema
from cosalette._schema._loader_helpers import _build_property_schema
from cosalette.schema import consumer

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    """Typer CLI test runner."""
    return CliRunner()


@pytest.fixture
def schemas_dir() -> Path:
    """Path to test fixture schemas directory."""
    return Path(__file__).parent.parent.parent / "fixtures" / "schemas"


@pytest.fixture
def consumer_schema(schemas_dir: Path) -> Path:
    """Path to consumer test schema."""
    return schemas_dir / "consumer_basic.yaml"


def _make_registry(
    channels: dict[str, ChannelSchema] | None = None,
    *,
    app_version: str = "1.0.0",
    device_names: frozenset[str] = frozenset(),
) -> SchemaRegistry:
    """Build a minimal SchemaRegistry for testing.

    ``device_names`` defaults to empty, which the generator treats as "every
    resolved device is root" (ADR-058) — the right default for tests that
    exercise unrelated behaviour. Tests targeting named-device behaviour
    (per-device blocks, availability merging, the bridge entity) must pass
    the resolved device name(s) explicitly, matching what the real loader's
    ``_extract_device_names`` would have populated.
    """
    return SchemaRegistry(
        app_name=None,
        app_version=app_version,
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="strict", network_level=True),
        channels=channels or {},
        operations={},
        component_schemas={},
        device_names=device_names,
    )


def _temp_channel(
    *,
    address: str = "myapp/sensor/state",
    app_name: str = "myapp",
    archetype: str = "telemetry",
    direction: str = "send",
    properties: dict[str, PropertySchema] | None = None,
    ha_entities: tuple[HaEntitySpec, ...] = (),
    scope: str | None = None,
) -> ChannelSchema:
    """Build a minimal ChannelSchema for testing."""
    return ChannelSchema(
        address=address,
        address_template=address,
        direction=direction,  # ty: ignore[invalid-argument-type]
        app_name=app_name,
        archetype=archetype,  # ty: ignore[invalid-argument-type]
        properties=properties or {},
        ha_entities=ha_entities,
        scope=scope,
    )


def _temp_property(
    *,
    name: str = "temperature",
    json_type: str = "number",
    device_class: str | None = "temperature",
    unit: str | None = "°C",
    display_name: str | None = "Temperature",
    state_class: str | None = "measurement",
    icon: str | None = None,
    ha: HaDiscoveryOverrides | None = None,
    openhab: OpenHabOverrides | None = None,
) -> PropertySchema:
    """Build a PropertySchema with consumer metadata for testing."""
    return PropertySchema(
        name=name,
        json_schema={"type": json_type},
        consumer=ConsumerMetadata(
            device_class=device_class,
            unit=unit,
            display_name=display_name,
            state_class=state_class,
            icon=icon,
        ),
        ha_discovery=ha,
        openhab=openhab,
    )


# ---------------------------------------------------------------------------
# HA Discovery Generator
# ---------------------------------------------------------------------------


class TestHaDiscoveryGenerator:
    """Tests for HaDiscoveryGenerator."""

    def test_generate_sensor_from_telemetry_channel(self) -> None:
        """Telemetry channel with number property → sensor component.

        Technique: Equivalence Partitioning — telemetry + number class.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 1
        p = payloads[0]
        assert "sensor" in p.topic
        assert p.config["device_class"] == "temperature"
        assert p.config["unit_of_measurement"] == "°C"
        assert p.config["state_class"] == "measurement"
        assert p.config["state_topic"] == "myapp/sensor/state"
        assert "command_topic" not in p.config

    def test_generate_binary_sensor_from_boolean_property(self) -> None:
        """Telemetry + boolean → binary_sensor.

        Technique: Equivalence Partitioning — boolean type class.
        """
        prop = _temp_property(
            name="open",
            json_type="boolean",
            device_class="door",
            display_name="Door Sensor",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(properties={"open": prop})
        registry = _make_registry({"door": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 1
        assert "binary_sensor" in payloads[0].topic
        assert payloads[0].config["device_class"] == "door"

    def test_generate_number_from_command_integer(self) -> None:
        """Command + integer → number component.

        Technique: Equivalence Partitioning — command + integer class.
        """
        prop = _temp_property(
            name="position",
            json_type="integer",
            device_class=None,
            display_name="Valve Position",
            unit="%",
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/valve/set",
            archetype="command",
            direction="receive",
            properties={"position": prop},
        )
        registry = _make_registry({"valve": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 1
        assert "number" in payloads[0].topic
        assert payloads[0].config["command_topic"] == "myapp/valve/set"
        assert "state_topic" not in payloads[0].config

    def test_generate_select_from_command_string_enum(self) -> None:
        """Command + string with enum → select component.

        Technique: Equivalence Partitioning — string+enum special case.
        """
        prop = PropertySchema(
            name="mode",
            json_schema={"type": "string", "enum": ["auto", "manual", "off"]},
            consumer=ConsumerMetadata(display_name="Operating Mode"),
        )
        channel = _temp_channel(
            address="myapp/mode/set",
            archetype="command",
            direction="receive",
            properties={"mode": prop},
        )
        registry = _make_registry({"mode": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 1
        assert "select" in payloads[0].topic

    def test_generate_explicit_component_override(self) -> None:
        """ha_discovery.component overrides inferred component on a writable channel.

        Technique: Boundary Value Analysis — explicit override takes precedence.
        A send-only channel always resolves to sensor/binary_sensor regardless of
        ha.component (Bug 1 fix); the override only applies to receive/both channels.
        """
        ha = HaDiscoveryOverrides(component="climate")
        prop = _temp_property(ha=ha)
        channel = _temp_channel(
            address="myapp/thermostat/ctrl",
            archetype="command",
            direction="both",
            properties={"temperature": prop},
        )
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert "climate" in payloads[0].topic

    def test_generate_value_template_auto(self) -> None:
        """Auto-generated value_template uses property name.

        Technique: Specification-based Testing — template format contract.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["value_template"] == "{{ value_json.temperature }}"

    def test_generate_value_template_explicit(self) -> None:
        """Explicit value_template from ha_discovery overrides auto.

        Technique: Boundary Value Analysis — explicit overrides default.
        """
        ha = HaDiscoveryOverrides(value_template="{{ value_json.temp | round(1) }}")
        prop = _temp_property(ha=ha)
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert (
            payloads[0].config["value_template"] == "{{ value_json.temp | round(1) }}"
        )

    def test_generate_command_template(self) -> None:
        """command_template included for receive channels.

        Technique: Branch Coverage — receive direction with command_template.
        """
        ha = HaDiscoveryOverrides(
            component="number",
            command_template='{"position": {{ value }}}',
        )
        prop = _temp_property(
            name="position",
            json_type="integer",
            ha=ha,
            device_class=None,
            display_name="Position",
            unit="%",
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/valve/set",
            archetype="command",
            direction="receive",
            properties={"position": prop},
        )
        registry = _make_registry({"valve": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["command_template"] == '{"position": {{ value }}}'

    def test_generate_device_grouping(self) -> None:
        """Properties from same app share device block.

        Technique: Specification-based Testing — device grouping contract.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        device = payloads[0].config["device"]
        assert device["identifiers"] == ["cosalette_myapp"]
        assert device["name"] == "myapp"
        assert device["manufacturer"] == "cosalette"

    def test_generate_skips_property_without_consumer(self) -> None:
        """Properties without x-cosalette-consumer are skipped.

        Technique: Boundary Value Analysis — absent consumer metadata.
        """
        annotated = _temp_property()
        bare = PropertySchema(name="unit", json_schema={"type": "string"})
        channel = _temp_channel(
            properties={"temperature": annotated, "unit": bare},
        )
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 1
        assert payloads[0].config["name"] == "Temperature"

    def test_generate_empty_registry(self) -> None:
        """Empty registry produces no payloads.

        Technique: Boundary Value Analysis — empty input.
        """
        registry = _make_registry()

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads == []

    def test_generate_unique_id_format(self) -> None:
        """unique_id follows cosalette_{app}_{device}_{property} pattern.

        Technique: Specification-based Testing — unique ID contract.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["unique_id"] == "cosalette_myapp_sensor_temperature"

    def test_generate_expire_after(self) -> None:
        """expire_after from ha_discovery included in config.

        Technique: Branch Coverage — optional field present.
        """
        ha = HaDiscoveryOverrides(expire_after=300)
        prop = _temp_property(ha=ha)
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["expire_after"] == 300

    def test_generate_icon(self) -> None:
        """Consumer icon included in config.

        Technique: Branch Coverage — optional icon present.
        """
        prop = _temp_property(icon="mdi:thermometer")
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["icon"] == "mdi:thermometer"

    def test_generate_skips_all_apps_channels(self) -> None:
        """Channels with scope=all_apps are skipped (framework channels).

        Technique: Boundary Value Analysis — framework channel exclusion.
        """
        channel = _temp_channel(
            address="{appName}/status",
            app_name=None,  # ty: ignore[invalid-argument-type]
        )
        # Create a new ChannelSchema with scope set
        channel = ChannelSchema(
            address="{appName}/status",
            address_template="{appName}/status",
            direction="send",
            scope="all_apps",
        )
        registry = _make_registry({"status": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads == []

    def test_generate_custom_prefix(self) -> None:
        """Custom discovery_prefix changes topic prefix.

        Technique: Specification-based Testing — configurable prefix.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(
            registry=registry, discovery_prefix="custom"
        ).generate()

        assert payloads[0].topic.startswith("custom/")

    def test_generate_multi_property_channel(self) -> None:
        """Channel with multiple annotated properties produces multiple payloads.

        Technique: Branch Coverage — multi-property iteration.
        """
        props = {
            "co2": _temp_property(
                name="co2",
                device_class="carbon_dioxide",
                unit="ppm",
                display_name="CO₂",
            ),
            "humidity": _temp_property(
                name="humidity",
                device_class="humidity",
                unit="%",
                display_name="Humidity",
            ),
        }
        channel = _temp_channel(
            address="airthings2mqtt/airquality/state",
            app_name="airthings2mqtt",
            properties=props,
        )
        registry = _make_registry({"air": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 2
        names = {p.config["name"] for p in payloads}
        assert names == {"CO₂", "Humidity"}


class TestHaDiscoveryPayload:
    """Tests for HaDiscoveryPayload dataclass."""

    def test_payload_immutable(self) -> None:
        """HaDiscoveryPayload is frozen dataclass.

        Technique: Specification-based Testing — immutability contract.
        """
        payload = HaDiscoveryPayload(topic="test", config={"key": "value"})

        with pytest.raises(AttributeError):
            payload.topic = "modified"  # ty: ignore[invalid-assignment]


class TestHaDiscoveryToJson:
    """Tests for ha_discovery_to_json serialization."""

    def test_serializes_payloads(self) -> None:
        """JSON output is a list of {topic, config} objects.

        Technique: Round-trip Testing — serialize and parse back.
        """
        payloads = [
            HaDiscoveryPayload(
                topic="homeassistant/sensor/myapp/temp/config",
                config={"name": "Temperature", "unique_id": "test_123"},
            ),
        ]

        output = ha_discovery_to_json(payloads)
        parsed = json.loads(output)

        assert len(parsed) == 1
        assert parsed[0]["topic"] == "homeassistant/sensor/myapp/temp/config"
        assert parsed[0]["config"]["name"] == "Temperature"

    def test_empty_list(self) -> None:
        """Empty input produces empty JSON array.

        Technique: Boundary Value Analysis — empty input.
        """
        assert json.loads(ha_discovery_to_json([])) == []


# ---------------------------------------------------------------------------
# OpenHAB Generator
# ---------------------------------------------------------------------------


class TestOpenHabGenerator:
    """Tests for OpenHabGenerator."""

    def test_things_from_telemetry_channel(self) -> None:
        """Telemetry channel produces a Thing with channel definitions.

        Technique: Specification-based Testing — .things file structure.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        output = OpenHabGenerator(registry=registry).generate_things()

        assert "Thing mqtt:topic:broker:myapp_sensor" in output
        assert 'stateTopic="myapp/sensor/state"' in output
        assert 'transformationPattern="JSONPATH:$.temperature"' in output
        assert output.startswith("// Generated by cosalette schema openhab")

    def test_things_escapes_dotted_property_name(self) -> None:
        """Non-identifier property names use bracket-notation JSONPATH.

        Technique: Error Guessing — a dotted name must select the top-level key
        via bracket notation, not a nested dot-path, and must not corrupt the
        generated transformationPattern.
        """
        prop = _temp_property(name="hot.water")
        channel = _temp_channel(properties={"hot.water": prop})
        registry = _make_registry({"hw": channel})

        output = OpenHabGenerator(registry=registry).generate_things()

        assert "transformationPattern=\"JSONPATH:$['hot.water']\"" in output
        assert "JSONPATH:$.hot.water" not in output

    def test_things_escapes_quote_in_property_name(self) -> None:
        """A quote in a property name is escaped inside the JSONPATH selector.

        Technique: Error Guessing — a quoting metacharacter must not break the
        double-quoted ``.things`` transformationPattern string.
        """
        prop = _temp_property(name="a'b")
        channel = _temp_channel(properties={"a'b": prop})
        registry = _make_registry({"q": channel})

        output = OpenHabGenerator(registry=registry).generate_things()

        assert r"JSONPATH:$['a\'b']" in output

    def test_things_escapes_quote_in_app_name(self) -> None:
        """A double quote in the app name is escaped in the Thing label.

        An app name containing a double quote must stay inside the quoted
        label instead of breaking out of it.

        Technique: Error Guessing — a quoting metacharacter in the app name must
        not terminate the double-quoted ``.things`` Thing label early.
        """
        prop = _temp_property()
        channel = _temp_channel(app_name='ac"unit', properties={"temperature": prop})
        registry = _make_registry({"q": channel})

        output = OpenHabGenerator(registry=registry).generate_things()

        assert 'ac\\"unit' in output  # quote escaped inside the label
        assert '"ac"unit' not in output  # no raw break-out of the quoted label

    def test_items_from_telemetry_channel(self) -> None:
        """Telemetry channel produces Item definitions.

        Technique: Specification-based Testing — .items file structure.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        output = OpenHabGenerator(registry=registry).generate_items()

        assert "Number:Temperature" in output
        assert "Temperature" in output
        assert 'channel="mqtt:topic:broker:myapp_sensor:temperature"' in output

    def test_items_type_mapping_temperature(self) -> None:
        """Temperature device_class maps to Number:Temperature.

        Technique: Equivalence Partitioning — temperature type class.
        """
        prop = _temp_property(device_class="temperature", unit="°C")
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        output = OpenHabGenerator(registry=registry).generate_items()

        assert "Number:Temperature" in output
        assert "%.1f °C" in output

    def test_items_type_mapping_humidity(self) -> None:
        """Humidity device_class maps to Number:Dimensionless.

        Technique: Equivalence Partitioning — humidity type class.
        """
        prop = _temp_property(
            name="humidity",
            device_class="humidity",
            unit="%",
            display_name="Humidity",
        )
        channel = _temp_channel(properties={"humidity": prop})
        registry = _make_registry({"hum": channel})

        output = OpenHabGenerator(registry=registry).generate_items()

        assert "Number:Dimensionless" in output

    def test_items_explicit_type_override(self) -> None:
        """OpenHAB item_type override takes precedence.

        Technique: Boundary Value Analysis — explicit override.
        """
        oh = OpenHabOverrides(item_type="Dimmer")
        prop = _temp_property(openhab=oh)
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        output = OpenHabGenerator(registry=registry).generate_items()

        assert "Dimmer" in output

    def test_things_command_channel(self) -> None:
        """Command channel produces commandTopic in Thing.

        Technique: Branch Coverage — receive direction.
        """
        prop = _temp_property(
            name="position",
            json_type="integer",
            device_class=None,
            display_name="Position",
            unit="%",
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/valve/set",
            archetype="command",
            direction="receive",
            properties={"position": prop},
        )
        registry = _make_registry({"valve": channel})

        output = OpenHabGenerator(registry=registry).generate_things()

        assert 'commandTopic="myapp/valve/set"' in output

    def test_empty_registry(self) -> None:
        """Empty registry produces header-only output.

        Technique: Boundary Value Analysis — empty input.
        """
        registry = _make_registry()

        things = OpenHabGenerator(registry=registry).generate_things()
        items = OpenHabGenerator(registry=registry).generate_items()

        assert things.startswith("// Generated")
        assert items.startswith("// Generated")
        assert "Thing" not in things
        assert "Number" not in items

    def test_groups_and_tags(self) -> None:
        """OpenHAB groups and tags from overrides.

        Technique: Specification-based Testing — override propagation.
        """
        oh = OpenHabOverrides(
            groups=("gHeating", "gSensors"),
            tags=("Measurement", "Temperature"),
        )
        prop = _temp_property(openhab=oh)
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        output = OpenHabGenerator(registry=registry).generate_items()

        assert "(gHeating, gSensors)" in output
        assert '["Measurement", "Temperature"]' in output

    def test_custom_broker_uid(self) -> None:
        """Custom broker_uid changes Thing UID prefix.

        Technique: Specification-based Testing — configurable broker.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        output = OpenHabGenerator(
            registry=registry, broker_uid="mybroker"
        ).generate_things()

        assert "mqtt:topic:mybroker:myapp_sensor" in output

    def test_skips_all_apps_channels(self) -> None:
        """all_apps channels excluded from output.

        Technique: Boundary Value Analysis — framework channel exclusion.
        """
        channel = ChannelSchema(
            address="{appName}/status",
            address_template="{appName}/status",
            direction="send",
            scope="all_apps",
        )
        registry = _make_registry({"status": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert "Thing" not in things

    def test_openhab_label_override(self) -> None:
        """OpenHAB label override takes precedence over display_name.

        Technique: Boundary Value Analysis — explicit label override.
        """
        oh = OpenHabOverrides(label="Custom Label")
        prop = _temp_property(display_name="Default Name", openhab=oh)
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        output = OpenHabGenerator(registry=registry).generate_items()

        assert "Custom Label" in output
        assert "Default Name" not in output

    def test_deterministic_output_ordering(self) -> None:
        """Output is ordered by channel address then property name.

        Technique: Specification-based Testing — reproducible output.
        """
        prop_b = _temp_property(name="beta", display_name="Beta")
        prop_a = _temp_property(name="alpha", display_name="Alpha")
        ch_z = _temp_channel(
            address="myapp/z_sensor/state",
            properties={"beta": prop_b, "alpha": prop_a},
        )
        ch_a = _temp_channel(
            address="myapp/a_sensor/state",
            properties={"beta": prop_b, "alpha": prop_a},
        )
        # Insert channels in reverse order to verify sorting
        registry = _make_registry({"z": ch_z, "a": ch_a})

        items = OpenHabGenerator(registry=registry).generate_items()
        lines = [ln for ln in items.splitlines() if ln.startswith("Number")]

        # a_sensor channels should come before z_sensor
        a_indices = [i for i, ln in enumerate(lines) if "a_sensor" in ln]
        z_indices = [i for i, ln in enumerate(lines) if "z_sensor" in ln]
        assert max(a_indices) < min(z_indices)

        # Within each channel, alpha before beta
        assert lines[0].index("alpha") < lines[1].index("beta") or (
            "alpha" in lines[0] and "beta" in lines[1]
        )


# ---------------------------------------------------------------------------
# Empty consumer block guard
# ---------------------------------------------------------------------------


class TestEmptyConsumerBlockGuard:
    """An empty x-cosalette-consumer block must not become a discovery entity."""

    def test_empty_block_parsed_as_absent_consumer(self) -> None:
        """An empty consumer() block loads as no consumer metadata at all.

        Technique: Boundary Value Analysis — all-default/empty consumer block.
        """
        prop = _build_property_schema("temperature", {"type": "number", **consumer()})

        assert prop.consumer is None

    def test_empty_block_emits_no_ha_or_openhab_entity(self) -> None:
        """A channel whose only property carries an empty consumer() block
        produces no degenerate (name-only) HA or OpenHAB discovery entity.

        Technique: Boundary Value Analysis — degenerate empty-metadata guard.
        """
        prop = _build_property_schema("temperature", {"type": "number", **consumer()})
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        assert HaDiscoveryGenerator(registry=registry).generate() == []

        things = OpenHabGenerator(registry=registry).generate_things()
        items = OpenHabGenerator(registry=registry).generate_items()
        assert "Thing" not in things
        assert "Number" not in items


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestConsumerGenCli:
    """Tests for ha-discovery and openhab CLI commands."""

    def test_ha_discovery_command_output(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        """ha-discovery produces valid JSON array.

        Technique: Round-trip Testing — CLI output is parseable JSON.
        """
        result = runner.invoke(schema_app, ["ha-discovery", str(consumer_schema)])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "topic" in data[0]
        assert "config" in data[0]

    def test_ha_discovery_invalid_format(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        """ha-discovery rejects unknown format.

        Technique: Error Condition Testing — invalid parameter.
        """
        result = runner.invoke(
            schema_app, ["ha-discovery", str(consumer_schema), "--format", "xml"]
        )

        assert result.exit_code != 0

    def test_openhab_things_output(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        """openhab --output things produces .things syntax.

        Technique: Specification-based Testing — CLI output contract.
        """
        result = runner.invoke(
            schema_app, ["openhab", str(consumer_schema), "--output", "things"]
        )

        assert result.exit_code == 0
        assert "Thing mqtt:topic:" in result.output
        assert "stateTopic=" in result.output

    def test_openhab_items_output(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        """openhab --output items produces .items syntax.

        Technique: Specification-based Testing — CLI output contract.
        """
        result = runner.invoke(
            schema_app, ["openhab", str(consumer_schema), "--output", "items"]
        )

        assert result.exit_code == 0
        assert "Number" in result.output
        assert "channel=" in result.output

    def test_openhab_both_output(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        """openhab --output both includes things and items with separator.

        Technique: Specification-based Testing — both output mode.
        """
        result = runner.invoke(
            schema_app, ["openhab", str(consumer_schema), "--output", "both"]
        )

        assert result.exit_code == 0
        assert "Thing" in result.output
        assert "// ---" in result.output
        assert "channel=" in result.output

    def test_openhab_invalid_output(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        """openhab rejects unknown output mode.

        Technique: Error Condition Testing — invalid parameter.
        """
        result = runner.invoke(
            schema_app, ["openhab", str(consumer_schema), "--output", "xml"]
        )

        assert result.exit_code != 0

    # -- F23: "report, don't shrug" ----------------------------------------

    def test_ha_discovery_exits_nonzero_when_nothing_annotated(
        self, runner: CliRunner, schemas_dir: Path
    ) -> None:
        """A fully-wired, consumer-visible schema with zero annotations exits
        non-zero and warns on stderr instead of silently printing ``[]``
        (F23 item 3 — the jeelink2mqtt/suncast Evidence 3 case).
        """
        no_annotations = schemas_dir / "valid_basic.yaml"

        result = runner.invoke(schema_app, ["ha-discovery", str(no_annotations)])

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "no discovery payloads" in result.stderr

    def test_ha_discovery_succeeds_when_annotated(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        """Sanity check: the non-zero exit is specific to the empty case."""
        result = runner.invoke(schema_app, ["ha-discovery", str(consumer_schema)])

        assert result.exit_code == EXIT_OK

    def test_openhab_exits_nonzero_when_nothing_annotated(
        self, runner: CliRunner, schemas_dir: Path
    ) -> None:
        no_annotations = schemas_dir / "valid_basic.yaml"

        result = runner.invoke(schema_app, ["openhab", str(no_annotations)])

        assert result.exit_code == EXIT_CONFIG_ERROR
        assert "nothing will show up" in result.stderr.lower()

    def test_openhab_succeeds_when_annotated(
        self, runner: CliRunner, consumer_schema: Path
    ) -> None:
        result = runner.invoke(schema_app, ["openhab", str(consumer_schema)])

        assert result.exit_code == EXIT_OK

    def test_ha_discovery_warns_about_unreachable_consumer_annotations(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """A consumer() block nested beyond the loader's one-level descent
        produces a stderr warning naming the affected channel."""
        schema_file = tmp_path / "unreachable.yaml"
        schema_file.write_text(
            """
asyncapi: 3.0.0
info: {title: probe, version: 1.0.0}
channels:
  sensorState:
    address: probe/sensor/state
    x-cosalette-app: probe
    x-cosalette-archetype: telemetry
    messages:
      reading:
        payload:
          type: object
          properties:
            top:
              type: number
              x-cosalette-consumer: {display_name: Top}
            readings:
              type: array
              items:
                type: object
                properties:
                  meta:
                    type: object
                    properties:
                      label:
                        type: string
                        x-cosalette-consumer: {display_name: Label}
""",
            encoding="utf-8",
        )

        result = runner.invoke(schema_app, ["ha-discovery", str(schema_file)])

        # Emits the reachable "top" entity fine, but still warns about "label".
        assert result.exit_code == EXIT_OK
        assert "unreachable positions" in result.stderr
        assert "sensorState" in result.stderr


# ---------------------------------------------------------------------------
# ADR-054 — stream archetype excluded from HA discovery
# ---------------------------------------------------------------------------


class TestHaDiscoveryStreamExclusion:
    """Stream channels are excluded from consumer generation (ADR-054 Q2a).

    Covers both HA discovery and OpenHAB generation — both derive entities from
    the same AsyncAPI channels, so both must skip the stream archetype.

    Test Techniques Used:
        - Specification-based Testing: ADR-054 guard contract.
        - Equivalence Partitioning: stream archetype vs telemetry/device archetypes.
    """

    def test_stream_channel_with_consumer_produces_no_ha_payloads(self) -> None:
        """A stream channel with consumer-annotated properties yields no HA payloads.

        Even when a stream channel carries x-cosalette-consumer metadata, the
        ADR-054 guard must suppress all HA discovery payloads for it.
        """
        prop = _temp_property()
        channel = _temp_channel(
            address="myapp/readings/state",
            archetype="stream",
            direction="send",
            properties={"temperature": prop},
        )
        registry = _make_registry({"readings": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads == [], (
            "Stream channel must produce no HA discovery payloads (ADR-054 Q2a)"
        )

    def test_stream_channel_does_not_suppress_sibling_telemetry(self) -> None:
        """Excluding a stream channel must not suppress adjacent telemetry payloads."""
        prop = _temp_property()
        stream_channel = _temp_channel(
            address="myapp/readings/state",
            archetype="stream",
            direction="send",
            properties={"temperature": prop},
        )
        tel_channel = _temp_channel(
            address="myapp/sensor/state",
            archetype="telemetry",
            direction="send",
            properties={"temperature": prop},
        )
        registry = _make_registry({"readings": stream_channel, "sensor": tel_channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 1
        assert "sensor" in payloads[0].topic

    def test_stream_channel_produces_no_openhab_entities(self) -> None:
        """A stream channel with consumer metadata yields no OpenHAB things/items.

        OpenHAB generation consumes the same AsyncAPI channels as HA discovery,
        so the ADR-054 stream exclusion must apply there too — otherwise a stream
        would silently create OpenHAB entities on regeneration.
        """
        prop = _temp_property()
        channel = _temp_channel(
            address="myapp/readings/state",
            archetype="stream",
            direction="send",
            properties={"temperature": prop},
        )
        registry = _make_registry({"readings": channel})

        things = OpenHabGenerator(registry=registry).generate_things()
        items = OpenHabGenerator(registry=registry).generate_items()

        assert "readings" not in things
        assert "readings" not in items

    def test_stream_channel_does_not_suppress_sibling_openhab_telemetry(self) -> None:
        """Excluding a stream channel must not suppress adjacent OpenHAB telemetry."""
        prop = _temp_property()
        stream_channel = _temp_channel(
            address="myapp/readings/state",
            archetype="stream",
            direction="send",
            properties={"temperature": prop},
        )
        tel_channel = _temp_channel(
            address="myapp/sensor/state",
            archetype="telemetry",
            direction="send",
            properties={"temperature": prop},
        )
        registry = _make_registry({"readings": stream_channel, "sensor": tel_channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert "sensor" in things
        assert "readings" not in things


# ---------------------------------------------------------------------------
# Consumer generator correctness fixes
# ---------------------------------------------------------------------------


class TestHaCorrectnessFixes:
    """HA discovery correctness fixes from the consumer-integration proposal.

    Test Techniques Used:
        - Equivalence Partitioning: optional vs required, state vs command.
        - Specification-based Testing: id/topic/template/constraint contracts.
        - Boundary Value Analysis: explicit override vs derived default.
    """

    def test_optional_command_int_stays_number(self) -> None:
        """anyOf:[integer,null] resolves to integer, not string (F6)."""
        prop = PropertySchema(
            name="kelvin",
            json_schema={"anyOf": [{"type": "integer"}, {"type": "null"}]},
            consumer=ConsumerMetadata(display_name="Colour Temp"),
        )
        channel = _temp_channel(
            address="myapp/bulb/set",
            archetype="command",
            direction="receive",
            properties={"kelvin": prop},
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert "/number/" in payloads[0].topic
        assert "/sensor/" not in payloads[0].topic

    def test_array_value_template_joins(self) -> None:
        """array properties render via a join filter, not a Python repr (F7)."""
        prop = PropertySchema(
            name="rgb",
            json_schema={"type": "array", "items": {"type": "integer"}},
            consumer=ConsumerMetadata(display_name="RGB"),
        )
        channel = _temp_channel(
            address="myapp/desk/state",
            archetype="device",
            direction="send",
            properties={"rgb": prop},
        )
        registry = _make_registry({"c": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["value_template"] == "{{ value_json.rgb | join(',') }}"

    def test_command_entity_carries_cmd_suffix(self) -> None:
        """Command object_id/unique_id/topic carry a _cmd disambiguator (F4)."""
        prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/desk/set",
            archetype="command",
            direction="receive",
            properties={"brightness": prop},
        )
        registry = _make_registry({"desk": channel})

        payload = HaDiscoveryGenerator(registry=registry).generate()[0]

        assert payload.config["object_id"] == "desk_brightness_cmd"
        assert payload.config["unique_id"] == "cosalette_myapp_desk_brightness_cmd"
        assert "desk_brightness_cmd/config" in payload.topic

    def test_state_and_command_do_not_collide(self) -> None:
        """State and command entities for one device+prop get distinct ids (F4)."""
        state_prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        cmd_prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        state_ch = _temp_channel(
            address="myapp/desk/state",
            archetype="device",
            direction="send",
            properties={"brightness": state_prop},
        )
        cmd_ch = _temp_channel(
            address="myapp/desk/set",
            archetype="command",
            direction="receive",
            properties={"brightness": cmd_prop},
        )
        registry = _make_registry({"a": state_ch, "b": cmd_ch})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        object_ids = {p.config["object_id"] for p in payloads}
        assert object_ids == {"desk_brightness", "desk_brightness_cmd"}
        assert len({p.topic for p in payloads}) == 2

    def test_default_command_template_numeric(self) -> None:
        """Numeric command defaults to a JSON envelope command_template (F11)."""
        prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/desk/set",
            archetype="command",
            direction="receive",
            properties={"brightness": prop},
        )
        registry = _make_registry({"desk": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["command_template"] == '{"brightness": {{ value }}}'

    def test_default_command_template_boolean(self) -> None:
        """Boolean command maps ON/OFF \u2192 true/false in the JSON envelope (F11)."""
        prop = PropertySchema(
            name="state",
            json_schema={"type": "boolean"},
            consumer=ConsumerMetadata(display_name="State"),
        )
        channel = _temp_channel(
            address="myapp/sw/set",
            archetype="command",
            direction="receive",
            properties={"state": prop},
        )
        registry = _make_registry({"sw": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["command_template"] == "{\"state\": {{ (value == 'ON') | lower }}}"

    def test_default_command_template_string_quoted(self) -> None:
        """String command uses tojson to safely serialise the value (F11)."""
        prop = PropertySchema(
            name="label",
            json_schema={"type": "string"},
            consumer=ConsumerMetadata(display_name="Label"),
        )
        channel = _temp_channel(
            address="myapp/dev/set",
            archetype="command",
            direction="receive",
            properties={"label": prop},
        )
        registry = _make_registry({"dev": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["command_template"] == '{"label": {{ value | tojson }}}'

    def test_explicit_command_template_wins(self) -> None:
        """An explicit command_template overrides the default (F11)."""
        ha = HaDiscoveryOverrides(command_template='{"x": {{ value }}}')
        prop = _temp_property(
            name="brightness",
            json_type="integer",
            ha=ha,
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/desk/set",
            archetype="command",
            direction="receive",
            properties={"brightness": prop},
        )
        registry = _make_registry({"desk": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["command_template"] == '{"x": {{ value }}}'

    def test_number_emits_min_max_step(self) -> None:
        """number entities carry min/max/step from schema constraints (F14)."""
        prop = PropertySchema(
            name="brightness",
            json_schema={
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
                "multipleOf": 1,
            },
            consumer=ConsumerMetadata(display_name="Brightness"),
        )
        channel = _temp_channel(
            address="myapp/desk/set",
            archetype="command",
            direction="receive",
            properties={"brightness": prop},
        )
        registry = _make_registry({"desk": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["min"] == 0
        assert cfg["max"] == 255
        assert cfg["step"] == 1

    def test_select_emits_options(self) -> None:
        """select entities carry options from enum (F15)."""
        prop = PropertySchema(
            name="scene",
            json_schema={"type": "string", "enum": ["ocean", "party"]},
            consumer=ConsumerMetadata(display_name="Scene"),
        )
        channel = _temp_channel(
            address="myapp/desk/set",
            archetype="command",
            direction="receive",
            properties={"scene": prop},
        )
        registry = _make_registry({"desk": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["options"] == ["ocean", "party"]

    def test_command_string_maps_to_text(self) -> None:
        """Writable string command → text platform, not sensor (overhaul step 2)."""
        prop = PropertySchema(
            name="label",
            json_schema={"type": "string"},
            consumer=ConsumerMetadata(display_name="Label"),
        )
        channel = _temp_channel(
            address="myapp/dev/set",
            archetype="command",
            direction="receive",
            properties={"label": prop},
        )
        registry = _make_registry({"dev": channel})

        payload = HaDiscoveryGenerator(registry=registry).generate()[0]

        assert "/text/" in payload.topic
        assert payload.config["command_topic"] == "myapp/dev/set"
        assert "state_topic" not in payload.config

    def test_read_only_forces_read_only_entity(self) -> None:
        """read_only boolean command → state-only binary_sensor (F17)."""
        prop = PropertySchema(
            name="locked",
            json_schema={"type": "boolean"},
            consumer=ConsumerMetadata(display_name="Locked", read_only=True),
        )
        channel = _temp_channel(
            address="myapp/dev/set",
            archetype="command",
            direction="receive",
            properties={"locked": prop},
        )
        registry = _make_registry({"dev": channel})

        payload = HaDiscoveryGenerator(registry=registry).generate()[0]

        assert "/binary_sensor/" in payload.topic
        assert payload.config["state_topic"] == "myapp/dev/set"
        assert "command_topic" not in payload.config
        assert payload.config["object_id"] == "dev_locked"

    def test_platform_key_filtering_binary_sensor(self) -> None:
        """binary_sensor drops unit_of_measurement and state_class (overhaul step 4)."""
        prop = _temp_property(
            name="motion",
            json_type="boolean",
            device_class="motion",
            display_name="Motion",
            unit="%",
            state_class="measurement",
        )
        channel = _temp_channel(
            address="myapp/dev/state",
            archetype="telemetry",
            direction="send",
            properties={"motion": prop},
        )
        registry = _make_registry({"dev": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["device_class"] == "motion"
        assert "unit_of_measurement" not in cfg
        assert "state_class" not in cfg

    def test_nested_device_address_resolves(self) -> None:
        """Nested address app/room/device/state resolves device to room/device (F5)."""
        prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/livingroom/ceiling/state",
            archetype="device",
            direction="send",
            properties={"brightness": prop},
        )
        registry = _make_registry({"c": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["object_id"] == "livingroom_ceiling_brightness"

    def test_bidirectional_command_channel_gets_cmd_suffix(self) -> None:
        """direction='both' + archetype='command' gets the _cmd suffix (F4)."""
        prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/desk/ctrl",
            archetype="command",
            direction="both",
            properties={"brightness": prop},
        )
        registry = _make_registry({"desk": channel})

        payload = HaDiscoveryGenerator(registry=registry).generate()[0]

        assert payload.config["object_id"] == "desk_brightness_cmd"
        assert payload.config["unique_id"] == "cosalette_myapp_desk_brightness_cmd"

    def test_bidirectional_command_channel_has_both_topics(self) -> None:
        """direction='both' command channel emits state_topic and command_topic."""
        prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="myapp/desk/ctrl",
            archetype="command",
            direction="both",
            properties={"brightness": prop},
        )
        registry = _make_registry({"desk": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["state_topic"] == "myapp/desk/ctrl"
        assert cfg["command_topic"] == "myapp/desk/ctrl"

    def test_constrained_integer_allof_stays_number(self) -> None:
        """allOf:[{type:integer,minimum:0}] resolves to integer, not string (F6)."""
        prop = PropertySchema(
            name="brightness",
            json_schema={"allOf": [{"type": "integer", "minimum": 0}]},
            consumer=ConsumerMetadata(display_name="Brightness"),
        )
        channel = _temp_channel(
            address="myapp/desk/set",
            archetype="command",
            direction="receive",
            properties={"brightness": prop},
        )
        registry = _make_registry({"desk": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert "/number/" in payloads[0].topic
        assert "/sensor/" not in payloads[0].topic


class TestOpenHabCorrectnessFixes:
    """OpenHAB correctness fixes from the consumer-integration proposal.

    Test Techniques Used:
        - Specification-based Testing: Thing/Item identity and channel params.
        - Branch Coverage: state vs command channel emission.
    """

    def _bidirectional_registry(self) -> SchemaRegistry:
        """One device with a boolean state channel and an integer command channel."""
        state_prop = _temp_property(
            name="state",
            json_type="boolean",
            device_class=None,
            display_name="Desk Lamp",
            unit=None,
            state_class=None,
        )
        cmd_prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        state_ch = _temp_channel(
            address="wiz/desk/state",
            app_name="wiz",
            archetype="device",
            direction="send",
            properties={"state": state_prop},
        )
        cmd_ch = _temp_channel(
            address="wiz/desk/set",
            app_name="wiz",
            archetype="command",
            direction="receive",
            properties={"brightness": cmd_prop},
        )
        return _make_registry({"s": state_ch, "c": cmd_ch})

    def test_one_thing_per_device(self) -> None:
        """A device with state+command channels yields one Thing block (F1)."""
        things = OpenHabGenerator(
            registry=self._bidirectional_registry()
        ).generate_things()

        assert things.count("mqtt:topic:broker:wiz_desk") == 1
        assert 'stateTopic="wiz/desk/state"' in things
        assert 'commandTopic="wiz/desk/set"' in things

    def test_command_item_direction_aware(self) -> None:
        """Command Item ends _Cmd and links to _cmd channel; no duplicates (F2, F3)."""
        state_prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        cmd_prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        state_ch = _temp_channel(
            address="wiz/desk/state",
            app_name="wiz",
            archetype="device",
            direction="send",
            properties={"brightness": state_prop},
        )
        cmd_ch = _temp_channel(
            address="wiz/desk/set",
            app_name="wiz",
            archetype="command",
            direction="receive",
            properties={"brightness": cmd_prop},
        )
        registry = _make_registry({"s": state_ch, "c": cmd_ch})

        items = OpenHabGenerator(registry=registry).generate_items()

        assert "Wiz_Desk_Brightness " in items
        assert "Wiz_Desk_Brightness_Cmd " in items
        assert ':brightness" }' in items
        assert ':brightness_cmd" }' in items
        assert items.count("Wiz_Desk_Brightness ") == 1

    def test_switch_channel_has_on_off(self) -> None:
        """Boolean switch channels carry on/off so JSON booleans aren't UNDEF (F8)."""
        prop = _temp_property(
            name="state",
            json_type="boolean",
            device_class=None,
            display_name="Lamp",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="wiz/desk/state",
            app_name="wiz",
            archetype="device",
            direction="send",
            properties={"state": prop},
        )
        registry = _make_registry({"s": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert 'on="true"' in things
        assert 'off="false"' in things

    def test_command_channel_uses_format_before_publish(self) -> None:
        """Command channels emit formatBeforePublish, not an inbound transform (F12)."""
        prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="wiz/desk/set",
            app_name="wiz",
            archetype="command",
            direction="receive",
            properties={"brightness": prop},
        )
        registry = _make_registry({"c": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert 'formatBeforePublish="{\\"brightness\\":%s}"' in things
        assert "transformationPattern" not in things

    def test_read_only_skips_command_channel(self) -> None:
        """read_only emits only the state channel/item, never _cmd (F17)."""
        prop = PropertySchema(
            name="locked",
            json_schema={"type": "boolean"},
            consumer=ConsumerMetadata(display_name="Locked", read_only=True),
        )
        channel = _temp_channel(
            address="wiz/dev/set",
            app_name="wiz",
            archetype="command",
            direction="receive",
            properties={"locked": prop},
        )
        registry = _make_registry({"c": channel})

        things = OpenHabGenerator(registry=registry).generate_things()
        items = OpenHabGenerator(registry=registry).generate_items()

        assert "_cmd" not in things
        assert 'stateTopic="wiz/dev/set"' in things
        assert "Wiz_Dev_Locked " in items
        assert "Wiz_Dev_Locked_Cmd" not in items

    def test_nested_device_thing_and_item_ids(self) -> None:
        """Nested address resolves to slugified room_device in things/items (F5)."""
        prop = _temp_property(
            name="brightness",
            json_type="integer",
            device_class=None,
            display_name="Brightness",
            unit=None,
            state_class=None,
        )
        channel = _temp_channel(
            address="wiz/livingroom/ceiling/state",
            app_name="wiz",
            archetype="device",
            direction="send",
            properties={"brightness": prop},
        )
        registry = _make_registry({"c": channel})

        things = OpenHabGenerator(registry=registry).generate_things()
        items = OpenHabGenerator(registry=registry).generate_items()

        assert "mqtt:topic:broker:wiz_livingroom_ceiling" in things
        assert "Wiz_LivingroomCeiling_Brightness" in items

    def test_boolean_command_channel_format_before_publish(self) -> None:
        """Boolean command: %s + on/off=true/false produces valid JSON (F8, F12)."""
        prop = PropertySchema(
            name="power",
            json_schema={"type": "boolean"},
            consumer=ConsumerMetadata(display_name="Power"),
        )
        channel = _temp_channel(
            address="wiz/dev/set",
            app_name="wiz",
            archetype="command",
            direction="receive",
            properties={"power": prop},
        )
        registry = _make_registry({"c": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert 'formatBeforePublish="{\\"power\\":%s}"' in things
        assert 'on="true"' in things
        assert 'off="false"' in things


# ---------------------------------------------------------------------------
# Typed override surface (ADR-056): extra passthrough, channel_type/params
# ---------------------------------------------------------------------------


class TestHaExtraPassthrough:
    """x-cosalette-ha-discovery.extra merges into the HA payload last (F13)."""

    def test_extra_adds_keys_the_curated_field_map_does_not_cover(self) -> None:
        """extra reaches HA platform keys with no curated equivalent.

        Technique: Boundary Value Analysis — key absent from the curated set.
        """
        ha = HaDiscoveryOverrides(extra={"schema": "json", "optimistic": False})
        prop = _temp_property(ha=ha)
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["schema"] == "json"
        assert payloads[0].config["optimistic"] is False

    def test_extra_overrides_a_curated_key(self) -> None:
        """extra is merged last, so it can override a curated default.

        Technique: Boundary Value Analysis — passthrough vs. curated precedence.
        """
        ha = HaDiscoveryOverrides(extra={"unit_of_measurement": "kWh"})
        prop = _temp_property(ha=ha, unit="%")
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["unit_of_measurement"] == "kWh"

    def test_no_extra_leaves_config_unchanged(self) -> None:
        """Absent/empty extra does not add any keys.

        Technique: Boundary Value Analysis — default (empty) passthrough.
        """
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert "schema" not in payloads[0].config


class TestOpenHabChannelTypeOverride:
    """x-cosalette-openhab.channel_type overrides the inferred channel type.

    (F9, F21)
    """

    def test_channel_type_override_takes_precedence(self) -> None:
        """An array field can be bound to a native Color channel.

        Technique: Boundary Value Analysis — explicit override on a type
        (array) the inference table has no entry for.
        """
        oh = OpenHabOverrides(item_type="Color", channel_type="color")
        prop = _temp_property(name="hsb", json_type="array", openhab=oh)
        channel = _temp_channel(properties={"hsb": prop})
        registry = _make_registry({"hsb": channel})

        things = OpenHabGenerator(registry=registry).generate_things()
        items = OpenHabGenerator(registry=registry).generate_items()

        assert "Type color : hsb" in things
        assert "Color" in items

    def test_no_override_falls_back_to_inference(self) -> None:
        """Without channel_type, inference still applies (regression guard).

        Technique: Boundary Value Analysis — default (absent) override.
        """
        prop = _temp_property(name="power", json_type="boolean")
        channel = _temp_channel(properties={"power": prop})
        registry = _make_registry({"p": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert "Type switch : power" in things


class TestOpenHabChannelParams:
    """x-cosalette-openhab.channel_params merges into the .things channel (F21)."""

    def test_channel_params_adds_a_new_parameter(self) -> None:
        """channel_params can add a parameter the generator never computes.

        Technique: Specification-based Testing — new-key passthrough.
        """
        oh = OpenHabOverrides(channel_params={"colorMode": "HSB"})
        prop = _temp_property(name="hsb", json_type="array", openhab=oh)
        channel = _temp_channel(properties={"hsb": prop})
        registry = _make_registry({"hsb": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert 'colorMode="HSB"' in things

    def test_channel_params_overrides_a_computed_default_in_place(self) -> None:
        """channel_params can override a computed default (e.g. on/off).

        Technique: Boundary Value Analysis — passthrough vs. computed precedence.
        """
        oh = OpenHabOverrides(channel_params={"on": "1", "off": "0"})
        prop = _temp_property(name="state", json_type="boolean", openhab=oh)
        channel = _temp_channel(properties={"state": prop})
        registry = _make_registry({"s": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert 'on="1"' in things
        assert 'off="0"' in things
        assert 'on="true"' not in things

    def test_channel_params_numeric_values_are_unquoted(self) -> None:
        """Numeric channel_params render bare, matching openHAB's own style.

        Technique: Equivalence Partitioning — numeric value class.
        """
        oh = OpenHabOverrides(channel_params={"min": 0, "max": 255, "step": 1})
        prop = _temp_property(name="brightness", json_type="integer", openhab=oh)
        channel = _temp_channel(
            address="wiz/desk/set",
            archetype="command",
            direction="receive",
            properties={"brightness": prop},
        )
        registry = _make_registry({"c": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert "min=0" in things
        assert "max=255" in things
        assert "step=1" in things

    def test_no_channel_params_leaves_computed_params_unchanged(self) -> None:
        """Absent/empty channel_params does not alter the computed parameter set.

        Technique: Boundary Value Analysis — default (empty) passthrough.
        """
        prop = _temp_property(name="power", json_type="boolean")
        channel = _temp_channel(properties={"power": prop})
        registry = _make_registry({"p": channel})

        things = OpenHabGenerator(registry=registry).generate_things()

        assert 'on="true"' in things
        assert 'off="false"' in things


class TestCompositeHaEntities:
    """Channel-level x-cosalette-ha-discovery.entities (F10, F20, ADR-057)."""

    def test_composite_entity_replaces_per_property_scatter(self) -> None:
        """A channel with ha_entities emits no scalar per-property payloads.

        Technique: Boundary Value Analysis — composite vs. scalar exclusivity.
        """
        prop = _temp_property(name="brightness")
        channel = _temp_channel(
            properties={"brightness": prop},
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 1
        assert payloads[0].config["name"] == "Desk Lamp"

    def test_send_only_channel_gets_state_topic_only(self) -> None:
        channel = _temp_channel(
            address="myapp/bulb/state",
            direction="send",
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["state_topic"] == "myapp/bulb/state"
        assert "command_topic" not in payloads[0].config

    def test_light_component_defaults_to_json_schema(self) -> None:
        channel = _temp_channel(
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),)
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["schema"] == "json"

    def test_climate_component_drops_generic_state_and_command_topics(self) -> None:
        channel = _temp_channel(
            address="myapp/thermostat/state",
            direction="both",
            ha_entities=(HaEntitySpec(component="climate", name="Thermostat"),),
        )
        registry = _make_registry({"thermostat": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert "state_topic" not in payloads[0].config
        assert "command_topic" not in payloads[0].config

    def test_cover_component_keeps_inherited_topics(self) -> None:
        channel = _temp_channel(
            address="myapp/blind/state",
            direction="both",
            ha_entities=(HaEntitySpec(component="cover", name="Blind"),),
        )
        registry = _make_registry({"blind": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["state_topic"] == "myapp/blind/state"
        assert payloads[0].config["command_topic"] == "myapp/blind/state"

    def test_unrecognized_component_gets_no_extra_defaults(self) -> None:
        channel = _temp_channel(
            address="myapp/fan/state",
            direction="both",
            ha_entities=(HaEntitySpec(component="fan", name="Fan"),),
        )
        registry = _make_registry({"fan": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["state_topic"] == "myapp/fan/state"
        assert payloads[0].config["command_topic"] == "myapp/fan/state"
        assert "schema" not in payloads[0].config

    def test_extra_overrides_computed_defaults_last(self) -> None:
        """extra is merged after the component builder, mirroring
        HaDiscoveryOverrides.extra's override-last semantics (ADR-056).
        """
        channel = _temp_channel(
            ha_entities=(
                HaEntitySpec(
                    component="light",
                    name="Desk Lamp",
                    extra={"schema": "template", "brightness": True},
                ),
            )
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["schema"] == "template"
        assert payloads[0].config["brightness"] is True

    def test_discovery_topic_uses_entity_component(self) -> None:
        channel = _temp_channel(
            address="myapp/bulb/state",
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].topic == "homeassistant/light/myapp/bulb_desk_lamp/config"
        assert payloads[0].config["object_id"] == "bulb_desk_lamp"
        assert payloads[0].config["unique_id"] == "cosalette_myapp_bulb_desk_lamp"

    def test_state_and_set_channels_merge_into_one_entity(self) -> None:
        """A device archetype's paired /state (send) and /set (receive) channels
        share one payload model (ADR-055) and must merge into one composite
        entity rather than emitting two incomplete configs.
        """
        spec = HaEntitySpec(component="light", name="Desk Lamp")
        state_channel = _temp_channel(
            address="myapp/bulb/state",
            archetype="device",
            direction="send",
            ha_entities=(spec,),
        )
        set_channel = _temp_channel(
            address="myapp/bulb/set",
            archetype="device",
            direction="receive",
            ha_entities=(spec,),
        )
        registry = _make_registry({"state": state_channel, "set": set_channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert len(payloads) == 1
        assert payloads[0].config["state_topic"] == "myapp/bulb/state"
        assert payloads[0].config["command_topic"] == "myapp/bulb/set"

    def test_distinct_entities_on_same_channel_produce_separate_payloads(self) -> None:
        channel = _temp_channel(
            ha_entities=(
                HaEntitySpec(component="light", name="Desk Lamp"),
                HaEntitySpec(component="sensor", name="Signal Strength"),
            )
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        names = {p.config["name"] for p in payloads}
        assert names == {"Desk Lamp", "Signal Strength"}

    def test_composite_entity_carries_device_block(self) -> None:
        channel = _temp_channel(
            app_name="wiz2mqtt",
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["device"] == {
            "identifiers": ["cosalette_wiz2mqtt"],
            "name": "wiz2mqtt",
            "manufacturer": "cosalette",
        }

    def test_stream_channel_with_entities_is_excluded(self) -> None:
        """Composite entities respect the same consumer-visibility gate (ADR-054)
        as scalar entities — a stream channel produces nothing either way.
        """
        channel = _temp_channel(
            archetype="stream",
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads == []

    def test_receive_only_channel_gets_command_topic_only(self) -> None:
        """Boundary Value Analysis — directional symmetry with send-only path."""
        channel = _temp_channel(
            address="myapp/bulb/set",
            direction="receive",
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["command_topic"] == "myapp/bulb/set"
        assert "state_topic" not in payloads[0].config

    def test_composite_entity_without_name_falls_back_to_device_and_component(
        self,
    ) -> None:
        """Boundary Value Analysis — absent optional name field."""
        channel = _temp_channel(
            address="myapp/bulb/state",
            ha_entities=(HaEntitySpec(component="light"),),
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["name"] == "bulb"
        assert payloads[0].config["object_id"] == "bulb_light"

    def test_all_apps_scoped_channel_with_entities_is_excluded(self) -> None:
        """Equivalence Partitioning — visibility-gate parity with scalar path."""
        channel = _temp_channel(
            scope="all_apps",
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel})

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads == []


# ---------------------------------------------------------------------------
# ADR-058 — availability keys and per-device device modelling
# ---------------------------------------------------------------------------


class TestHaAvailabilityAndDeviceModelling:
    """Tests for ADR-058: availability keys (F18) and per-device blocks (F19)."""

    def _named_payload(self) -> HaDiscoveryPayload:
        """One scalar sensor payload for a named device ('sensor')."""
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel}, device_names=frozenset({"sensor"}))
        return HaDiscoveryGenerator(registry=registry).generate()[0]

    def _root_payload(self) -> HaDiscoveryPayload:
        """One scalar sensor payload for a root (unnamed) device."""
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel})
        return HaDiscoveryGenerator(registry=registry).generate()[0]

    # -- availability (F18) ------------------------------------------------

    def test_root_device_gets_multi_topic_availability(self) -> None:
        """Boundary Value Analysis — root device uses dual-topic availability
        (ADR-058 F18)."""
        config = self._root_payload().config

        assert config["availability"] == [
            {"topic": "myapp/availability"},
            {
                "topic": "myapp/status",
                "value_template": (
                    "{{ value_json.status if value_json is mapping else value }}"
                ),
            },
        ]
        assert config["availability_mode"] == "all"
        assert config["payload_available"] == "online"
        assert config["payload_not_available"] == "offline"
        assert "availability_topic" not in config

    def test_named_device_gets_multi_topic_availability(self) -> None:
        """Specification-based Testing — ADR-058 device+app-status merge."""
        config = self._named_payload().config

        assert config["availability"] == [
            {"topic": "myapp/sensor/availability"},
            {
                "topic": "myapp/status",
                "value_template": (
                    "{{ value_json.status if value_json is mapping else value }}"
                ),
            },
        ]
        assert config["availability_mode"] == "all"
        assert config["payload_available"] == "online"
        assert config["payload_not_available"] == "offline"
        assert "availability_topic" not in config

    # -- per-device device blocks (F19) -------------------------------------

    def test_root_device_uses_bridge_identity_directly(self) -> None:
        """Boundary Value Analysis — root device has no via_device layer."""
        device = self._root_payload().config["device"]

        assert device == {
            "identifiers": ["cosalette_myapp"],
            "name": "myapp",
            "manufacturer": "cosalette",
        }

    def test_named_device_gets_own_block_with_via_device(self) -> None:
        """Specification-based Testing — ADR-058 per-device device block."""
        device = self._named_payload().config["device"]

        assert device == {
            "identifiers": ["cosalette_myapp_sensor"],
            "name": "sensor",
            "manufacturer": "cosalette",
            "via_device": "cosalette_myapp",
        }

    def test_two_named_devices_get_distinct_device_blocks(self) -> None:
        """Specification-based Testing — Finding 19's headline symptom fixed."""
        blind_prop = _temp_property(name="position")
        window_prop = _temp_property(name="position")
        blind = _temp_channel(
            address="velux2mqtt/blind/state",
            app_name="velux2mqtt",
            properties={"position": blind_prop},
        )
        window = _temp_channel(
            address="velux2mqtt/window/state",
            app_name="velux2mqtt",
            properties={"position": window_prop},
        )
        registry = _make_registry(
            {"blind": blind, "window": window},
            device_names=frozenset({"blind", "window"}),
        )

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        entity_payloads = [p for p in payloads if p.config["object_id"] != "bridge"]
        identifiers = {p.config["device"]["identifiers"][0] for p in entity_payloads}
        assert identifiers == {
            "cosalette_velux2mqtt_blind",
            "cosalette_velux2mqtt_window",
        }

    # -- origin block (F19) --------------------------------------------------

    def test_origin_block_carries_app_version(self) -> None:
        """Specification-based Testing — origin surfaces registry.app_version."""
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"temp": channel}, app_version="2.3.1")

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads[0].config["origin"] == {"name": "myapp", "sw_version": "2.3.1"}

    # -- bridge entity (F19) --------------------------------------------------

    def test_bridge_entity_emitted_for_app_with_named_device(self) -> None:
        """Specification-based Testing — via_device needs a real bridge entity."""
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry(
            {"temp": channel}, app_version="2.3.1", device_names=frozenset({"sensor"})
        )

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        bridge = next(p for p in payloads if p.config["object_id"] == "bridge")
        assert bridge.topic == "homeassistant/binary_sensor/myapp/bridge/config"
        assert bridge.config["unique_id"] == "cosalette_myapp_bridge"
        assert bridge.config["state_topic"] == "myapp/status"
        assert bridge.config["value_template"] == (
            "{{ 'ON' if (value_json.status if value_json is mapping else value)"
            " == 'online' else 'OFF' }}"
        )
        assert bridge.config["device_class"] == "connectivity"
        assert bridge.config["entity_category"] == "diagnostic"
        assert bridge.config["device"] == {
            "identifiers": ["cosalette_myapp"],
            "name": "myapp",
            "manufacturer": "cosalette",
        }
        assert bridge.config["origin"] == {"name": "myapp", "sw_version": "2.3.1"}

    def test_no_bridge_entity_when_every_device_is_root(self) -> None:
        """Boundary Value Analysis — no via_device link needed, no bridge emitted."""
        payloads_config = [p.config for p in [self._root_payload()]]

        assert not any(c["object_id"] == "bridge" for c in payloads_config)

    def test_no_bridge_when_named_device_channel_has_no_annotated_properties(
        self,
    ) -> None:
        """Boundary Value Analysis — named-device channel with no consumer metadata."""
        bare_prop = PropertySchema(name="raw", json_schema={"type": "string"})
        channel = _temp_channel(
            address="myapp/sensor/state",
            properties={"raw": bare_prop},
        )
        registry = _make_registry({"s": channel}, device_names=frozenset({"sensor"}))

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        assert payloads == []

    def test_one_bridge_entity_per_app_with_multiple_named_devices(self) -> None:
        """Boundary Value Analysis — bridge is deduplicated per app, not per device."""
        blind_prop = _temp_property(name="position")
        window_prop = _temp_property(name="position")
        blind = _temp_channel(
            address="velux2mqtt/blind/state",
            app_name="velux2mqtt",
            properties={"position": blind_prop},
        )
        window = _temp_channel(
            address="velux2mqtt/window/state",
            app_name="velux2mqtt",
            properties={"position": window_prop},
        )
        registry = _make_registry(
            {"blind": blind, "window": window},
            device_names=frozenset({"blind", "window"}),
        )

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        bridges = [p for p in payloads if p.config["object_id"] == "bridge"]
        assert len(bridges) == 1

    # -- composite entities (ADR-057 interaction) ----------------------------

    def test_composite_entity_named_device_gets_availability_and_device_block(
        self,
    ) -> None:
        """Specification-based Testing — ADR-058 applies uniformly to composites."""
        channel = _temp_channel(
            address="myapp/bulb/state",
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel}, device_names=frozenset({"bulb"}))

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        light = next(p for p in payloads if p.config["name"] == "Desk Lamp")
        assert light.config["availability_mode"] == "all"
        assert light.config["device"] == {
            "identifiers": ["cosalette_myapp_bulb"],
            "name": "bulb",
            "manufacturer": "cosalette",
            "via_device": "cosalette_myapp",
        }
        assert light.config["origin"] == {"name": "myapp", "sw_version": "1.0.0"}
        assert any(p.config["object_id"] == "bridge" for p in payloads)

    def test_composite_extra_overrides_availability_and_device(self) -> None:
        """Specification-based Testing — extra stays override-last (ADR-056/057)."""
        channel = _temp_channel(
            address="myapp/bulb/state",
            ha_entities=(
                HaEntitySpec(
                    component="light",
                    name="Desk Lamp",
                    extra={"availability_mode": "any", "device": {"custom": True}},
                ),
            ),
        )
        registry = _make_registry({"bulb": channel}, device_names=frozenset({"bulb"}))

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        light = next(p for p in payloads if p.config["name"] == "Desk Lamp")
        assert light.config["availability_mode"] == "any"
        assert light.config["device"] == {"custom": True}


# ---------------------------------------------------------------------------
# F23 — enrichment hook
# ---------------------------------------------------------------------------


class TestEnrichmentHook:
    """App.discovery(enrich=...) hook wiring in HaDiscoveryGenerator (F23)."""

    def test_enrich_called_for_scalar_payload_with_channel_and_prop(self) -> None:
        """Technique: Specification-based Testing — args are (channel, prop, config)."""
        seen: list[tuple[ChannelSchema, PropertySchema | None, dict[str, object]]] = []

        def _enrich(channel: ChannelSchema, prop, config: dict[str, object]) -> None:  # noqa: ANN001
            seen.append((channel, prop, config))
            config["enriched"] = True

        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"sensor": channel})

        payloads = HaDiscoveryGenerator(registry=registry, enrich=_enrich).generate()

        assert len(seen) == 1
        seen_channel, seen_prop, seen_config = seen[0]
        assert seen_channel is channel
        assert seen_prop is prop
        assert seen_config is payloads[0].config
        assert payloads[0].config["enriched"] is True

    def test_enrich_receives_none_prop_for_composite_entity(self) -> None:
        """Technique: Boundary-value Analysis — composite entities have no one prop."""
        seen_props: list[object] = []

        def _enrich(channel, prop, config: dict[str, object]) -> None:  # noqa: ANN001
            seen_props.append(prop)

        channel = _temp_channel(
            address="myapp/bulb/state",
            ha_entities=(HaEntitySpec(component="light", name="Desk Lamp"),),
        )
        registry = _make_registry({"bulb": channel}, device_names=frozenset({"bulb"}))

        HaDiscoveryGenerator(registry=registry, enrich=_enrich).generate()

        # One call, for the composite light entity — the synthetic per-app
        # bridge entity (ADR-058) has no source channel/property at all, so
        # the hook is never invoked for it.
        assert seen_props == [None]

    def test_enrich_runs_after_extra_passthrough_so_it_has_the_final_word(self) -> None:
        """Technique: Specification-based Testing — enrich overrides extra (ADR-056)."""

        def _enrich(channel, prop, config: dict[str, object]) -> None:  # noqa: ANN001
            config["name"] = "Overridden by enrich"

        prop = _temp_property(
            ha=HaDiscoveryOverrides(extra={"name": "Overridden by extra"})
        )
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"sensor": channel})

        payloads = HaDiscoveryGenerator(registry=registry, enrich=_enrich).generate()

        sensor = next(p for p in payloads if "temperature" in p.topic)
        assert sensor.config["name"] == "Overridden by enrich"

    def test_no_enrich_hook_is_a_no_op(self) -> None:
        """Technique: Boundary-value Analysis — default enrich=None changes nothing."""
        prop = _temp_property()
        channel = _temp_channel(properties={"temperature": prop})
        registry = _make_registry({"sensor": channel})

        with_hook = HaDiscoveryGenerator(registry=registry).generate()
        without_hook = HaDiscoveryGenerator(registry=registry, enrich=None).generate()

        assert with_hook == without_hook


class TestHasConsumerVisibleChannels:
    """has_consumer_visible_channels() — the F23 'report, don't shrug' predicate."""

    def test_true_when_a_channel_is_consumer_visible(self) -> None:
        from cosalette._schema._consumer_gen import has_consumer_visible_channels

        channel = _temp_channel(properties={})
        registry = _make_registry({"sensor": channel})

        assert has_consumer_visible_channels(registry) is True

    def test_false_for_empty_registry(self) -> None:
        from cosalette._schema._consumer_gen import has_consumer_visible_channels

        assert has_consumer_visible_channels(_make_registry()) is False

    def test_false_when_only_stream_or_all_apps_channels(self) -> None:
        """Technique: Equivalence Partitioning — ADR-054 exclusions are not visible."""
        from cosalette._schema._consumer_gen import has_consumer_visible_channels

        stream_channel = _temp_channel(archetype="stream")
        broadcast_channel = _temp_channel(scope="all_apps")
        registry = _make_registry(
            {"stream": stream_channel, "broadcast": broadcast_channel}
        )

        assert has_consumer_visible_channels(registry) is False


# ---------------------------------------------------------------------------
# Bug 1 — command archetype: send-only /state channel must not be writable
# ---------------------------------------------------------------------------

_WRITABLE_COMPONENTS = frozenset(
    {"number", "select", "switch", "text", "light", "climate", "cover"}
)


class TestBug1CommandStateChannelDirection:
    """A command entity's send-only /state channel must resolve to sensor/binary_sensor.

    The framework stamps x-cosalette-archetype: command on BOTH the /set
    (direction=receive) and the /state (direction=send) channels.  Before
    this fix, _infer_component keyed on archetype alone, so the state channel
    resolved to a writable component (number/select/switch/text) while
    _apply_topics_and_templates correctly emitted only a state_topic — creating
    an HA config that Home Assistant rejects (Bug 1).

    Test Techniques Used:
        - Specification-based Testing: HA invariant — no writable component
          without command_topic.
        - Equivalence Partitioning: receive /set vs send /state for the same
          archetype.
        - Boundary Value Analysis: consumer block on the state property, which
          triggered the original mis-classification.
    """

    def _command_entity_registry(self) -> SchemaRegistry:
        """Registry mirroring a typical bug1.yaml shape.

        Two channels, both archetype=command:
        - /set  direction=receive  (the write endpoint)
        - /state direction=send   (the read-back endpoint, carries consumer block)
        """
        set_prop = PropertySchema(
            name="position",
            json_schema={"type": "integer", "minimum": 0, "maximum": 100},
            consumer=ConsumerMetadata(display_name="Valve Position", unit="%"),
        )
        state_prop = PropertySchema(
            name="position",
            json_schema={"type": "integer"},
            consumer=ConsumerMetadata(
                display_name="Valve Position",
                unit="%",
                state_class="measurement",
            ),
        )
        set_channel = _temp_channel(
            address="myapp/valve/set",
            archetype="command",
            direction="receive",
            properties={"position": set_prop},
        )
        state_channel = _temp_channel(
            address="myapp/valve/state",
            archetype="command",
            direction="send",
            properties={"position": state_prop},
        )
        return _make_registry({"set": set_channel, "state": state_channel})

    def test_send_only_state_channel_resolves_to_sensor(self) -> None:
        """The /state channel (direction=send) must produce a sensor, not number.

        Technique: Equivalence Partitioning — send direction forces sensor path.
        """
        registry = self._command_entity_registry()

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        state_payload = next(
            p for p in payloads if "valve/state" in p.config.get("state_topic", "")
        )
        assert "/sensor/" in state_payload.topic, (
            "Send-only command channel must resolve to sensor, not a writable component"
        )

    def test_no_writable_component_without_command_topic(self) -> None:
        """Invariant: every writable HA component must have a command_topic.

        Technique: Specification-based Testing — HA discovery validity invariant.
        Drives the full registry including both /set and /state channels.
        """
        registry = self._command_entity_registry()

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        for payload in payloads:
            component = payload.topic.split("/")[1]
            if component in _WRITABLE_COMPONENTS:
                assert "command_topic" in payload.config, (
                    f"Payload {payload.topic!r} uses writable component {component!r} "
                    f"but has no command_topic — Home Assistant would reject it"
                )

    def test_state_channel_object_id_has_no_cmd_suffix(self) -> None:
        """The send-only /state entity must not carry the _cmd disambiguator.

        Technique: Boundary Value Analysis — is_command gate must exclude
        send-only channels from the _cmd suffix.
        """
        registry = self._command_entity_registry()

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        state_payload = next(
            p for p in payloads if "valve/state" in p.config.get("state_topic", "")
        )
        assert not state_payload.config["object_id"].endswith("_cmd"), (
            "State-only entity must use the bare object_id, not the _cmd variant"
        )

    def test_receive_set_channel_still_resolves_to_number(self) -> None:
        """The /set channel (direction=receive) must still produce a number entity.

        Regression guard: the fix must not break the writable /set path.

        Technique: Equivalence Partitioning — receive direction keeps writable path.
        """
        registry = self._command_entity_registry()

        payloads = HaDiscoveryGenerator(registry=registry).generate()

        set_payload = next(
            p for p in payloads if "valve/set" in p.config.get("command_topic", "")
        )
        assert "/number/" in set_payload.topic
        assert set_payload.config["object_id"].endswith("_cmd")


# ---------------------------------------------------------------------------
# Bug 2 regression: nested / array-item accessor correctness
# ---------------------------------------------------------------------------

_BUG2_SCHEMA = """
asyncapi: 3.0.0
info:
  title: bug2
  version: 0.1.0
channels:
  eventsState:
    address: bug2/events/state
    x-cosalette-app: bug2
    x-cosalette-archetype: telemetry
    messages:
      message:
        payload:
          type: object
          properties:
            events:
              type: array
              items:
                type: object
                properties:
                  title:
                    type: string
                    x-cosalette-consumer: {display_name: "Event Title"}
            meta:
              type: object
              properties:
                source:
                  type: string
                  x-cosalette-consumer: {display_name: "Source"}
operations:
  publishEvents:
    action: send
    channel:
      $ref: '#/channels/eventsState'
""".strip()

_OPENHAB_ITEM_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class TestBug2NestedAccessorFix:
    """Regression tests for Bug 2 — flattened names must not be used as
    literal accessors in HA value_template or openHAB transformationPattern.

    Test Techniques Used:
        - Specification-based Testing: accessor format contracts.
        - Round-trip Testing: Jinja2 renders a non-empty value against a
          matching payload.
        - Error Guessing: array-item (events[].title) produces no entity.
        - Boundary Value Analysis: openHAB Item ID grammar constraint.
    """

    async def _load_registry(self) -> SchemaRegistry:
        """Load the bug2 schema through the real loader pipeline."""
        source = InlineSchemaSource(_BUG2_SCHEMA)
        return await load_schema(source)

    async def test_nested_meta_source_ha_value_template(self) -> None:
        """A nested-object property (meta.source) produces a dotted
        value_template — not a literal bracket lookup of 'meta.source'.

        Technique: Specification-based Testing — accessor format contract.
        """
        registry = await self._load_registry()
        payloads = HaDiscoveryGenerator(registry=registry).generate()

        # Filter to the sensor payload (bridge entity is also emitted but irrelevant).
        sensor_payloads = [p for p in payloads if p.config.get("name") == "Source"]
        assert len(sensor_payloads) == 1, (
            "Expected exactly one 'Source' sensor payload; got: "
            + str([p.config["name"] for p in payloads])
        )
        cfg = sensor_payloads[0].config
        assert cfg["value_template"] == "{{ value_json.meta.source }}", (
            "Nested property must use dotted accessor, not literal flattened key"
        )

    async def test_nested_meta_source_openhab_jsonpath(self) -> None:
        """A nested-object property produces JSONPATH:$.meta.source —
        not JSONPATH:$['meta.source'] (the broken 0.6.2 form).

        Technique: Specification-based Testing — JSONPath accessor contract.
        """
        registry = await self._load_registry()
        output = OpenHabGenerator(registry=registry).generate_things()

        assert 'transformationPattern="JSONPATH:$.meta.source"' in output, (
            "Nested property must use dotted JSONPath, not bracket-quoted flattened key"
        )
        assert "JSONPATH:$['meta.source']" not in output

    async def test_nested_meta_source_value_template_renders_nonempty(self) -> None:
        """The generated value_template renders a non-empty string when applied
        to a matching payload via Jinja2.

        Technique: Round-trip Testing — template renders real data.
        """
        from jinja2 import Environment

        registry = await self._load_registry()
        payloads = HaDiscoveryGenerator(registry=registry).generate()

        # Filter to the Source sensor payload only.
        sensor_payloads = [p for p in payloads if p.config.get("name") == "Source"]
        assert len(sensor_payloads) == 1

        template_str = sensor_payloads[0].config["value_template"]
        # Render the full value_template string; {{ }} delimiters are parsed by Jinja2.
        rendered = (
            Environment(autoescape=True)
            .from_string(template_str)
            .render(value_json={"meta": {"source": "test-value"}})
        )
        assert rendered == "test-value", (
            f"Template {template_str!r} rendered empty or wrong: {rendered!r}"
        )

    async def test_openhab_item_id_matches_grammar(self) -> None:
        """Every generated openHAB Item ID matches ^[A-Za-z][A-Za-z0-9_]*$.

        Nested or non-identifier property names (meta.source) must be slugified
        so the generated .items file is parseable.

        Technique: Boundary Value Analysis — Item ID grammar constraint.
        """
        registry = await self._load_registry()
        items_output = OpenHabGenerator(registry=registry).generate_items()

        item_lines = [
            ln.strip()
            for ln in items_output.splitlines()
            if ln.strip() and not ln.startswith("//")
        ]
        assert item_lines, "Expected at least one Item line"
        for line in item_lines:
            # Item lines start with the type; the second token is the Item ID.
            parts = line.split()
            assert len(parts) >= 2, f"Unexpected item line format: {line!r}"
            item_id = parts[1]
            assert _OPENHAB_ITEM_ID_RE.match(item_id), (
                f"Item ID {item_id!r} does not match openHAB identifier grammar "
                f"^[A-Za-z][A-Za-z0-9_]*$ — illegal characters present"
            )

    async def test_array_item_property_produces_no_ha_discovery_payload(
        self,
    ) -> None:
        """An array-item property (events[].title) produces no HA discovery entity.

        Array-of-objects children have no single value, so emitting an entity
        for them is arbitrary and was broken in 0.6.2.

        Technique: Error Guessing — array-item guard must suppress the entity.
        """
        registry = await self._load_registry()
        payloads = HaDiscoveryGenerator(registry=registry).generate()

        prop_names_in_payloads = {p.config.get("name") for p in payloads}
        assert "Event Title" not in prop_names_in_payloads, (
            "Array-item property (events[].title) must produce no HA discovery entity"
        )

    async def test_array_item_property_produces_no_openhab_thing_or_item_line(
        self,
    ) -> None:
        """An array-item property (events[].title) produces no openHAB
        thing channel entry or item line.

        Technique: Error Guessing — array-item guard applies to both OpenHAB outputs.
        """
        registry = await self._load_registry()
        things = OpenHabGenerator(registry=registry).generate_things()
        items = OpenHabGenerator(registry=registry).generate_items()

        assert "Event Title" not in things, (
            "Array-item property must produce no .things channel entry"
        )
        assert "Event Title" not in items, (
            "Array-item property must produce no .items line"
        )

    def test_array_item_consumer_annotation_triggers_warning(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """A consumer() annotation on an array-item property emits a stderr
        warning naming the affected channel.

        Technique: Specification-based Testing — warning contract.
        """
        schema_file = tmp_path / "bug2.yaml"
        schema_file.write_text(_BUG2_SCHEMA, encoding="utf-8")

        result = runner.invoke(schema_app, ["ha-discovery", str(schema_file)])

        assert result.exit_code == EXIT_OK, f"Exit code was {result.exit_code}"
        assert "array-item" in result.stderr, (
            "Expected warning about array-item consumer annotation on stderr"
        )
        assert "eventsState" in result.stderr


# ---------------------------------------------------------------------------
# cos-j6o5 regression: nested-object write-path envelope correctness
# ---------------------------------------------------------------------------

_COS_J6O5_SCHEMA = """
asyncapi: 3.0.0
info:
  title: j6o5
  version: 0.1.0
channels:
  controlCmd:
    address: j6o5/control/set
    x-cosalette-app: j6o5
    x-cosalette-archetype: command
    messages:
      message:
        payload:
          type: object
          properties:
            meta:
              type: object
              properties:
                source:
                  type: string
                  x-cosalette-consumer: {display_name: "Source"}
operations:
  setControl:
    action: receive
    channel:
      $ref: '#/channels/controlCmd'
""".strip()


class TestCosJ6o5NestedCommandEnvelope:
    """Regression tests for cos-j6o5 — nested-object properties on
    receive/both channels must produce a nested JSON envelope, not a flat
    ``{"meta.source": ...}`` key.

    Test Techniques Used:
        - Specification-based Testing: write-path envelope shape contract.
        - Boundary Value Analysis: top-level property unchanged (byte-identical).
        - Error Guessing: flat key was the bug; nested key is the fix.
    """

    async def _load_registry(self) -> SchemaRegistry:
        source = InlineSchemaSource(_COS_J6O5_SCHEMA)
        return await load_schema(source)

    async def test_ha_nested_command_template(self) -> None:
        """A nested-object property on a receive channel produces a nested
        command_template — not a flat ``{"meta.source": ...}`` envelope.

        Technique: Specification-based Testing — write-path envelope contract.
        """
        registry = await self._load_registry()
        payloads = HaDiscoveryGenerator(registry=registry).generate()

        source_payloads = [p for p in payloads if p.config.get("name") == "Source"]
        assert len(source_payloads) == 1, (
            "Expected exactly one 'Source' payload; got: "
            + str([p.config.get("name") for p in payloads])
        )
        cfg = source_payloads[0].config
        assert "command_topic" in cfg, "receive channel must emit command_topic"
        expected = '{"meta": {"source": {{ value | tojson }}}}'
        assert cfg["command_template"] == expected, (
            "Nested property must produce a nested envelope, not flat 'meta.source'"
        )

    def test_ha_top_level_command_template_unchanged(self) -> None:
        """A top-level property still yields the flat ``{\"<name>\": ...}``
        envelope — byte-identical to the pre-fix behaviour.

        Technique: Boundary Value Analysis — single-segment path.
        """
        prop = PropertySchema(
            name="brightness",
            json_schema={"type": "integer"},
            consumer=ConsumerMetadata(display_name="Brightness"),
            path=("brightness",),
        )
        channel = _temp_channel(
            address="myapp/desk/set",
            archetype="command",
            direction="receive",
            properties={"brightness": prop},
        )
        registry = _make_registry({"desk": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config

        assert cfg["command_template"] == '{"brightness": {{ value }}}', (
            "Top-level property must still produce flat envelope"
        )

    def test_openhab_nested_format_before_publish(self) -> None:
        """A nested-object property on a receive channel produces a nested
        ``formatBeforePublish`` value — not ``{\\\"meta.source\\\":...}``.

        Technique: Specification-based Testing — write-path envelope contract.
        """
        prop = PropertySchema(
            name="meta.source",
            json_schema={"type": "string"},
            consumer=ConsumerMetadata(display_name="Source"),
            path=("meta", "source"),
        )
        result = _openhab_format_before_publish(prop)
        assert result == '{\\"meta\\":{\\"source\\":\\"%s\\"}}'

    def test_openhab_top_level_format_before_publish_unchanged(self) -> None:
        """A top-level property still yields ``{\\\"<name>\\\":...}`` —
        byte-identical to the pre-fix behaviour.

        Technique: Boundary Value Analysis — single-segment path.
        """
        prop = PropertySchema(
            name="brightness",
            json_schema={"type": "integer"},
            consumer=ConsumerMetadata(display_name="Brightness"),
            path=("brightness",),
        )
        result = _openhab_format_before_publish(prop)
        assert result == '{\\"brightness\\":%s}'

    async def test_openhab_nested_format_before_publish_in_things_output(
        self,
    ) -> None:
        """End-to-end: a nested receive property emits the nested envelope in
        the generated ``.things`` output.

        Technique: Round-trip Testing — full generator pipeline.
        """
        registry = await self._load_registry()
        things = OpenHabGenerator(registry=registry).generate_things()

        assert 'formatBeforePublish="{\\"meta\\":{\\"source\\":\\"%s\\"}}"' in things, (
            "Expected nested formatBeforePublish in .things output"
        )

    def test_nested_numeric_envelope_both_generators(self) -> None:
        """A nested *integer* property nests the numeric (unquoted) placeholder
        for both generators — the type branch is applied inside the envelope.

        Technique: Boundary Value Analysis — numeric branch x nested path.
        """
        prop = PropertySchema(
            name="meta.level",
            json_schema={"type": "integer"},
            consumer=ConsumerMetadata(display_name="Level"),
            path=("meta", "level"),
        )
        channel = _temp_channel(
            address="myapp/panel/set",
            archetype="command",
            direction="receive",
            properties={"meta.level": prop},
        )
        registry = _make_registry({"panel": channel})

        cfg = HaDiscoveryGenerator(registry=registry).generate()[0].config
        assert cfg["command_template"] == '{"meta": {"level": {{ value }}}}'

        assert _openhab_format_before_publish(prop) == '{\\"meta\\":{\\"level\\":%s}}'
