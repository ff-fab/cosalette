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

    def test_root_device_gets_single_availability_topic(self) -> None:
        """Boundary Value Analysis — no resolved device name (root/unnamed)."""
        config = self._root_payload().config

        assert config["availability_topic"] == "myapp/availability"
        assert config["payload_available"] == "online"
        assert config["payload_not_available"] == "offline"
        assert "availability" not in config
        assert "availability_mode" not in config

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
