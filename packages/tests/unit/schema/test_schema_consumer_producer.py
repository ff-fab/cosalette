"""Unit tests for the public ``cosalette.schema`` producer surface.

Test Techniques Used:
    - Specification-based Testing: consumer()/ha_discovery()/openhab() wrap
      metadata under their respective x-cosalette-* keys; empty calls yield
      empty blocks.
    - Round-trip Testing: producer output attached via Field(json_schema_extra=)
      survives TypeAdapter(model).json_schema() and is parsed back by the
      matching reader (producer ↔ reader parity).
    - Drift Guard: each *Meta TypedDict's keys must equal its reader
      dataclass's fields.
    - Equivalence Partitioning: percent() icon supplied vs. omitted partitions;
      merge() with distinct vs. colliding extension keys.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated

import pydantic
import pytest

from cosalette._schema import (
    ConsumerMetadata,
    HaDiscoveryOverrides,
    HaEntitySpec,
    OpenHabOverrides,
)
from cosalette._schema._loader_helpers import (
    _build_consumer_metadata,
    _build_ha_entity_specs,
    _build_property_schema,
)
from cosalette.schema import (
    X_COSALETTE_CONSUMER,
    X_COSALETTE_HA_DISCOVERY,
    X_COSALETTE_OPENHAB,
    ConsumerMeta,
    HaDiscoveryMeta,
    HaEntityMeta,
    OpenHabMeta,
    consumer,
    ha_discovery,
    ha_entities,
    ha_entity,
    merge,
    openhab,
    percent,
    temperature,
)

pytestmark = pytest.mark.unit


class TestConsumerProducer:
    """consumer() builds x-cosalette-consumer json_schema_extra blocks."""

    def test_wraps_metadata_under_extension_key(self) -> None:
        # Arrange / Act
        result = consumer(display_name="X", unit="%", state_class="measurement")

        # Assert
        assert result == {
            "x-cosalette-consumer": {
                "display_name": "X",
                "unit": "%",
                "state_class": "measurement",
            }
        }

    def test_empty_call_yields_empty_block(self) -> None:
        # Arrange / Act / Assert
        assert consumer() == {"x-cosalette-consumer": {}}

    def test_constant_matches_extension_key(self) -> None:
        # Arrange / Act / Assert
        assert X_COSALETTE_CONSUMER == "x-cosalette-consumer"

    def test_public_import_surface(self) -> None:
        # Arrange — the public shim module.
        import cosalette.schema as schema_mod  # noqa: PLC0415

        # Act / Assert — the shim re-exports the producer surface.
        assert schema_mod.consumer is consumer
        assert schema_mod.ConsumerMeta is ConsumerMeta
        assert schema_mod.X_COSALETTE_CONSUMER == X_COSALETTE_CONSUMER
        assert schema_mod.temperature is temperature
        assert schema_mod.percent is percent
        assert schema_mod.ha_discovery is ha_discovery
        assert schema_mod.HaDiscoveryMeta is HaDiscoveryMeta
        assert schema_mod.X_COSALETTE_HA_DISCOVERY == X_COSALETTE_HA_DISCOVERY
        assert schema_mod.openhab is openhab
        assert schema_mod.OpenHabMeta is OpenHabMeta
        assert schema_mod.X_COSALETTE_OPENHAB == X_COSALETTE_OPENHAB
        assert schema_mod.merge is merge
        assert schema_mod.ha_entity is ha_entity
        assert schema_mod.ha_entities is ha_entities
        assert schema_mod.HaEntityMeta is HaEntityMeta
        assert set(schema_mod.__all__) == {
            "consumer",
            "ConsumerMeta",
            "X_COSALETTE_CONSUMER",
            "temperature",
            "percent",
            "ha_discovery",
            "HaDiscoveryMeta",
            "X_COSALETTE_HA_DISCOVERY",
            "openhab",
            "OpenHabMeta",
            "X_COSALETTE_OPENHAB",
            "merge",
            "ha_entity",
            "ha_entities",
            "HaEntityMeta",
        }


class TestTemperaturePreset:
    """temperature() collapses the device_class/unit/state_class triple."""

    def test_basic_call_wraps_standard_celsius_metadata(self) -> None:
        # Arrange / Act
        result = temperature("Room Temperature")

        # Assert
        assert result == {
            "x-cosalette-consumer": {
                "display_name": "Room Temperature",
                "device_class": "temperature",
                "unit": "°C",
                "state_class": "measurement",
            }
        }


class TestPercentPreset:
    """percent() collapses the unit=%/state_class=measurement pair."""

    def test_with_icon_includes_icon_key(self) -> None:
        # Arrange / Act
        result = percent("Pump Speed", icon="mdi:pump")

        # Assert
        assert result == {
            "x-cosalette-consumer": {
                "display_name": "Pump Speed",
                "unit": "%",
                "state_class": "measurement",
                "icon": "mdi:pump",
            }
        }

    def test_without_icon_omits_icon_key(self) -> None:
        """Technique: Equivalence Partitioning — omitted-icon partition.

        Asserts ``icon`` is absent from the dict entirely (not present as
        ``None``), matching a hand-written block exactly.
        """
        # Arrange / Act
        result = percent("Modulation")

        # Assert
        assert result == {
            "x-cosalette-consumer": {
                "display_name": "Modulation",
                "unit": "%",
                "state_class": "measurement",
            }
        }
        assert "icon" not in result[X_COSALETTE_CONSUMER]


class TestProducerReaderParity:
    """Producer output round-trips through pydantic and the reader."""

    def test_round_trip_through_pydantic_and_reader(self) -> None:
        # Arrange — include device_class and read_only=True so the round-trip
        # locks in bool survival end-to-end (read_only is the only non-str value).
        block = consumer(
            display_name="Cover Position",
            device_class="temperature",
            unit="%",
            state_class="measurement",
            icon="mdi:window-shutter",
            read_only=True,
        )

        class Model(pydantic.BaseModel):
            position: Annotated[int, pydantic.Field(json_schema_extra=block)]

        # Act — regenerate the schema the way cosalette does.
        schema = pydantic.TypeAdapter(Model).json_schema()
        prop_schema = schema["properties"]["position"]

        # Assert — the block survives regeneration unchanged.
        assert prop_schema[X_COSALETTE_CONSUMER] == block[X_COSALETTE_CONSUMER]

        # Act — feed the surviving block through the reader.
        metadata = _build_consumer_metadata(prop_schema[X_COSALETTE_CONSUMER])

        # Assert — reader reconstructs the producer's values, including the
        # boolean read_only, which must survive as True (not stringified).
        assert metadata == ConsumerMetadata(
            display_name="Cover Position",
            device_class="temperature",
            unit="%",
            state_class="measurement",
            icon="mdi:window-shutter",
            read_only=True,
        )
        assert metadata.read_only is True

    def test_temperature_preset_round_trips_through_pydantic_and_reader(self) -> None:
        # Arrange — presets must round-trip identically to hand-built consumer().
        block = temperature("Room Temperature")

        class Model(pydantic.BaseModel):
            temp: Annotated[float, pydantic.Field(json_schema_extra=block)]

        # Act
        schema = pydantic.TypeAdapter(Model).json_schema()
        prop_schema = schema["properties"]["temp"]
        metadata = _build_consumer_metadata(prop_schema[X_COSALETTE_CONSUMER])

        # Assert
        assert prop_schema[X_COSALETTE_CONSUMER] == block[X_COSALETTE_CONSUMER]
        assert metadata == ConsumerMetadata(
            display_name="Room Temperature",
            device_class="temperature",
            unit="\u00b0C",
            state_class="measurement",
        )

    def test_percent_preset_round_trips_through_pydantic_and_reader(self) -> None:
        # Arrange — percent() must round-trip without device_class.
        block = percent("Modulation")

        class Model(pydantic.BaseModel):
            pct: Annotated[int, pydantic.Field(json_schema_extra=block)]

        # Act
        schema = pydantic.TypeAdapter(Model).json_schema()
        prop_schema = schema["properties"]["pct"]
        metadata = _build_consumer_metadata(prop_schema[X_COSALETTE_CONSUMER])

        # Assert — no device_class in percent() output.
        assert prop_schema[X_COSALETTE_CONSUMER] == block[X_COSALETTE_CONSUMER]
        assert metadata == ConsumerMetadata(
            display_name="Modulation",
            unit="%",
            state_class="measurement",
        )


class TestDriftGuard:
    """ConsumerMeta keys are the single source of truth with the reader."""

    def test_consumer_meta_keys_match_dataclass_fields(self) -> None:
        # Arrange / Act
        typed_dict_keys = set(ConsumerMeta.__annotations__)
        dataclass_fields = {f.name for f in dataclasses.fields(ConsumerMetadata)}

        # Assert
        assert typed_dict_keys == dataclass_fields

    def test_ha_discovery_meta_keys_match_dataclass_fields(self) -> None:
        # Arrange / Act
        typed_dict_keys = set(HaDiscoveryMeta.__annotations__)
        dataclass_fields = {f.name for f in dataclasses.fields(HaDiscoveryOverrides)}

        # Assert
        assert typed_dict_keys == dataclass_fields

    def test_openhab_meta_keys_match_dataclass_fields(self) -> None:
        # Arrange / Act
        typed_dict_keys = set(OpenHabMeta.__annotations__)
        dataclass_fields = {f.name for f in dataclasses.fields(OpenHabOverrides)}

        # Assert
        assert typed_dict_keys == dataclass_fields

    def test_ha_entity_meta_keys_match_dataclass_fields(self) -> None:
        # Arrange / Act
        typed_dict_keys = set(HaEntityMeta.__annotations__)
        dataclass_fields = {f.name for f in dataclasses.fields(HaEntitySpec)}

        # Assert
        assert typed_dict_keys == dataclass_fields


class TestHaDiscoveryProducer:
    """ha_discovery() builds x-cosalette-ha-discovery json_schema_extra blocks."""

    def test_wraps_metadata_under_extension_key(self) -> None:
        # Arrange / Act
        result = ha_discovery(component="climate", expire_after=300)

        # Assert
        assert result == {
            "x-cosalette-ha-discovery": {"component": "climate", "expire_after": 300}
        }

    def test_empty_call_yields_empty_block(self) -> None:
        # Arrange / Act / Assert
        assert ha_discovery() == {"x-cosalette-ha-discovery": {}}

    def test_extra_carries_arbitrary_passthrough_keys(self) -> None:
        """extra is untyped — any key/value pair passes through unchanged.

        Technique: Boundary Value Analysis — open passthrough field.
        """
        # Arrange / Act
        result = ha_discovery(extra={"schema": "json", "optimistic": False})

        # Assert
        assert result["x-cosalette-ha-discovery"]["extra"] == {
            "schema": "json",
            "optimistic": False,
        }

    def test_constant_matches_extension_key(self) -> None:
        # Arrange / Act / Assert
        assert X_COSALETTE_HA_DISCOVERY == "x-cosalette-ha-discovery"

    def test_round_trip_through_pydantic_and_reader(self) -> None:
        """Technique: Round-trip Testing — producer output survives pydantic
        regeneration and reader reconstruction.
        """
        # Arrange
        block = ha_discovery(
            component="light",
            value_template="{{ value_json.state }}",
            command_template='{"state": {{ value }}}',
            expire_after=300,
            extra={"schema": "json", "optimistic": False},
        )

        class Model(pydantic.BaseModel):
            state: Annotated[bool, pydantic.Field(json_schema_extra=block)]

        # Act — regenerate the schema the way cosalette does.
        schema = pydantic.TypeAdapter(Model).json_schema()
        prop_schema = schema["properties"]["state"]

        # Assert — the block survives regeneration unchanged.
        assert prop_schema[X_COSALETTE_HA_DISCOVERY] == block[X_COSALETTE_HA_DISCOVERY]

        # Act — feed the surviving schema through the full property reader.
        built = _build_property_schema("state", prop_schema)

        # Assert
        assert built.ha_discovery == HaDiscoveryOverrides(
            component="light",
            value_template="{{ value_json.state }}",
            command_template='{"state": {{ value }}}',
            expire_after=300,
            extra={"schema": "json", "optimistic": False},
        )


class TestOpenHabProducer:
    """openhab() builds x-cosalette-openhab json_schema_extra blocks."""

    def test_wraps_metadata_under_extension_key(self) -> None:
        # Arrange / Act
        result = openhab(item_type="Color", channel_type="color")

        # Assert
        assert result == {
            "x-cosalette-openhab": {"item_type": "Color", "channel_type": "color"}
        }

    def test_empty_call_yields_empty_block(self) -> None:
        # Arrange / Act / Assert
        assert openhab() == {"x-cosalette-openhab": {}}

    def test_channel_params_carries_arbitrary_passthrough_keys(self) -> None:
        """channel_params is untyped — any key/value pair passes through.

        Technique: Boundary Value Analysis — open passthrough field.
        """
        # Arrange / Act
        result = openhab(channel_params={"colorMode": "HSB", "min": 0})

        # Assert
        assert result["x-cosalette-openhab"]["channel_params"] == {
            "colorMode": "HSB",
            "min": 0,
        }

    def test_constant_matches_extension_key(self) -> None:
        # Arrange / Act / Assert
        assert X_COSALETTE_OPENHAB == "x-cosalette-openhab"

    def test_round_trip_through_pydantic_and_reader(self) -> None:
        """Technique: Round-trip Testing — producer output survives pydantic
        regeneration and reader reconstruction.
        """
        # Arrange
        block = openhab(
            item_type="Color",
            label="HSB",
            groups=["gLights"],
            tags=["Lighting"],
            channel_type="color",
            channel_params={"colorMode": "HSB"},
        )

        class Model(pydantic.BaseModel):
            hsb: Annotated[list[int], pydantic.Field(json_schema_extra=block)]

        # Act — regenerate the schema the way cosalette does.
        schema = pydantic.TypeAdapter(Model).json_schema()
        prop_schema = schema["properties"]["hsb"]

        # Assert — the block survives regeneration; pydantic's schema
        # generator renders tuples as JSON arrays (lists), so groups/tags
        # come back as lists rather than tuples — the reader converts them
        # back to tuples below.
        assert prop_schema[X_COSALETTE_OPENHAB] == {
            "item_type": "Color",
            "label": "HSB",
            "groups": ["gLights"],
            "tags": ["Lighting"],
            "channel_type": "color",
            "channel_params": {"colorMode": "HSB"},
        }

        # Act — feed the surviving schema through the full property reader.
        built = _build_property_schema("hsb", prop_schema)

        # Assert
        assert built.openhab == OpenHabOverrides(
            item_type="Color",
            label="HSB",
            groups=("gLights",),
            tags=("Lighting",),
            channel_type="color",
            channel_params={"colorMode": "HSB"},
        )


class TestHaEntityProducer:
    """ha_entity()/ha_entities() build composite entity specs (ADR-057)."""

    def test_ha_entity_returns_bare_dict(self) -> None:
        # Arrange / Act
        result = ha_entity(
            component="light", name="Desk Lamp", extra={"schema": "json"}
        )

        # Assert — not wrapped under the extension key; ha_entities() does that.
        assert result == {
            "component": "light",
            "name": "Desk Lamp",
            "extra": {"schema": "json"},
        }

    def test_ha_entities_wraps_under_extension_key(self) -> None:
        # Arrange / Act
        result = ha_entities(
            ha_entity(component="light", name="Desk Lamp"),
            ha_entity(component="sensor", name="Signal"),
        )

        # Assert
        assert result == {
            "x-cosalette-ha-discovery": {
                "entities": [
                    {"component": "light", "name": "Desk Lamp"},
                    {"component": "sensor", "name": "Signal"},
                ]
            }
        }

    def test_empty_call_yields_empty_entities_list(self) -> None:
        # Arrange / Act / Assert
        assert ha_entities() == {"x-cosalette-ha-discovery": {"entities": []}}

    def test_round_trip_through_pydantic_model_config_and_reader(self) -> None:
        """Technique: Round-trip Testing — model-level json_schema_extra survives
        pydantic regeneration and is read back into HaEntitySpec via the
        payload-schema-level loader helper (not the per-property reader).
        """
        # Arrange
        block = ha_entities(
            ha_entity(
                component="light",
                name="Desk Lamp",
                extra={"schema": "json", "brightness": True},
            )
        )

        class BulbState(pydantic.BaseModel):
            model_config = pydantic.ConfigDict(json_schema_extra=block)

            state: Annotated[bool, pydantic.Field(json_schema_extra=consumer())]

        # Act — regenerate the schema the way cosalette does.
        schema = pydantic.TypeAdapter(BulbState).json_schema()

        # Assert — the block survives regeneration as a sibling of `properties`.
        assert schema[X_COSALETTE_HA_DISCOVERY] == block[X_COSALETTE_HA_DISCOVERY]

        # Act — feed the surviving schema through the channel-level reader.
        specs = _build_ha_entity_specs(schema)

        # Assert
        assert specs == (
            HaEntitySpec(
                component="light",
                name="Desk Lamp",
                extra={"schema": "json", "brightness": True},
            ),
        )


class TestNullCollectionFields:
    """Explicit null values in extension fields must not crash the loader.

    Technique: Boundary Value Analysis — key present with null value is distinct
    from key absent; .get(key, default) returns None (not the default) when the
    key exists with a null value.
    """

    @pytest.mark.parametrize(
        "schema",
        [
            {"type": "number", "x-cosalette-ha-discovery": {"extra": None}},
            {"type": "number", "x-cosalette-openhab": {"groups": None}},
            {"type": "number", "x-cosalette-openhab": {"tags": None}},
            {"type": "number", "x-cosalette-openhab": {"channel_params": None}},
        ],
        ids=[
            "ha_extra_null",
            "openhab_groups_null",
            "openhab_tags_null",
            "openhab_channel_params_null",
        ],
    )
    def test_null_collection_field_does_not_crash(
        self, schema: dict[str, object]
    ) -> None:
        """A null-valued collection field is treated the same as an absent key."""
        prop = _build_property_schema("x", schema)
        assert prop is not None


class TestMergeHelper:
    """merge() folds multiple producer outputs into one json_schema_extra dict."""

    def test_merges_distinct_extension_keys(self) -> None:
        # Arrange / Act
        result = merge(
            consumer(display_name="HSB"),
            openhab(item_type="Color", channel_type="color"),
        )

        # Assert
        assert result == {
            "x-cosalette-consumer": {"display_name": "HSB"},
            "x-cosalette-openhab": {"item_type": "Color", "channel_type": "color"},
        }

    def test_merges_all_three_producers(self) -> None:
        # Arrange / Act
        result = merge(
            consumer(display_name="Desk Lamp"),
            ha_discovery(expire_after=300),
            openhab(item_type="Switch"),
        )

        # Assert
        assert result == {
            X_COSALETTE_CONSUMER: {"display_name": "Desk Lamp"},
            X_COSALETTE_HA_DISCOVERY: {"expire_after": 300},
            X_COSALETTE_OPENHAB: {"item_type": "Switch"},
        }

    def test_no_blocks_yields_empty_dict(self) -> None:
        # Arrange / Act / Assert
        assert merge() == {}

    @pytest.mark.parametrize(
        "block_a, block_b",
        [
            (consumer(display_name="A"), consumer(display_name="B")),
            (ha_discovery(expire_after=10), ha_discovery(expire_after=20)),
            (openhab(item_type="Switch"), openhab(item_type="Dimmer")),
        ],
    )
    def test_raises_on_duplicate_extension_key(
        self, block_a: dict[str, object], block_b: dict[str, object]
    ) -> None:
        """Two calls to the same producer cannot be silently merged.

        Technique: Error Guessing — precedence between colliding keys is
        ambiguous, so merge() refuses rather than picking a winner silently.
        """
        # Arrange / Act / Assert
        with pytest.raises(ValueError, match="duplicate extension key"):
            merge(block_a, block_b)

    def test_merged_block_survives_pydantic_round_trip(self) -> None:
        # Arrange
        block = merge(
            consumer(display_name="HSB"),
            openhab(
                item_type="Color",
                channel_type="color",
                channel_params={"colorMode": "HSB"},
            ),
        )

        class Model(pydantic.BaseModel):
            hsb: Annotated[list[int], pydantic.Field(json_schema_extra=block)]

        # Act
        schema = pydantic.TypeAdapter(Model).json_schema()
        prop_schema = schema["properties"]["hsb"]
        built = _build_property_schema("hsb", prop_schema)

        # Assert
        assert built.consumer == ConsumerMetadata(display_name="HSB")
        assert built.openhab == OpenHabOverrides(
            item_type="Color",
            channel_type="color",
            channel_params={"colorMode": "HSB"},
        )
