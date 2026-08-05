"""Unit tests for the public ``cosalette.schema.consumer`` producer surface.

Test Techniques Used:
    - Specification-based Testing: consumer() wraps metadata under the
      x-cosalette-consumer key; empty call yields an empty block.
    - Round-trip Testing: producer output attached via Field(json_schema_extra=)
      survives TypeAdapter(model).json_schema() and is parsed back by the
      ConsumerMetadata reader (producer ↔ reader parity).
    - Drift Guard: ConsumerMeta keys must equal ConsumerMetadata dataclass fields.
    - Equivalence Partitioning: percent() icon supplied vs. omitted partitions.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated

import pydantic
import pytest

from cosalette._schema import ConsumerMetadata
from cosalette._schema._loader_helpers import _build_consumer_metadata
from cosalette.schema import (
    X_COSALETTE_CONSUMER,
    ConsumerMeta,
    consumer,
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
        assert set(schema_mod.__all__) == {
            "consumer",
            "ConsumerMeta",
            "X_COSALETTE_CONSUMER",
            "temperature",
            "percent",
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
