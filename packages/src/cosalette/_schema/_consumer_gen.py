"""Consumer code generation from AsyncAPI schemas.

Generates Home Assistant MQTT discovery payloads and OpenHAB
``.things``/``.items`` configuration from :class:`SchemaRegistry`.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cosalette._schema import (
    ChannelSchema,
    ConsumerMetadata,
    HaDiscoveryOverrides,
    HaEntitySpec,
    PropertySchema,
    SchemaRegistry,
    _device_name_from_archetype,
    _device_name_from_template,
)

# ---------------------------------------------------------------------------
# HA Discovery
# ---------------------------------------------------------------------------

# Component inference: (archetype, json_type) → HA component.
_HA_COMPONENT_MAP: dict[tuple[str | None, str], str] = {
    ("telemetry", "number"): "sensor",
    ("telemetry", "integer"): "sensor",
    ("telemetry", "string"): "sensor",
    ("telemetry", "boolean"): "binary_sensor",
    ("command", "boolean"): "switch",
    ("command", "integer"): "number",
    ("command", "number"): "number",
    ("command", "string"): "text",
    ("device", "number"): "sensor",
    ("device", "integer"): "sensor",
    ("device", "string"): "sensor",
    ("device", "boolean"): "binary_sensor",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_MANUFACTURER = "cosalette"


def _slugify(value: str) -> str:
    """Convert a string to a slug suitable for HA object IDs."""
    return _SLUG_RE.sub("_", value.lower()).strip("_")


def _path_segments_to_accessor(prefix: str, path: tuple[str, ...]) -> str:
    """Build a dotted/bracketed accessor string from structural *path* segments.

    *prefix* is placed first (``value_json`` for Jinja, ``$`` for JSONPath).
    Simple identifier segments use dot notation; others use single-quoted
    bracket notation with backslash, quote, and newline escaping.
    """
    parts = [prefix]
    for seg in path:
        if _IDENTIFIER_RE.match(seg):
            parts.append(f".{seg}")
        else:
            escaped = (
                seg.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace('"', '\\"')
                .replace("\n", "")
            )
            parts.append(f"['{escaped}']")
    return "".join(parts)


def _effective_schema(json_schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve the effective sub-schema for type inference (F6, F7).

    Pydantic emits optionals as ``anyOf: [T, {type: null}]`` with no top-level
    ``type``.  Unwrap such a union to its single non-null variant so optional
    fields infer their real type instead of degrading to ``string``.  Also
    handles ``allOf`` — emitted by Pydantic v2 for constrained annotated types
    such as ``Annotated[int, Field(ge=0)]``.  Falls back to *json_schema*
    unchanged when no clean single-variant unwrap is possible.
    """
    if "type" in json_schema:
        return json_schema
    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = json_schema.get(keyword)
        if not isinstance(variants, list):
            continue
        non_null = [
            v for v in variants if isinstance(v, dict) and v.get("type") != "null"
        ]
        if len(non_null) == 1:
            return non_null[0]
    return json_schema


def _effective_type(prop: PropertySchema) -> str:
    """Return the effective JSON schema type of *prop* (through optionals)."""
    return _effective_schema(prop.json_schema).get("type", "string")


def _resolve_device(channel: ChannelSchema) -> str:
    """Resolve the device segment for *channel* (mirrors ``_extract_device_names``).

    Prefers a ``{deviceName}`` template parameter, else the archetype-based
    structural extractor, which handles nested ``app/room/device/suffix``
    addresses (F5).  Falls back to the second address segment only for
    malformed short addresses.
    """
    name: str | None = None
    if "{deviceName}" in channel.address_template:
        name = _device_name_from_template(channel)
    elif channel.archetype and "{" not in channel.address_template:
        name = _device_name_from_archetype(channel)
    if name:
        return name
    parts = channel.address.split("/")
    return parts[1] if len(parts) >= 2 else parts[0]


def _is_root_device(registry: SchemaRegistry, device_name: str) -> bool:
    """True if *device_name* is not a real, resolved device (ADR-058).

    ``registry.device_names`` is populated by ``_extract_device_names``,
    which runs the identical archetype/template resolution ``_resolve_device``
    uses and only fails to produce a name for short, root-shaped addresses —
    the same addresses ``_resolve_device``'s own fallback guesses at. Absence
    from that set is therefore a free, exact-enough root signal without
    threading a new ``is_root`` extension through the AsyncAPI pipeline.
    """
    return device_name not in registry.device_names


# HA's Jinja value_template globals: ``value_json`` is the parsed JSON payload
# (undefined for non-JSON payloads such as the bare LWT string), ``value`` is
# the raw payload string. This normalises ADR-012's two payload shapes on
# ``{app}/status`` — the JSON heartbeat and the plain LWT "offline" — to a
# single "online"/"offline" value (F18).
_STATUS_VALUE_EXPR = "value_json.status if value_json is mapping else value"


def _availability_block(app: str, device_name: str, *, is_root: bool) -> dict[str, Any]:
    """Build the HA availability config for *device_name* (F18, ADR-058).

    All devices — root and named — use the dual-topic ``availability`` list
    with ``availability_mode: "all"``.

    Root devices combine the clean-shutdown topic ``{app}/availability`` with
    the app-level ``{app}/status`` heartbeat/LWT topic. Named devices do the
    same but use their own ``{app}/{device}/availability`` topic instead of
    the app-level one. ``availability_mode: "all"`` ensures that an unclean
    crash — which fires the LWT on ``{app}/status`` but leaves the retained
    ``{app}/availability`` payload stale at "online" — still marks the entity
    unavailable (F18).
    """
    if is_root:
        return {
            "availability": [
                {"topic": f"{app}/availability"},
                {
                    "topic": f"{app}/status",
                    "value_template": f"{{{{ {_STATUS_VALUE_EXPR} }}}}",
                },
            ],
            "availability_mode": "all",
            "payload_available": "online",
            "payload_not_available": "offline",
        }
    return {
        "availability": [
            {"topic": f"{app}/{device_name}/availability"},
            {
                "topic": f"{app}/status",
                "value_template": f"{{{{ {_STATUS_VALUE_EXPR} }}}}",
            },
        ],
        "availability_mode": "all",
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def _device_block(
    app: str, node_id: str, device_name: str, *, is_root: bool
) -> dict[str, Any]:
    """Build the HA ``device`` block for *device_name* (F19, ADR-058).

    Named devices get their own device entry linked to the app-level bridge
    via ``via_device``, so HA models each physical device separately instead
    of collapsing an entire app into one device. Root devices (no distinct
    device-name segment) attach straight to the bridge identity — for an
    unnamed device, the app *is* the device.
    """
    bridge_id = f"{_MANUFACTURER}_{node_id}"
    if is_root:
        return {
            "identifiers": [bridge_id],
            "name": app,
            "manufacturer": _MANUFACTURER,
        }
    return {
        "identifiers": [f"{bridge_id}_{_slugify(device_name)}"],
        "name": device_name,
        "manufacturer": _MANUFACTURER,
        "via_device": bridge_id,
    }


def _origin_block(app: str, app_version: str) -> dict[str, Any]:
    """Build the HA ``origin`` block — free diagnostics HA surfaces (F19)."""
    return {"name": app, "sw_version": app_version}


def _escape_openhab_string(value: str) -> str:
    """Escape a value for inclusion in an OpenHAB quoted field."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def _jsonpath_selector(path: tuple[str, ...]) -> str:
    """Return a JSONPath selector for *path* safe for OpenHAB transforms.

    Simple identifier segments use dot notation (``$.name``).  Any other
    segment uses bracket notation with single-quoted keys, escaping
    backslashes, quotes, and newlines so JSONPath or ``.things`` quoting
    metacharacters cannot corrupt the generated ``transformationPattern``.
    """
    return _path_segments_to_accessor("$", path)


def _infer_component(
    channel: ChannelSchema,
    prop: PropertySchema,
    ha: HaDiscoveryOverrides | None,
) -> str:
    """Infer HA component from archetype, JSON type, and channel direction."""
    effective = _effective_schema(prop.json_schema)
    json_type = effective.get("type", "string")

    # A send-only channel can only observe; read_only forces the same. Both
    # take the read-only component path regardless of archetype (F17, Bug 1).
    read_only = prop.consumer is not None and prop.consumer.read_only
    if read_only or channel.direction == "send":
        return "binary_sensor" if json_type == "boolean" else "sensor"

    if ha and ha.component:
        return ha.component

    archetype = channel.archetype
    # string with enum → select for commands
    if archetype == "command" and json_type == "string" and "enum" in effective:
        return "select"

    return _HA_COMPONENT_MAP.get((archetype, json_type), "sensor")


@dataclass(frozen=True, slots=True)
class HaDiscoveryPayload:
    """A single HA MQTT discovery message."""

    topic: str
    config: dict[str, Any]


HaEnrichHook = Callable[[ChannelSchema, "PropertySchema | None", dict[str, Any]], None]
"""Signature for an ``app.discovery(enrich=...)`` callback (F23).

Called once per emitted entity, immediately before its :class:`HaDiscoveryPayload`
is built, with the source *channel*, the source *prop* (``None`` for a composite
entity — see ADR-057 — which has no single backing property), and the *config*
dict assembled so far. The hook mutates *config* in place; its return value is
ignored. Runs last, after every schema-derived merge (``extra`` passthrough,
composite ``HaEntitySpec.extra``, consumer/HA overrides), so it always has the
final word — the escape hatch for whatever the schema cannot express.

Not called for the synthetic per-app bridge entity (ADR-058): it has no
source channel or property to pass, and it is framework plumbing rather than
schema-derived output.
"""


def _light_composite_defaults(config: dict[str, Any]) -> None:
    """Default a composite ``light`` entity to HA's JSON schema (F10, F20).

    HA's MQTT JSON light schema reads/writes the retained body as a single
    JSON object matching cosalette's own wire format directly — no per-field
    ``value_template`` is needed, unlike a scalar entity.
    """
    config.setdefault("schema", "json")


def _climate_composite_defaults(config: dict[str, Any]) -> None:
    """Drop the generic state/command topics for a composite ``climate`` entity.

    HA's MQTT climate platform has no single state/command topic — every
    capability (mode, target temperature, ...) needs its own
    ``<x>_state_topic`` / ``<x>_command_topic`` pair, which only the author
    can name via ``extra``.  Leaving the generic keys in place would produce
    a config HA silently ignores rather than one that fails loudly.
    """
    config.pop("state_topic", None)
    config.pop("command_topic", None)


# component → payload-builder defaults, applied before an entity's `extra`
# passthrough is merged last (F10: component selects a real builder).  Any
# component not listed here (including `cover`, which accepts the inherited
# state_topic/command_topic natively) gets no extra defaults.
_HA_COMPOSITE_BUILDERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "light": _light_composite_defaults,
    "climate": _climate_composite_defaults,
}

# (channel, spec, state_topic, command_topic) accumulator for
# _composite_payloads_for_device. *channel* is the first channel seen for the
# key — paired send/receive channels share one entity, so only one is kept as
# the enrichment-hook's representative channel (F23).
_CompositeMergeEntry = tuple[ChannelSchema, HaEntitySpec, str | None, str | None]


def _default_command_template(prop: PropertySchema) -> str:
    """Build a JSON-envelope command template keyed by the property name (F11).

    cosalette's wire format is a single JSON object per channel, so a command
    entity must publish ``{"<prop>": <value>}`` rather than a bare scalar.

    Type branches:

    - ``integer`` / ``number``: raw ``{{ value }}`` — HA sends numeric literals.
    - ``boolean``: ``{{ (value == 'ON') | lower }}`` — HA MQTT switch delivers
      ``ON`` / ``OFF`` which must map to JSON ``true`` / ``false``.
    - all others: ``{{ value | tojson }}`` — Jinja serialises the value as a
      JSON string so quotes and backslashes cannot break the envelope.
    """
    json_type = _effective_type(prop)
    if json_type == "boolean":
        value_expr = "{{ (value == 'ON') | lower }}"
    elif json_type in ("integer", "number"):
        value_expr = "{{ value }}"
    else:
        value_expr = "{{ value | tojson }}"
    return "{" + json.dumps(prop.name) + ": " + value_expr + "}"


def _derive_value_template(
    prop: PropertySchema, ha: HaDiscoveryOverrides | None
) -> str:
    """Derive the HA ``value_template`` for *prop* (explicit override wins).

    Builds the accessor from ``prop.path`` segments so nested properties
    (``meta.source`` → ``value_json.meta.source``) render correctly.
    Uses bracket notation for non-identifier segments and a ``join`` filter
    for arrays so a list is not emitted as a Python repr (F7).
    """
    if ha and ha.value_template:
        return ha.value_template
    path = prop.path if prop.path else (prop.name,)
    accessor = _path_segments_to_accessor("value_json", path)
    if _effective_type(prop) == "array":
        return f"{{{{ {accessor} | join(',') }}}}"
    return f"{{{{ {accessor} }}}}"


def _apply_topics_and_templates(
    config: dict[str, Any],
    channel: ChannelSchema,
    prop: PropertySchema,
    ha: HaDiscoveryOverrides | None,
) -> None:
    """Set state/command topics and value/command templates on *config*."""
    read_only = prop.consumer is not None and prop.consumer.read_only
    if read_only:
        # A read-only field observes its channel as state regardless of
        # direction and never publishes commands (F17).
        config["state_topic"] = channel.address
    else:
        if channel.direction in ("send", "both"):
            config["state_topic"] = channel.address
        if channel.direction in ("receive", "both"):
            config["command_topic"] = channel.address

    if "state_topic" in config:
        config["value_template"] = _derive_value_template(prop, ha)
    if "command_topic" in config:
        # Default a JSON envelope so commands are not published as bare scalars
        # that the app's own enforcement would reject (F11).
        config["command_template"] = (
            ha.command_template
            if ha and ha.command_template
            else _default_command_template(prop)
        )


# consumer attr → HA config key
_CONSUMER_FIELD_MAP: dict[str, str] = {
    "device_class": "device_class",
    "unit": "unit_of_measurement",
    "state_class": "state_class",
    "icon": "icon",
}


def _apply_consumer_fields(
    config: dict[str, Any],
    consumer: ConsumerMetadata,
    ha: HaDiscoveryOverrides | None,
    component: str,
) -> None:
    """Copy consumer metadata and HA overrides into *config*.

    Keys the target platform rejects are dropped: ``state_class`` is only valid
    on ``sensor`` and ``unit_of_measurement`` only on ``sensor`` / ``number``.
    ``device_class`` and ``icon`` are broadly accepted.
    """
    for attr, key in _CONSUMER_FIELD_MAP.items():
        value = getattr(consumer, attr)
        if not value:
            continue
        if key == "state_class" and component != "sensor":
            continue
        if key == "unit_of_measurement" and component not in ("sensor", "number"):
            continue
        config[key] = value

    # expire_after + extra passthrough (F13)
    if ha:
        if ha.expire_after is not None:
            config["expire_after"] = ha.expire_after
        if ha.extra:
            config.update(ha.extra)


def _apply_type_constraints(
    config: dict[str, Any],
    prop: PropertySchema,
    component: str,
) -> None:
    """Emit component-specific constraints already present in the schema.

    ``number`` entities gain ``min`` / ``max`` / ``step`` from
    ``minimum`` / ``maximum`` / ``multipleOf`` (F14); ``select`` entities gain
    ``options`` from ``enum`` (F15).
    """
    effective = _effective_schema(prop.json_schema)
    if component == "number":
        if "minimum" in effective:
            config["min"] = effective["minimum"]
        if "maximum" in effective:
            config["max"] = effective["maximum"]
        if "multipleOf" in effective:
            config["step"] = effective["multipleOf"]
    elif component == "select" and "enum" in effective:
        config["options"] = list(effective["enum"])


def _will_emit_entities(channel: ChannelSchema) -> bool:
    """True if *channel* will contribute at least one HA discovery entity."""
    if channel.ha_entities:
        return True
    return any(_is_emittable(p) for p in channel.properties.values())


def _is_emittable(prop: PropertySchema) -> bool:
    """A prop yields a consumer entity only if annotated and not an array item.

    Array-of-objects children (``events[].title``) have no single value, so an
    entity for them would be arbitrary; they are skipped and warned instead.
    """
    return prop.consumer is not None and not prop.is_array_item


def _is_consumer_visible(channel: ChannelSchema) -> bool:
    """True if the channel should appear in consumer generation output (ADR-054)."""
    return channel.scope != "all_apps" and channel.archetype != "stream"


def has_consumer_visible_channels(registry: SchemaRegistry) -> bool:
    """True if *registry* has any channel eligible for consumer generation.

    Used by the ``ha-discovery``/``openhab`` CLI commands (F23) to distinguish
    "this app genuinely has nothing to publish" from "this app is fully wired
    but its channels carry no ``consumer()``/``ha_entities()`` annotations at
    all" — the latter should be reported, not shrugged off with a silent ``[]``.
    """
    return any(_is_consumer_visible(c) for c in registry.channels.values())


@dataclass(frozen=True, slots=True)
class HaDiscoveryGenerator:
    """Generate Home Assistant MQTT discovery payloads from a schema registry.

    Each property annotated with ``x-cosalette-consumer`` produces one
    discovery entity.  Component type is inferred from archetype and JSON
    schema type, or overridden via ``x-cosalette-ha-discovery``.
    """

    registry: SchemaRegistry
    discovery_prefix: str = "homeassistant"
    enrich: HaEnrichHook | None = None

    def generate(self) -> list[HaDiscoveryPayload]:
        """Return discovery payloads for annotated properties and composite entities."""
        payloads: list[HaDiscoveryPayload] = []
        composite_channels: list[ChannelSchema] = []
        for channel in sorted(self.registry.channels.values(), key=lambda c: c.address):
            if not _is_consumer_visible(channel):
                continue
            if channel.ha_entities:
                # A channel-level composite entity (ADR-057) replaces the
                # per-property scatter for that channel entirely (F20).
                composite_channels.append(channel)
                continue
            payloads.extend(self._payloads_for_channel(channel))
        payloads.extend(self._composite_payloads(composite_channels))
        payloads.extend(self._bridge_payloads())
        return payloads

    # -- bridge devices (ADR-058) -------------------------------------------

    def _bridge_payloads(self) -> list[HaDiscoveryPayload]:
        """Emit one diagnostic bridge entity per app that has a named device.

        HA's device registry does not materialise a device purely from a
        ``via_device`` reference in another entity's config — this entity is
        what actually causes the app-level bridge device to exist, so every
        named device's ``via_device`` link resolves (F19).
        """
        apps: set[str] = set()
        for channel in self.registry.channels.values():
            if not _is_consumer_visible(channel) or not _will_emit_entities(channel):
                continue
            app = channel.app_name or "unknown"
            if not _is_root_device(self.registry, _resolve_device(channel)):
                apps.add(app)
        return [self._build_bridge_payload(app) for app in sorted(apps)]

    def _build_bridge_payload(self, app: str) -> HaDiscoveryPayload:
        node_id = _slugify(app)
        bridge_id = f"cosalette_{node_id}"
        object_id = "bridge"
        unique_id = f"{bridge_id}_{object_id}"
        topic = f"{self.discovery_prefix}/binary_sensor/{node_id}/{object_id}/config"

        config: dict[str, Any] = {
            "name": "Bridge",
            "unique_id": unique_id,
            "object_id": object_id,
            "state_topic": f"{app}/status",
            "value_template": (
                f"{{{{ 'ON' if ({_STATUS_VALUE_EXPR}) == 'online' else 'OFF' }}}}"
            ),
            "device_class": "connectivity",
            "entity_category": "diagnostic",
            # No availability block: the bridge IS the connectivity indicator.
            # Its state machine (ON/OFF via LWT) is the availability signal —
            # hiding it as "unavailable" at disconnect defeats its purpose.
            "device": _device_block(app, node_id, app, is_root=True),
            "origin": _origin_block(app, self.registry.app_version),
        }
        return HaDiscoveryPayload(topic=topic, config=config)

    # -- composite entities (ADR-057) --------------------------------------

    def _composite_payloads(
        self, channels: list[ChannelSchema]
    ) -> list[HaDiscoveryPayload]:
        """Build one payload per composite entity, grouped by (app, device).

        A ``device`` archetype channel with ``payload_model=`` emits a paired
        send ``/state`` and receive ``/set`` channel sharing one model
        (ADR-055), so both halves of a channel-level entity spec must be
        merged into one config rather than emitted twice, each incomplete.
        """
        groups: dict[tuple[str, str], list[ChannelSchema]] = {}
        for channel in channels:
            app = channel.app_name or "unknown"
            groups.setdefault((app, _resolve_device(channel)), []).append(channel)

        payloads: list[HaDiscoveryPayload] = []
        for (app, device_name), group_channels in sorted(groups.items()):
            payloads.extend(
                self._composite_payloads_for_device(app, device_name, group_channels)
            )
        return payloads

    def _composite_payloads_for_device(
        self, app: str, device_name: str, channels: list[ChannelSchema]
    ) -> list[HaDiscoveryPayload]:
        """Merge paired send/receive channels into one payload per entity key."""
        # Python 3.7+ dicts preserve insertion order; no parallel list needed.
        merged: dict[tuple[str, str | None], _CompositeMergeEntry] = {}
        for channel in channels:
            for spec in channel.ha_entities:
                key = (spec.component, spec.name)
                if key in merged:
                    # paired /state+/set channels share the same spec; accumulate
                    # topics, keeping the first-seen channel as representative.
                    rep_channel, _, state_topic, command_topic = merged[key]
                else:
                    rep_channel, state_topic, command_topic = channel, None, None
                if channel.direction in ("send", "both"):
                    state_topic = channel.address
                if channel.direction in ("receive", "both"):
                    command_topic = channel.address
                merged[key] = (rep_channel, spec, state_topic, command_topic)

        node_id = _slugify(app)
        return [
            self._build_composite_payload(
                app, device_name, node_id, channel, spec, state, command
            )
            for channel, spec, state, command in merged.values()
        ]

    def _build_composite_payload(
        self,
        app: str,
        device_name: str,
        node_id: str,
        channel: ChannelSchema,
        spec: HaEntitySpec,
        state_topic: str | None,
        command_topic: str | None,
    ) -> HaDiscoveryPayload:
        object_id = _slugify(f"{device_name}_{spec.name or spec.component}")
        unique_id = f"cosalette_{node_id}_{object_id}"
        topic = f"{self.discovery_prefix}/{spec.component}/{node_id}/{object_id}/config"

        config: dict[str, Any] = {
            "name": spec.name or device_name,
            "unique_id": unique_id,
            "object_id": object_id,
        }
        if state_topic:
            config["state_topic"] = state_topic
        if command_topic:
            config["command_topic"] = command_topic

        builder = _HA_COMPOSITE_BUILDERS.get(spec.component)
        if builder:
            builder(config)

        is_root = _is_root_device(self.registry, device_name)
        config.update(_availability_block(app, device_name, is_root=is_root))
        config["device"] = _device_block(app, node_id, device_name, is_root=is_root)
        config["origin"] = _origin_block(app, self.registry.app_version)
        # extra is an open passthrough merged last, mirroring
        # HaDiscoveryOverrides.extra's override-last semantics (ADR-056) — it
        # can add new keys or override any computed field, including device/unique_id.
        config.update(spec.extra)
        self._apply_enrichment(channel, None, config)

        return HaDiscoveryPayload(topic=topic, config=config)

    # -- scalar per-property entities --------------------------------------

    def _payloads_for_channel(self, channel: ChannelSchema) -> list[HaDiscoveryPayload]:
        results: list[HaDiscoveryPayload] = []
        app = channel.app_name or "unknown"
        device_name = _resolve_device(channel)

        for prop in sorted(channel.properties.values(), key=lambda p: p.name):
            if not _is_emittable(prop):
                continue
            results.append(self._build_payload(channel, prop, app, device_name))
        return results

    def _build_payload(
        self,
        channel: ChannelSchema,
        prop: PropertySchema,
        app: str,
        device_name: str,
    ) -> HaDiscoveryPayload:
        consumer = prop.consumer
        assert consumer is not None  # caller guarantees  # noqa: S101
        ha = prop.ha_discovery

        component = _infer_component(channel, prop, ha)
        # Command entities carry a direction suffix so a state entity and a
        # command entity for the same device+property do not collide on
        # object_id, unique_id or discovery topic (F4).  State entities
        # stay bare for backward compatibility; read_only fields are always state.
        is_command = (
            channel.archetype == "command"
            and not consumer.read_only
            and channel.direction in ("receive", "both")
        )
        suffix = "_cmd" if is_command else ""
        object_id = _slugify(f"{device_name}_{prop.name}") + suffix
        node_id = _slugify(app)
        unique_id = f"cosalette_{node_id}_{object_id}"

        topic = f"{self.discovery_prefix}/{component}/{node_id}/{object_id}/config"

        config: dict[str, Any] = {
            "name": consumer.display_name or prop.name,
            "unique_id": unique_id,
            "object_id": object_id,
        }

        _apply_topics_and_templates(config, channel, prop, ha)
        _apply_consumer_fields(config, consumer, ha, component)
        _apply_type_constraints(config, prop, component)

        is_root = _is_root_device(self.registry, device_name)
        config.update(_availability_block(app, device_name, is_root=is_root))
        config["device"] = _device_block(app, node_id, device_name, is_root=is_root)
        config["origin"] = _origin_block(app, self.registry.app_version)
        self._apply_enrichment(channel, prop, config)

        return HaDiscoveryPayload(topic=topic, config=config)

    def _apply_enrichment(
        self,
        channel: ChannelSchema,
        prop: PropertySchema | None,
        config: dict[str, Any],
    ) -> None:
        """Run the ``app.discovery(enrich=...)`` hook, if any (F23)."""
        if self.enrich is not None:
            self.enrich(channel, prop, config)


# ---------------------------------------------------------------------------
# OpenHAB
# ---------------------------------------------------------------------------

# device_class → (item_type, format_pattern)
_OPENHAB_TYPE_MAP: dict[str, tuple[str, str]] = {
    "temperature": ("Number:Temperature", "%.1f °C"),
    "humidity": ("Number:Dimensionless", "%.0f %%"),
    "carbon_dioxide": ("Number:Dimensionless", "%d ppm"),
    "volatile_organic_compounds_parts": ("Number:Dimensionless", "%d ppb"),
    "pressure": ("Number:Pressure", "%.0f hPa"),
    "battery": ("Number:Dimensionless", "%d %%"),
}


def _openhab_item_type(prop: PropertySchema) -> str:
    """Resolve OpenHAB item type from overrides or device class."""
    if prop.openhab and prop.openhab.item_type:
        return prop.openhab.item_type

    dc = prop.consumer.device_class if prop.consumer else None
    if dc and dc in _OPENHAB_TYPE_MAP:
        return _OPENHAB_TYPE_MAP[dc][0]

    json_type = _effective_type(prop)
    return {"number": "Number", "integer": "Number", "boolean": "Switch"}.get(
        json_type, "String"
    )


def _openhab_format_pattern(prop: PropertySchema) -> str:
    """Resolve display format pattern."""
    dc = prop.consumer.device_class if prop.consumer else None
    unit = prop.consumer.unit if prop.consumer else None
    if dc and dc in _OPENHAB_TYPE_MAP:
        return _OPENHAB_TYPE_MAP[dc][1]
    if unit:
        return f"%s {unit}"
    return "%s"


def _openhab_thing_uid(broker_uid: str, app: str, device: str) -> str:
    """Build a Thing UID: ``mqtt:topic:<broker>:<app>_<device>``."""
    return f"mqtt:topic:{broker_uid}:{_slugify(app)}_{_slugify(device)}"


def _openhab_channel_local(prop_name: str, *, is_command: bool) -> str:
    """Local channel name: ``<slug>`` for state, ``<slug>_cmd`` for command (F2)."""
    slug = _slugify(prop_name)
    return f"{slug}_cmd" if is_command else slug


def _openhab_channel_uid(
    broker_uid: str,
    app: str,
    device: str,
    prop_name: str,
    *,
    is_command: bool = False,
) -> str:
    """Build a channel UID for an Item link."""
    thing = _openhab_thing_uid(broker_uid, app, device)
    return f"{thing}:{_openhab_channel_local(prop_name, is_command=is_command)}"


def _openhab_item_id(
    app: str, device: str, prop_name: str, *, is_command: bool = False
) -> str:
    """Build an Item ID: ``App_Device_Property`` (CamelCase segments).

    The device and prop_name are slugified so flattened or non-identifier names
    (e.g. ``events[].title``, ``living/ceiling``) yield legal identifiers.
    Command Items gain a trailing ``Cmd`` segment so they never collide with
    their state Item (F3).
    """
    parts = [app, _slugify(device), _slugify(prop_name)]
    if is_command:
        parts.append("cmd")
    return "_".join(p.replace("-", "_").title().replace("_", "") for p in parts)


def _openhab_channel_type(prop: PropertySchema) -> str:
    """Map JSON schema type to OpenHAB channel type descriptor.

    Honours ``x-cosalette-openhab.channel_type`` when set, mirroring how
    ``item_type`` already overrides the Item type (F9/F21) — without it, an
    array field annotated ``item_type="Color"`` still bound to an inferred
    ``string`` channel, which openHAB rejects.
    """
    if prop.openhab and prop.openhab.channel_type:
        return prop.openhab.channel_type
    json_type = _effective_type(prop)
    return {"number": "number", "integer": "number", "boolean": "switch"}.get(
        json_type, "string"
    )


def _format_openhab_channel_param(value: Any) -> str:
    """Render a ``channel_params`` value for embedding in a ``.things`` parameter.

    Booleans and strings are quoted (matching the existing ``on="true"``
    convention); numbers are emitted bare, matching openHAB's own ``min=0,
    max=255, step=1`` style.
    """
    if isinstance(value, bool):
        return f'"{str(value).lower()}"'
    if isinstance(value, (int, float)):
        return str(value)
    return f'"{_escape_openhab_string(str(value))}"'


def _openhab_format_before_publish(prop: PropertySchema) -> str:
    """Return the ``formatBeforePublish`` value for a command channel (F12).

    openHAB's ``formatBeforePublish`` builds the outbound payload; cosalette
    expects a JSON envelope ``{"<prop>": <value>}``.  String values are
    JSON-quoted.  Quotes are escaped for embedding in the ``.things`` string.

    For ``boolean`` (Switch) channels ``%s`` receives the mapped value
    (``true`` / ``false``) from the channel's ``on`` / ``off`` parameters,
    so no extra quoting is needed — the result is valid JSON.
    """
    json_type = _effective_type(prop)
    placeholder = "%s" if json_type in ("integer", "number", "boolean") else '\\"%s\\"'
    key = json.dumps(prop.name).replace('"', '\\"')
    return "{" + key + ":" + placeholder + "}"


def _openhab_groups(prop: PropertySchema, group_name: str) -> str:
    """Return the ``(group, ...)`` segment for an ``.items`` line."""
    if prop.openhab and prop.openhab.groups:
        return f"({', '.join(prop.openhab.groups)})"
    return f"({group_name})"


def _openhab_tags(prop: PropertySchema) -> str:
    """Return the ``["Tag", ...]`` segment (or empty string)."""
    if prop.openhab and prop.openhab.tags:
        return "[" + ", ".join(f'"{t}"' for t in prop.openhab.tags) + "]"
    return ""


def _format_item_line(
    broker_uid: str,
    app: str,
    device: str,
    prop: PropertySchema,
    group_name: str,
    *,
    is_command: bool = False,
) -> str:
    """Build a single OpenHAB ``.items`` line for *prop*."""
    consumer = prop.consumer
    assert consumer is not None  # noqa: S101

    item_type = _openhab_item_type(prop)
    item_id = _openhab_item_id(app, device, prop.name, is_command=is_command)
    label = (
        prop.openhab.label
        if prop.openhab and prop.openhab.label
        else consumer.display_name or prop.name
    )
    label = _escape_openhab_string(label)
    fmt = _openhab_format_pattern(prop)
    icon = consumer.icon or consumer.device_class or ""
    channel_uid = _openhab_channel_uid(
        broker_uid, app, device, prop.name, is_command=is_command
    )

    groups = _openhab_groups(prop, group_name)
    tags = _openhab_tags(prop)

    icon_part = f"<{icon}>  " if icon else ""
    tags_part = f"  {tags}" if tags else ""

    return (
        f'{item_type}  {item_id}  "{label} [{fmt}]"  '
        f"{icon_part}{groups}{tags_part}  "
        f'{{ channel="{channel_uid}" }}'
    )


def _channel_directions(channel: ChannelSchema, prop: PropertySchema) -> list[bool]:
    """Return command flags (``False``=state, ``True``=command) to emit for *prop*.

    A ``read_only`` property is always state-only regardless of channel
    direction (F17).
    """
    read_only = prop.consumer is not None and prop.consumer.read_only
    result: list[bool] = []
    if read_only or channel.direction in ("send", "both"):
        result.append(False)
    if not read_only and channel.direction in ("receive", "both"):
        result.append(True)
    return result


def _channel_lines(
    ch_type: str, local: str, label: str, params: list[str]
) -> list[str]:
    """Render an OpenHAB channel entry with comma-joined parameters."""
    lines = [f'        Type {ch_type} : {local} "{label}" [']
    last = len(params) - 1
    for i, param in enumerate(params):
        comma = "," if i < last else ""
        lines.append(f"            {param}{comma}")
    lines.append("        ]")
    return lines


def _channel_entries(channel: ChannelSchema, prop: PropertySchema) -> list[str]:
    """Render the ``.things`` channel entries for *prop* (F2, F8, F12, F21)."""
    assert prop.consumer is not None  # noqa: S101
    prop_label = _escape_openhab_string(prop.consumer.display_name or prop.name)
    ch_type = _openhab_channel_type(prop)
    topic = channel.address
    channel_params = prop.openhab.channel_params if prop.openhab else {}

    entries: list[str] = []
    for is_command in _channel_directions(channel, prop):
        local = _openhab_channel_local(prop.name, is_command=is_command)
        params: dict[str, str] = {}
        if is_command:
            # Command channels build an outbound JSON envelope; the inbound-only
            # transformationPattern is dropped (F12).
            params["commandTopic"] = f'commandTopic="{topic}"'
            params["formatBeforePublish"] = (
                f'formatBeforePublish="{_openhab_format_before_publish(prop)}"'
            )
        else:
            params["stateTopic"] = f'stateTopic="{topic}"'
            params["transformationPattern"] = (
                'transformationPattern="JSONPATH:'
                + _jsonpath_selector(prop.path or (prop.name,))
                + '"'
            )
        if ch_type == "switch":
            # JSON booleans need explicit on/off or the Item stays UNDEF (F8).
            params["on"] = 'on="true"'
            params["off"] = 'off="false"'
        # channel_params is merged last: it can add a new parameter (e.g.
        # colorMode) or override a computed default in place (F21).
        for key, value in channel_params.items():
            params[key] = f"{key}={_format_openhab_channel_param(value)}"
        entries.extend(
            _channel_lines(ch_type, local, prop_label, list(params.values()))
        )
    return entries


@dataclass(frozen=True, slots=True)
class OpenHabGenerator:
    """Generate OpenHAB ``.things`` and ``.items`` files from a schema registry."""

    registry: SchemaRegistry
    broker_uid: str = "broker"

    def generate_things(self) -> str:
        """Return OpenHAB ``.things`` file content."""
        lines = [
            "// Generated by cosalette schema openhab",
            "",
        ]
        for (app, device), channels in self._channels_by_device():
            lines.extend(self._thing_block(app, device, channels))
        return "\n".join(lines).rstrip() + "\n"

    def generate_items(self) -> str:
        """Return OpenHAB ``.items`` file content."""
        lines = [
            "// Generated by cosalette schema openhab",
            "",
        ]
        for (app, device), channels in self._channels_by_device():
            lines.extend(self._items_for_device(app, device, channels))
        return "\n".join(lines).rstrip() + "\n"

    # -- private helpers --------------------------------------------------

    def consumer_channels(self) -> list[ChannelSchema]:
        """Return channels that have at least one consumer-annotated property.

        Public so the ``openhab`` CLI command (F23) can detect a registry
        that has consumer-visible channels but yields no consumer-annotated
        ones — the same "report, don't shrug" gap as ``ha-discovery``.
        """
        return sorted(
            [
                ch
                for ch in self.registry.channels.values()
                if _is_consumer_visible(ch)
                and any(p.consumer is not None for p in ch.properties.values())
            ],
            key=lambda c: c.address,
        )

    def _channels_by_device(
        self,
    ) -> list[tuple[tuple[str, str], list[ChannelSchema]]]:
        """Group consumer channels by ``(app, resolved device)`` (F1).

        A device with a state channel and a command channel becomes a single
        Thing rather than two blocks sharing one UID.
        """
        grouped: dict[tuple[str, str], list[ChannelSchema]] = {}
        for channel in self.consumer_channels():
            app = channel.app_name or "unknown"
            grouped.setdefault((app, _resolve_device(channel)), []).append(channel)
        return sorted(grouped.items(), key=lambda kv: kv[0])

    def _thing_block(
        self, app: str, device: str, channels: list[ChannelSchema]
    ) -> list[str]:
        if not any(_is_emittable(p) for ch in channels for p in ch.properties.values()):
            return []
        thing_uid = _openhab_thing_uid(self.broker_uid, app, device)
        # Escape before embedding in the quoted .things label — app/device names
        # permit quotes/backslashes (validate_mqtt_name only bars /+#/control
        # chars), which would otherwise break out of the DSL string.
        label = _escape_openhab_string(f"{app} {device}")

        lines = [
            f'Thing {thing_uid} "{label}" (mqtt:broker:{self.broker_uid}) {{',
            "    Channels:",
        ]
        for channel in channels:  # already address-ordered from _channels_by_device
            for prop in sorted(channel.properties.values(), key=lambda p: p.name):
                if not _is_emittable(prop):
                    continue
                lines.extend(_channel_entries(channel, prop))
        lines.append("}")
        lines.append("")
        return lines

    def _items_for_device(
        self, app: str, device: str, channels: list[ChannelSchema]
    ) -> list[str]:
        group_name = f"g{app.replace('-', '_').title().replace('_', '')}"
        lines: list[str] = []
        for channel in channels:  # already address-ordered from _channels_by_device
            for prop in sorted(channel.properties.values(), key=lambda p: p.name):
                if not _is_emittable(prop):
                    continue
                for is_command in _channel_directions(channel, prop):
                    lines.append(
                        _format_item_line(
                            self.broker_uid,
                            app,
                            device,
                            prop,
                            group_name,
                            is_command=is_command,
                        )
                    )
        return lines


# ---------------------------------------------------------------------------
# JSON serialization helper
# ---------------------------------------------------------------------------


def ha_discovery_to_json(payloads: list[HaDiscoveryPayload]) -> str:
    """Serialize discovery payloads to a JSON array of {topic, config}."""
    return json.dumps(
        [{"topic": p.topic, "config": p.config} for p in payloads],
        indent=2,
    )
