"""Schema data model for AsyncAPI 3.0.0 + x-cosalette-* extensions.

Frozen dataclasses representing parsed schema documents. No I/O —
loading is handled by :mod:`cosalette._schema._loader`.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, Unpack


@dataclass(frozen=True, slots=True)
class EnforcementConfig:
    """Document-level enforcement from x-cosalette-enforcement."""

    mode: Literal["strict", "warn", "off"] = "off"
    on_configure: bool = True
    on_publish: bool = False
    network_level: bool = False


@dataclass(frozen=True, slots=True)
class MqttBinding:
    """MQTT binding properties from bindings.mqtt."""

    qos: int = 1
    retain: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """Tag-based capability from x-cosalette-requires."""

    tag: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ConsumerMetadata:
    """Generic consumer metadata from x-cosalette-consumer."""

    device_class: str | None = None
    unit: str | None = None
    display_name: str | None = None
    icon: str | None = None
    state_class: str | None = None
    read_only: bool = False


X_COSALETTE_CONSUMER = "x-cosalette-consumer"
"""Schema extension key carrying HA/OpenHAB consumer discovery metadata."""


class ConsumerMeta(TypedDict, total=False):
    """Valid Home Assistant / OpenHAB discovery keys for x-cosalette-consumer.

    Keys mirror the fields of :class:`ConsumerMetadata` (the reader side); a
    drift-guard test asserts this parity. Keys-only typing — no value-enum
    validation is performed here.
    """

    display_name: str
    device_class: str
    unit: str
    state_class: str
    icon: str
    read_only: bool


def consumer(**metadata: Unpack[ConsumerMeta]) -> dict[str, Any]:
    """Wrap HA/OpenHAB discovery metadata under the x-cosalette-consumer key.

    Ready to pass to pydantic ``Field(json_schema_extra=...)``. The key set is
    the single source of truth shared with the :class:`ConsumerMetadata` reader.

    Note:
        These values are emitted verbatim (unescaped, including any non-ASCII) into
        the generated schema/docs artifacts and downstream consumer configs (HA
        discovery, OpenHAB). Keep them to trusted, printable content — do not embed
        untrusted input or invisible/bidirectional Unicode control characters.
    """
    for key, value in metadata.items():
        if isinstance(value, str) and any(
            unicodedata.category(c) in ("Cc", "Cf") for c in value
        ):
            raise ValueError(
                f"consumer() value for {key!r} contains invisible or bidirectional "
                "Unicode characters (category Cc/Cf); use only printable content"
            )
    return {X_COSALETTE_CONSUMER: dict(metadata)}


def temperature(display_name: str) -> dict[str, Any]:
    """``x-cosalette-consumer`` for a standard °C measurement sensor.

    Collapses the ``device_class="temperature"``, ``unit="°C"``,
    ``state_class="measurement"`` triple shared by the many temperature
    fields, where only the ``display_name`` varies.
    """
    return consumer(
        display_name=display_name,
        device_class="temperature",
        unit="°C",
        state_class="measurement",
    )


def percent(display_name: str, *, icon: str | None = None) -> dict[str, Any]:
    """``x-cosalette-consumer`` for a percentage measurement sensor.

    Shared by the modulation / pump-speed / power fields (``unit="%"``,
    ``state_class="measurement"``). ``icon`` is optional and omitted from the
    emitted metadata when not supplied, so output matches a hand-written block
    exactly.
    """
    if icon is None:
        return consumer(display_name=display_name, unit="%", state_class="measurement")
    return consumer(
        display_name=display_name,
        unit="%",
        state_class="measurement",
        icon=icon,
    )


@dataclass(frozen=True, slots=True)
class HaDiscoveryOverrides:
    """HA-specific overrides from x-cosalette-ha-discovery."""

    component: str | None = None
    value_template: str | None = None
    command_template: str | None = None
    expire_after: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OpenHabOverrides:
    """OpenHAB-specific from x-cosalette-openhab."""

    item_type: str | None = None
    label: str | None = None
    groups: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    channel_type: str | None = None
    channel_params: dict[str, Any] = field(default_factory=dict)


X_COSALETTE_HA_DISCOVERY = "x-cosalette-ha-discovery"
"""Schema extension key carrying Home Assistant-specific discovery overrides."""

X_COSALETTE_OPENHAB = "x-cosalette-openhab"
"""Schema extension key carrying OpenHAB-specific discovery overrides."""


class HaDiscoveryMeta(TypedDict, total=False):
    """Valid keys for x-cosalette-ha-discovery.

    Keys mirror the fields of :class:`HaDiscoveryOverrides` (the reader side); a
    drift-guard test asserts this parity. ``extra`` is an open passthrough —
    unlike the other keys it is not itself typed, since it exists precisely to
    reach Home Assistant MQTT discovery keys the curated fields do not cover.
    """

    component: str
    value_template: str
    command_template: str
    expire_after: int
    extra: dict[str, Any]


class OpenHabMeta(TypedDict, total=False):
    """Valid keys for x-cosalette-openhab.

    Keys mirror the fields of :class:`OpenHabOverrides` (the reader side); a
    drift-guard test asserts this parity. ``channel_params`` is an open
    passthrough for openHAB Thing channel parameters (``on``/``off``,
    ``min``/``max``/``step``, ``colorMode``, ...) the curated fields do not
    cover.
    """

    item_type: str
    label: str
    groups: list[str]
    tags: list[str]
    channel_type: str
    channel_params: dict[str, Any]


def ha_discovery(**metadata: Unpack[HaDiscoveryMeta]) -> dict[str, Any]:
    """Wrap Home Assistant discovery overrides under the x-cosalette-ha-discovery key.

    Ready to pass to pydantic ``Field(json_schema_extra=...)``, alone or combined
    with :func:`consumer`/:func:`openhab` via :func:`merge`. The key set is the
    single source of truth shared with the :class:`HaDiscoveryOverrides` reader.

    Note:
        String values are subject to the same invisible-character guard as
        :func:`consumer` — see that function's note for details.
    """
    for key, value in metadata.items():
        if isinstance(value, str) and any(
            unicodedata.category(c) in ("Cc", "Cf") for c in value
        ):
            raise ValueError(
                f"ha_discovery() value for {key!r} contains invisible or bidirectional "
                "Unicode characters (category Cc/Cf); use only printable content"
            )
    return {X_COSALETTE_HA_DISCOVERY: dict(metadata)}


def openhab(**metadata: Unpack[OpenHabMeta]) -> dict[str, Any]:
    """Wrap OpenHAB overrides under the x-cosalette-openhab key.

    Ready to pass to pydantic ``Field(json_schema_extra=...)``, alone or combined
    with :func:`consumer`/:func:`ha_discovery` via :func:`merge`. The key set is
    the single source of truth shared with the :class:`OpenHabOverrides` reader.

    Note:
        String values are subject to the same invisible-character guard as
        :func:`consumer` — see that function's note for details.
    """
    for key, value in metadata.items():
        if isinstance(value, str) and any(
            unicodedata.category(c) in ("Cc", "Cf") for c in value
        ):
            raise ValueError(
                f"openhab() value for {key!r} contains invisible or bidirectional "
                "Unicode characters (category Cc/Cf); use only printable content"
            )
    return {X_COSALETTE_OPENHAB: dict(metadata)}


def merge(*blocks: dict[str, Any]) -> dict[str, Any]:
    """Fold multiple producer outputs into one ``json_schema_extra`` dict.

    ``consumer()``, ``ha_discovery()`` and ``openhab()`` each return a
    single-key dict; pydantic's ``Field(json_schema_extra=...)`` accepts only
    one dict per field, so combining them requires a shallow merge over their
    top-level extension keys.

    Raises:
        ValueError: If two blocks carry the same extension key — merge() folds
            distinct producer outputs together, it does not decide precedence
            between two calls to the same producer.
    """
    result: dict[str, Any] = {}
    for block in blocks:
        for key, value in block.items():
            if key in result:
                msg = f"merge() received duplicate extension key: {key!r}"
                raise ValueError(msg)
            result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class PropertySchema:
    """Single property in a payload schema."""

    name: str
    json_schema: dict[str, Any]
    consumer: ConsumerMetadata | None = None
    ha_discovery: HaDiscoveryOverrides | None = None
    openhab: OpenHabOverrides | None = None


@dataclass(frozen=True, slots=True)
class ChannelSchema:
    """Parsed AsyncAPI channel."""

    address: str
    address_template: str
    direction: Literal["send", "receive", "both"]
    payload_schema: dict[str, Any] | None = None
    mqtt_binding: MqttBinding = field(default_factory=MqttBinding)
    capability_requirements: tuple[CapabilityRequirement, ...] = ()
    archetype: Literal["telemetry", "command", "device", "stream"] | None = None
    coalescing_group: str | None = None
    message_name: str | None = None
    app_name: str | None = None
    scope: str | None = None
    properties: dict[str, PropertySchema] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OperationSchema:
    """Parsed AsyncAPI operation."""

    action: Literal["send", "receive"]
    channel_ref: str
    archetype: Literal["telemetry", "command", "device", "stream"] | None = None
    coalescing_group: str | None = None
    mqtt_binding: MqttBinding = field(default_factory=MqttBinding)


@dataclass(frozen=True, slots=True)
class SchemaRegistry:
    """Top-level container."""

    app_name: str | None
    app_version: str
    asyncapi_version: str
    enforcement: EnforcementConfig
    channels: dict[str, ChannelSchema]
    operations: dict[str, OperationSchema]
    component_schemas: dict[str, dict[str, Any]]
    device_names: frozenset[str]

    def filter_for_app(self, app_name: str) -> SchemaRegistry:
        """Filter channels where ch.app_name == app_name or ch.scope == "all_apps".

        Returns a new registry with filtered channels and matching operations.
        """
        filtered_channels = {
            name: channel
            for name, channel in self.channels.items()
            if channel.app_name == app_name or channel.scope == "all_apps"
        }

        filtered_operations = {
            name: op
            for name, op in self.operations.items()
            if op.channel_ref in filtered_channels
        }

        filtered_device_names = _extract_device_names(filtered_channels)

        return SchemaRegistry(
            app_name=app_name,
            app_version=self.app_version,
            asyncapi_version=self.asyncapi_version,
            enforcement=self.enforcement,
            channels=filtered_channels,
            operations=filtered_operations,
            component_schemas=self.component_schemas,
            device_names=filtered_device_names,
        )

    def all_app_names(self) -> frozenset[str]:
        """Return all unique app_name values from channels."""
        app_names = {
            channel.app_name
            for channel in self.channels.values()
            if channel.app_name is not None
        }
        return frozenset(app_names)

    def channels_for_device(self, device_name: str) -> list[ChannelSchema]:
        """Find channels whose address template contains {deviceName}.

        Also includes channels whose concrete address contains device_name.
        """
        result = []
        for channel in self.channels.values():
            if (
                "{deviceName}" in channel.address_template
                or device_name in channel.address.split("/")
            ):
                result.append(channel)
        return result

    def required_channels_for_tag(self, tag: str) -> list[ChannelSchema]:
        """Find channels with matching capability requirement tag."""
        result = []
        for channel in self.channels.values():
            for req in channel.capability_requirements:
                if req.tag == tag:
                    result.append(channel)
                    break
        return result

    def payload_schema_for_topic(self, resolved_topic: str) -> dict[str, Any] | None:
        """Look up JSON Schema for a resolved topic."""
        for channel in self.channels.values():
            if _topic_matches(channel.address_template, resolved_topic):
                return channel.payload_schema
            if channel.address == resolved_topic:
                return channel.payload_schema
        return None


def _topic_matches(template: str, topic: str) -> bool:
    """Check whether topic matches an address template."""
    escaped = re.escape(template)
    pattern = re.sub(r"\\\{[^}]+\\\}", "[^/]+", escaped)
    return re.fullmatch(pattern, topic) is not None


def _device_name_from_template(channel: ChannelSchema) -> str | None:
    """Extract device name from a channel using {deviceName} in its template."""
    template_parts = channel.address_template.split("/")
    address_parts = channel.address.split("/")

    if len(template_parts) != len(address_parts):
        return None

    for template_part, address_part in zip(template_parts, address_parts, strict=True):
        if template_part == "{deviceName}":
            return address_part
    return None


def _device_name_from_archetype(channel: ChannelSchema) -> str | None:
    """Extract device name from a channel with an archetype but no template params.

    Relies on the ADR-002 topic structure: ``{app}/{device…}/{signal}``.
    Returns ``None`` for fewer than 3 segments — archetype channels require at
    least ``app/device/suffix`` (3 parts).  A 2-segment address is treated as
    malformed and returns ``None`` (changed from the prior behaviour of returning
    ``parts[1]``).
    """
    parts = channel.address.split("/")
    if len(parts) == 3:
        # Standard: app/device/suffix  →  "device"
        return parts[1]
    if len(parts) > 3:
        # Nested: app/device/sub/suffix  →  "device/sub"
        return "/".join(parts[1:-1])
    return None


def _extract_device_names(channels: dict[str, ChannelSchema]) -> frozenset[str]:
    """Extract device names from channel address templates."""
    device_names: set[str] = set()

    for channel in channels.values():
        if "{deviceName}" in channel.address_template:
            name = _device_name_from_template(channel)
            if name:
                device_names.add(name)
        elif channel.archetype and "{" not in channel.address_template:
            name = _device_name_from_archetype(channel)
            if name:
                device_names.add(name)

    return frozenset(device_names)
