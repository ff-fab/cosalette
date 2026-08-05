"""Public producer surface for the ``x-cosalette-consumer`` schema extension.

Apps attach Home Assistant / OpenHAB discovery metadata to model fields with
:func:`consumer`, passing the result to pydantic ``Field(json_schema_extra=...)``.
The metadata rides on the field and survives schema regeneration via
``TypeAdapter(model).json_schema()``; the HA and OpenHAB generators then consume
it. The :class:`ConsumerMeta` key set is the single source of truth shared with
the framework's ``ConsumerMetadata`` reader.

:func:`temperature` and :func:`percent` are semantic presets over
:func:`consumer` for the two most common field shapes.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

from cosalette._schema import (
    X_COSALETTE_CONSUMER,
    ConsumerMeta,
    consumer,
    percent,
    temperature,
)

__all__ = [
    "ConsumerMeta",
    "X_COSALETTE_CONSUMER",
    "consumer",
    "percent",
    "temperature",
]
