"""Consumer code generation from AsyncAPI schemas.

Generates Home Assistant MQTT discovery payloads and OpenHAB
``.things``/``.items`` configuration from :class:`SchemaRegistry`.

Finding reference (proposal cos-fr9s):
    F4  — unique_id / object_id collision for state + command on same device+prop
    F5  — device-name extraction for nested / router addresses
    F6  — optional type inference (anyOf/oneOf/allOf with null)
    F7  — array value_template: join filter instead of Python repr
    F8  — openHAB switch: explicit on/off so Item does not stay UNDEF
    F11 — default command_template: JSON-envelope rather than bare scalar
    F12 — openHAB formatBeforePublish: outbound JSON-envelope for commands
    F14 — number constraints (min/max/step) from JSON schema
    F15 — select options from enum
    F17 — read_only forces state-only regardless of channel direction

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from cosalette._schema import (
    ChannelSchema,
    ConsumerMetadata,
    HaDiscoveryOverrides,
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


def _slugify(value: str) -> str:
    """Convert a string to a slug suitable for HA object IDs."""
    return _SLUG_RE.sub("_", value.lower()).strip("_")


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


def _escape_openhab_string(value: str) -> str:
    """Escape a value for inclusion in an OpenHAB quoted field."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def _jsonpath_selector(name: str) -> str:
    """Return a JSONPath selector for *name* safe for OpenHAB transforms.

    Simple identifiers use dot notation (``$.name``).  Any other name uses
    bracket notation with single-quoted keys, escaping backslashes, quotes,
    and newlines (e.g. ``$['a\'b']`` for a name containing a single quote),
    so JSONPath or ``.things`` quoting metacharacters cannot corrupt the
    generated ``transformationPattern``.
    """
    if _IDENTIFIER_RE.match(name):
        return f"$.{name}"
    escaped = (
        name.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "")
    )
    return f"$['{escaped}']"


def _infer_component(
    archetype: str | None,
    prop: PropertySchema,
    ha: HaDiscoveryOverrides | None,
) -> str:
    """Infer HA component from archetype + JSON schema type."""
    effective = _effective_schema(prop.json_schema)
    json_type = effective.get("type", "string")

    # read_only forces a read-only component regardless of direction (F17).
    if prop.consumer is not None and prop.consumer.read_only:
        return "binary_sensor" if json_type == "boolean" else "sensor"

    if ha and ha.component:
        return ha.component

    # string with enum → select for commands
    if archetype == "command" and json_type == "string" and "enum" in effective:
        return "select"

    return _HA_COMPONENT_MAP.get((archetype, json_type), "sensor")


@dataclass(frozen=True, slots=True)
class HaDiscoveryPayload:
    """A single HA MQTT discovery message."""

    topic: str
    config: dict[str, Any]


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

    Uses bracket notation when the property name is not a simple identifier and
    a ``join`` filter for arrays so a list is not emitted as a Python repr (F7).
    """
    if ha and ha.value_template:
        return ha.value_template
    if _IDENTIFIER_RE.match(prop.name):
        accessor = f"value_json.{prop.name}"
    else:
        escaped = prop.name.replace("\\", "\\\\").replace("'", "\\'")
        accessor = f"value_json['{escaped}']"
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

    if ha and ha.expire_after is not None:
        config["expire_after"] = ha.expire_after


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


def _is_consumer_visible(channel: ChannelSchema) -> bool:
    """True if the channel should appear in consumer generation output (ADR-054)."""
    return channel.scope != "all_apps" and channel.archetype != "stream"


@dataclass(frozen=True, slots=True)
class HaDiscoveryGenerator:
    """Generate Home Assistant MQTT discovery payloads from a schema registry.

    Each property annotated with ``x-cosalette-consumer`` produces one
    discovery entity.  Component type is inferred from archetype and JSON
    schema type, or overridden via ``x-cosalette-ha-discovery``.
    """

    registry: SchemaRegistry
    discovery_prefix: str = "homeassistant"

    def generate(self) -> list[HaDiscoveryPayload]:
        """Return discovery payloads for all annotated properties."""
        payloads: list[HaDiscoveryPayload] = []
        for channel in sorted(self.registry.channels.values(), key=lambda c: c.address):
            if not _is_consumer_visible(channel):
                continue
            payloads.extend(self._payloads_for_channel(channel))
        return payloads

    # -- private helpers --------------------------------------------------

    def _payloads_for_channel(self, channel: ChannelSchema) -> list[HaDiscoveryPayload]:
        results: list[HaDiscoveryPayload] = []
        app = channel.app_name or "unknown"
        device_name = _resolve_device(channel)

        for prop in sorted(channel.properties.values(), key=lambda p: p.name):
            if prop.consumer is None:
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

        component = _infer_component(channel.archetype, prop, ha)
        # Command entities carry a direction suffix so a state entity and a
        # command entity for the same device+property do not collide on
        # object_id, unique_id or discovery topic (F4).  Keyed on archetype so
        # direction="both" command channels also get the suffix.  State entities
        # stay bare for backward compatibility; read_only fields are always state.
        is_command = channel.archetype == "command" and not consumer.read_only
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

        config["device"] = {
            "identifiers": [f"cosalette_{node_id}"],
            "name": app,
            "manufacturer": "cosalette",
        }

        return HaDiscoveryPayload(topic=topic, config=config)


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

    The device is slugified first so a nested name containing ``/`` (e.g.
    ``living/ceiling``) yields a legal identifier.  Command Items gain a
    trailing ``Cmd`` segment so they never collide with their state Item (F3).
    """
    parts = [app, _slugify(device), prop_name]
    if is_command:
        parts.append("cmd")
    return "_".join(p.replace("-", "_").title().replace("_", "") for p in parts)


def _openhab_channel_type(prop: PropertySchema) -> str:
    """Map JSON schema type to OpenHAB channel type descriptor."""
    json_type = _effective_type(prop)
    return {"number": "number", "integer": "number", "boolean": "switch"}.get(
        json_type, "string"
    )


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
    """Render the ``.things`` channel entries for *prop* (F2, F8, F12)."""
    assert prop.consumer is not None  # noqa: S101
    prop_label = _escape_openhab_string(prop.consumer.display_name or prop.name)
    ch_type = _openhab_channel_type(prop)
    topic = channel.address

    entries: list[str] = []
    for is_command in _channel_directions(channel, prop):
        local = _openhab_channel_local(prop.name, is_command=is_command)
        if is_command:
            # Command channels build an outbound JSON envelope; the inbound-only
            # transformationPattern is dropped (F12).
            params = [
                f'commandTopic="{topic}"',
                f'formatBeforePublish="{_openhab_format_before_publish(prop)}"',
            ]
        else:
            params = [
                f'stateTopic="{topic}"',
                f'transformationPattern="JSONPATH:{_jsonpath_selector(prop.name)}"',
            ]
        if ch_type == "switch":
            # JSON booleans need explicit on/off or the Item stays UNDEF (F8).
            params.extend(['on="true"', 'off="false"'])
        entries.extend(_channel_lines(ch_type, local, prop_label, params))
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

    def _consumer_channels(self) -> list[ChannelSchema]:
        """Return channels that have at least one consumer-annotated property."""
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
        for channel in self._consumer_channels():
            app = channel.app_name or "unknown"
            grouped.setdefault((app, _resolve_device(channel)), []).append(channel)
        return sorted(grouped.items(), key=lambda kv: kv[0])

    def _thing_block(
        self, app: str, device: str, channels: list[ChannelSchema]
    ) -> list[str]:
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
                if prop.consumer is None:
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
                if prop.consumer is None:
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
