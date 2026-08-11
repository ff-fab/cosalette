"""Public producer surface for the ``x-cosalette-*`` consumer schema extensions.

Apps attach Home Assistant / OpenHAB discovery metadata to model fields with
:func:`consumer`, and platform-specific overrides with :func:`ha_discovery` /
:func:`openhab`, passing the result to pydantic ``Field(json_schema_extra=...)``.
Use :func:`merge` to combine more than one of these on a single field. The
metadata rides on the field and survives schema regeneration via
``TypeAdapter(model).json_schema()``; the HA and OpenHAB generators then consume
it. Each producer's key set is the single source of truth shared with its
reader-side dataclass (``ConsumerMetadata``, ``HaDiscoveryOverrides``,
``OpenHabOverrides``).

:func:`temperature` and :func:`percent` are semantic presets over
:func:`consumer` for the two most common field shapes.

See Also:
    ADR-033 — MQTT schema enforcement.
    ADR-050 — Typed consumer() producer.
    ADR-056 — Typed ha_discovery()/openhab() producers and open passthrough.
"""

from __future__ import annotations

from cosalette._schema import (
    X_COSALETTE_CONSUMER,
    X_COSALETTE_HA_DISCOVERY,
    X_COSALETTE_OPENHAB,
    ConsumerMeta,
    HaDiscoveryMeta,
    OpenHabMeta,
    consumer,
    ha_discovery,
    merge,
    openhab,
    percent,
    temperature,
)

__all__ = [
    "ConsumerMeta",
    "HaDiscoveryMeta",
    "OpenHabMeta",
    "X_COSALETTE_CONSUMER",
    "X_COSALETTE_HA_DISCOVERY",
    "X_COSALETTE_OPENHAB",
    "consumer",
    "ha_discovery",
    "merge",
    "openhab",
    "percent",
    "temperature",
]
