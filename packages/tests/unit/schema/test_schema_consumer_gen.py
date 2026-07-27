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
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cosalette._schema import (
    ChannelSchema,
    ConsumerMetadata,
    EnforcementConfig,
    HaDiscoveryOverrides,
    OpenHabOverrides,
    PropertySchema,
    SchemaRegistry,
)
from cosalette._schema._cli import schema_app
from cosalette._schema._consumer_gen import (
    HaDiscoveryGenerator,
    HaDiscoveryPayload,
    OpenHabGenerator,
    ha_discovery_to_json,
)
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
) -> SchemaRegistry:
    """Build a minimal SchemaRegistry for testing."""
    return SchemaRegistry(
        app_name=None,
        app_version="1.0.0",
        asyncapi_version="3.0.0",
        enforcement=EnforcementConfig(mode="strict", network_level=True),
        channels=channels or {},
        operations={},
        component_schemas={},
        device_names=frozenset(),
    )


def _temp_channel(
    *,
    address: str = "myapp/sensor/state",
    app_name: str = "myapp",
    archetype: str = "telemetry",
    direction: str = "send",
    properties: dict[str, PropertySchema] | None = None,
) -> ChannelSchema:
    """Build a minimal ChannelSchema for testing."""
    return ChannelSchema(
        address=address,
        address_template=address,
        direction=direction,  # ty: ignore[invalid-argument-type]
        app_name=app_name,
        archetype=archetype,  # ty: ignore[invalid-argument-type]
        properties=properties or {},
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
        """ha_discovery.component overrides inferred component.

        Technique: Boundary Value Analysis — explicit override takes precedence.
        """
        ha = HaDiscoveryOverrides(component="climate")
        prop = _temp_property(ha=ha)
        channel = _temp_channel(properties={"temperature": prop})
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
        """Non-identifier property names use bracket-notation JSONPATH (cos-ym6.2).

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

        Regression: build_app_asyncapi now routes the real app name into every
        channel (previously the .things label always fell back to "unknown"),
        so an app name containing a double quote must stay inside the quoted
        label instead of breaking out of it. Mirrors the property-label escaping
        (cos-ym6.2).

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
