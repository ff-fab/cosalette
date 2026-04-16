"""Consumer code generation from AsyncAPI schemas.

Generates Home Assistant MQTT discovery payloads and OpenHAB
``.things``/``.items`` configuration from :class:`SchemaRegistry`.

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


def _escape_openhab_string(value: str) -> str:
    """Escape a value for inclusion in an OpenHAB quoted field."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def _infer_component(
    archetype: str | None,
    prop: PropertySchema,
    ha: HaDiscoveryOverrides | None,
) -> str:
    """Infer HA component from archetype + JSON schema type."""
    if ha and ha.component:
        return ha.component

    json_type = prop.json_schema.get("type", "string")
    # string with enum → select for commands
    if archetype == "command" and json_type == "string" and "enum" in prop.json_schema:
        return "select"

    return _HA_COMPONENT_MAP.get((archetype, json_type), "sensor")


def _device_name_from_address(address: str) -> str:
    """Extract device segment from ``app/device/suffix`` address."""
    parts = address.split("/")
    if len(parts) >= 2:
        return parts[1]
    return parts[0]


@dataclass(frozen=True, slots=True)
class HaDiscoveryPayload:
    """A single HA MQTT discovery message."""

    topic: str
    config: dict[str, Any]


def _apply_topics_and_templates(
    config: dict[str, Any],
    channel: ChannelSchema,
    prop: PropertySchema,
    ha: HaDiscoveryOverrides | None,
) -> None:
    """Set state/command topics and value/command templates on *config*."""
    if channel.direction in ("send", "both"):
        config["state_topic"] = channel.address
    if channel.direction in ("receive", "both"):
        config["command_topic"] = channel.address

    value_tpl = ha.value_template if ha else None
    if not value_tpl:
        # Use bracket notation for safety when prop name isn't a simple identifier
        if _IDENTIFIER_RE.match(prop.name):
            value_tpl = f"{{{{ value_json.{prop.name} }}}}"
        else:
            value_tpl = "{{{{ value_json['{}'] }}}}".format(
                prop.name.replace("\\", "\\\\").replace("'", "\\'")
            )
    if "state_topic" in config:
        config["value_template"] = value_tpl
    if ha and ha.command_template and "command_topic" in config:
        config["command_template"] = ha.command_template


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
) -> None:
    """Copy consumer metadata and HA overrides into *config*."""
    for attr, key in _CONSUMER_FIELD_MAP.items():
        value = getattr(consumer, attr)
        if value:
            config[key] = value

    if ha and ha.expire_after is not None:
        config["expire_after"] = ha.expire_after


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
            if channel.scope == "all_apps":
                continue
            payloads.extend(self._payloads_for_channel(channel))
        return payloads

    # -- private helpers --------------------------------------------------

    def _payloads_for_channel(self, channel: ChannelSchema) -> list[HaDiscoveryPayload]:
        results: list[HaDiscoveryPayload] = []
        app = channel.app_name or "unknown"
        device_name = _device_name_from_address(channel.address)

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
        object_id = _slugify(f"{device_name}_{prop.name}")
        node_id = _slugify(app)
        unique_id = f"cosalette_{_slugify(app)}_{object_id}"

        topic = f"{self.discovery_prefix}/{component}/{node_id}/{object_id}/config"

        config: dict[str, Any] = {
            "name": consumer.display_name or prop.name,
            "unique_id": unique_id,
            "object_id": object_id,
        }

        _apply_topics_and_templates(config, channel, prop, ha)
        _apply_consumer_fields(config, consumer, ha)

        config["device"] = {
            "identifiers": [f"cosalette_{_slugify(app)}"],
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

    json_type = prop.json_schema.get("type", "string")
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


def _openhab_channel_uid(broker_uid: str, app: str, device: str, prop_name: str) -> str:
    """Build a channel UID for an Item link."""
    thing = _openhab_thing_uid(broker_uid, app, device)
    return f"{thing}:{_slugify(prop_name)}"


def _openhab_item_id(app: str, device: str, prop_name: str) -> str:
    """Build an Item ID: ``App_Device_Property`` (CamelCase segments)."""
    parts = [app, device, prop_name]
    return "_".join(p.replace("-", "_").title().replace("_", "") for p in parts)


def _openhab_channel_type(prop: PropertySchema) -> str:
    """Map JSON schema type to OpenHAB channel type descriptor."""
    json_type = prop.json_schema.get("type", "string")
    return {"number": "number", "integer": "number", "boolean": "switch"}.get(
        json_type, "string"
    )


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
) -> str:
    """Build a single OpenHAB ``.items`` line for *prop*."""
    consumer = prop.consumer
    assert consumer is not None  # noqa: S101

    item_type = _openhab_item_type(prop)
    item_id = _openhab_item_id(app, device, prop.name)
    label = (
        prop.openhab.label
        if prop.openhab and prop.openhab.label
        else consumer.display_name or prop.name
    )
    label = _escape_openhab_string(label)
    fmt = _openhab_format_pattern(prop)
    icon = consumer.icon or consumer.device_class or ""
    channel_uid = _openhab_channel_uid(broker_uid, app, device, prop.name)

    groups = _openhab_groups(prop, group_name)
    tags = _openhab_tags(prop)

    icon_part = f"<{icon}>  " if icon else ""
    tags_part = f"  {tags}" if tags else ""

    return (
        f'{item_type}  {item_id}  "{label} [{fmt}]"  '
        f"{icon_part}{groups}{tags_part}  "
        f'{{ channel="{channel_uid}" }}'
    )


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
        for channel in self._consumer_channels():
            lines.extend(self._thing_block(channel))
        return "\n".join(lines).rstrip() + "\n"

    def generate_items(self) -> str:
        """Return OpenHAB ``.items`` file content."""
        lines = [
            "// Generated by cosalette schema openhab",
            "",
        ]
        for channel in self._consumer_channels():
            lines.extend(self._items_for_channel(channel))
        return "\n".join(lines).rstrip() + "\n"

    # -- private helpers --------------------------------------------------

    def _consumer_channels(self) -> list[ChannelSchema]:
        """Return channels that have at least one consumer-annotated property."""
        return sorted(
            [
                ch
                for ch in self.registry.channels.values()
                if ch.scope != "all_apps"
                and any(p.consumer is not None for p in ch.properties.values())
            ],
            key=lambda c: c.address,
        )

    def _thing_block(self, channel: ChannelSchema) -> list[str]:
        app = channel.app_name or "unknown"
        device = _device_name_from_address(channel.address)
        thing_uid = _openhab_thing_uid(self.broker_uid, app, device)
        label = f"{app} {device}"

        lines = [
            f'Thing {thing_uid} "{label}" (mqtt:broker:{self.broker_uid}) {{',
            "    Channels:",
        ]

        for prop in sorted(channel.properties.values(), key=lambda p: p.name):
            if prop.consumer is None:
                continue
            prop_label = _escape_openhab_string(prop.consumer.display_name or prop.name)
            ch_type = _openhab_channel_type(prop)
            topic = channel.address
            jsonpath = f"JSONPATH:$.{prop.name}"

            if channel.direction in ("send", "both"):
                lines.append(
                    f'        Type {ch_type} : {_slugify(prop.name)} "{prop_label}" ['
                )
                lines.append(f'            stateTopic="{topic}",')
                lines.append(f'            transformationPattern="{jsonpath}"')
                lines.append("        ]")
            if channel.direction in ("receive", "both"):
                lines.append(
                    f"        Type {ch_type} : {_slugify(prop.name)}_cmd"
                    f' "{prop_label}" ['
                )
                lines.append(f'            commandTopic="{topic}",')
                lines.append(f'            transformationPattern="{jsonpath}"')
                lines.append("        ]")

        lines.append("}")
        lines.append("")
        return lines

    def _items_for_channel(self, channel: ChannelSchema) -> list[str]:
        app = channel.app_name or "unknown"
        device = _device_name_from_address(channel.address)
        group_name = f"g{app.replace('-', '_').title().replace('_', '')}"

        lines: list[str] = []
        for prop in sorted(channel.properties.values(), key=lambda p: p.name):
            if prop.consumer is None:
                continue
            lines.append(
                _format_item_line(self.broker_uid, app, device, prop, group_name)
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
