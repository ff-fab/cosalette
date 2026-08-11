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

:func:`ha_entities` / :func:`ha_entity` declare a composite Home Assistant
entity spanning a channel's whole payload model (e.g. a JSON-schema ``light``
built from ``state`` + ``brightness`` + ``color_temp``) — pass the result to
pydantic ``ConfigDict(json_schema_extra=...)`` on the model itself rather than
a field.

``ChannelSchema`` / ``PropertySchema`` / ``HaEnrichHook`` are the reader-side
types passed to an ``App.discovery(enrich=...)`` callback (F23) — re-exported
here so an app can type-annotate its enrichment hook without reaching into
the private ``cosalette._schema`` package.

See Also:
    ADR-033 — MQTT schema enforcement.
    ADR-050 — Typed consumer() producer.
    ADR-056 — Typed ha_discovery()/openhab() producers and open passthrough.
    ADR-057 — Component-aware HA payload builders via composite entities.
"""

from __future__ import annotations

from cosalette._schema import (
    X_COSALETTE_CONSUMER,
    X_COSALETTE_HA_DISCOVERY,
    X_COSALETTE_OPENHAB,
    ChannelSchema,
    ConsumerMeta,
    HaDiscoveryMeta,
    HaEntityMeta,
    OpenHabMeta,
    PropertySchema,
    consumer,
    ha_discovery,
    ha_entities,
    ha_entity,
    merge,
    openhab,
    percent,
    temperature,
)
from cosalette._schema._consumer_gen import HaEnrichHook

__all__ = [
    "ChannelSchema",
    "ConsumerMeta",
    "HaDiscoveryMeta",
    "HaEnrichHook",
    "HaEntityMeta",
    "OpenHabMeta",
    "PropertySchema",
    "X_COSALETTE_CONSUMER",
    "X_COSALETTE_HA_DISCOVERY",
    "X_COSALETTE_OPENHAB",
    "consumer",
    "ha_discovery",
    "ha_entities",
    "ha_entity",
    "merge",
    "openhab",
    "percent",
    "temperature",
]
