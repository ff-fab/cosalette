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


type Aggregate = Literal["count", "min", "max", "avg", "sum"]
"""A typed value aggregate for an array-valued property (ADR-076).

``count`` yields the element count and is valid on any array; ``min``/``max``/
``avg``/``sum`` reduce an array of numbers to one number. The declaration names
no target -- each generator renders it in its own vocabulary.
"""


@dataclass(frozen=True, slots=True)
class ConsumerMetadata:
    """Generic consumer metadata from x-cosalette-consumer."""

    device_class: str | None = None
    unit: str | None = None
    display_name: str | None = None
    icon: str | None = None
    state_class: str | None = None
    read_only: bool = False
    aggregate: str | None = None


X_COSALETTE_CONSUMER = "x-cosalette-consumer"
"""Schema extension key carrying HA/OpenHAB consumer discovery metadata."""

X_COSALETTE_TOPIC_PREFIX = "x-cosalette-topic-prefix"
"""Info-level extension key carrying the resolved MQTT topic prefix (ADR-072).

Additive and optional: it is emitted only when the prefix differs from
``info.title`` (the app name), so documents for unprefixed apps are unchanged.
Readers fall back to ``info.title`` when the key is absent, which reproduces
the pre-ADR-072 behaviour exactly.
"""

TOPIC_PREFIX_SAFE_RE = re.compile(r"^[A-Za-z0-9_./:-]*$")
"""Characters a topic prefix may contain (ADR-072).

A prefix is interpolated into both MQTT topics and generated broker ACL files
(``_schema/_acl.py``), so it must stay within the character set the ACL layer
can emit safely: spaces, quotes, control bytes (CWE-117) and non-ASCII would
either break ACL-file tokenisation or forge log records.  Wildcards (``+``/
``#``) are excluded here and rejected separately with a specific message.  The
runtime mirror lives in ``MqttSettings`` (``_settings/__init__.py``).
"""


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
    aggregate: Aggregate


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

X_COSALETTE_DISCOVERABLE = "x-cosalette-discoverable"
"""Channel-level extension marking a channel as (non-)consumer-visible (ADR-073).

Author-controlled via ``discoverable=`` on ``@app.telemetry``/``@app.command``/
``@app.device``. Emitted onto the generated channel dict **only when False**, so
documents for the default ``discoverable=True`` case stay byte-identical to
pre-ADR-073 output; readers default a missing key to ``True``. A ``False``
channel is excluded from Home Assistant / openHAB consumer generation and does
not trip the per-channel discovery gate — the supported way to declare a channel
intentionally not a consumer entity.
"""


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


@dataclass(frozen=True, slots=True)
class HaEntitySpec:
    """One composite HA entity built from a channel's whole payload model.

    Populated from ``x-cosalette-ha-discovery.entities[]`` at the payload
    schema's top level (model-level ``json_schema_extra``), distinct from the
    per-property :class:`HaDiscoveryOverrides` block of the same extension key.
    """

    component: str
    name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class HaEntityMeta(TypedDict, total=False):
    """Valid keys for one entry in ``ha_entities(...)``.

    Keys mirror the fields of :class:`HaEntitySpec` (the reader side); a
    drift-guard test asserts this parity. ``extra`` is an open passthrough —
    most of a composite entity's keys (``schema``, ``brightness``,
    ``supported_color_modes``, ...) are native Home Assistant vocabulary with
    no cosalette-side semantics to curate.
    """

    component: str
    name: str
    extra: dict[str, Any]


def ha_entity(**metadata: Unpack[HaEntityMeta]) -> dict[str, Any]:
    """Build one composite HA entity spec for :func:`ha_entities`.

    Not a ``json_schema_extra``-ready block by itself — pass the result(s) to
    :func:`ha_entities`, which wraps them under the
    ``x-cosalette-ha-discovery`` key for a model's ``ConfigDict``.

    Note:
        String values are subject to the same invisible-character guard as
        :func:`consumer` — see that function's note for details.
    """
    for key, value in metadata.items():
        if isinstance(value, str) and any(
            unicodedata.category(c) in ("Cc", "Cf") for c in value
        ):
            raise ValueError(
                f"ha_entity() value for {key!r} contains invisible or bidirectional "
                "Unicode characters (category Cc/Cf); use only printable content"
            )
    return dict(metadata)


def ha_entities(*entities: dict[str, Any]) -> dict[str, Any]:
    """Wrap one or more :func:`ha_entity` specs for a model's ``ConfigDict``.

    Ready to pass to pydantic ``ConfigDict(json_schema_extra=...)`` on the
    payload model registered for a channel — composite entities are declared
    at the model level, not the field level, so a single entity can span
    every property the model carries. See ADR-057.
    """
    return {X_COSALETTE_HA_DISCOVERY: {"entities": list(entities)}}


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
    path: tuple[str, ...] = ()  # structural accessor segments, e.g. ("meta", "source")
    is_array_item: bool = False  # child of an array's items; has no single value


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
    ha_entities: tuple[HaEntitySpec, ...] = ()
    discoverable: bool = True
    """Whether this channel is exposed to consumer generation (ADR-073).

    ``False`` when the author registered it with ``discoverable=False`` (emitted
    as ``x-cosalette-discoverable: false``). Defaults to ``True`` so documents
    without the key — every one generated before ADR-073 — behave unchanged.
    """


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
    unreachable_consumer_channels: frozenset[str] = frozenset()
    topic_prefix: str | None = None
    """Resolved MQTT topic prefix from ``info.x-cosalette-topic-prefix`` (ADR-072).

    ``None`` when the document omits the key — every document generated before
    ADR-072, and every document whose prefix equals its app name.  Use
    :attr:`resolved_topic_prefix` to apply the documented ``or app_name``
    fallback; the raw ``None`` is kept distinguishable so address parsing can
    stay at its pre-ADR-072 one-segment assumption when nothing says otherwise.
    """

    @property
    def resolved_topic_prefix(self) -> str | None:
        """The effective topic prefix: the explicit extension, else the app name.

        Mirrors the runtime's ``settings.mqtt.topic_prefix or app.name``
        resolution for consumers reading a *serialised* document, which have no
        ``App`` object to fall back on (ADR-072).  ``None`` only when neither is
        known — a network-level document, whose channels span several apps.
        """
        return self.topic_prefix or self.app_name

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

        filtered_device_names = _extract_device_names(
            filtered_channels, self.topic_prefix
        )

        return SchemaRegistry(
            app_name=app_name,
            app_version=self.app_version,
            asyncapi_version=self.asyncapi_version,
            enforcement=self.enforcement,
            channels=filtered_channels,
            operations=filtered_operations,
            component_schemas=self.component_schemas,
            device_names=filtered_device_names,
            unreachable_consumer_channels=self.unreachable_consumer_channels
            & filtered_channels.keys(),
            topic_prefix=self.topic_prefix,
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


def _prefix_depth(topic_prefix: str | None) -> int:
    """Return how many leading address segments *topic_prefix* occupies.

    ADR-072: ``mqtt.topic_prefix`` may be multi-segment (``house/wiz``), so the
    number of segments to strip is a property of the prefix, not a constant.
    An unknown (``None``) or empty prefix falls back to ``1`` — the pre-ADR-072
    assumption, which is exactly right for an ``App(name=...)``-derived prefix
    since app names may not contain ``/``.
    """
    if not topic_prefix:
        return 1
    return len(topic_prefix.split("/"))


def _device_name_from_archetype(
    channel: ChannelSchema, topic_prefix: str | None = None
) -> str | None:
    """Extract device name from a channel with an archetype but no template params.

    Relies on the ADR-002 topic structure: ``{prefix}/{device…}/{signal}``, where
    ``{prefix}`` may itself span several segments (ADR-072) — so the leading
    ``len(topic_prefix.split("/"))`` segments are dropped, not exactly one.
    Device names are therefore identical for ``wiz2mqtt/desk/state`` and
    ``house/wiz/desk/state``, which is what keeps Home Assistant ``object_id`` /
    ``unique_id`` stable when a prefix is introduced.

    Returns ``None`` when the address is too short to carry both a prefix and a
    device segment — an archetype channel needs at least
    ``{prefix…}/device/suffix``.  Such addresses are root-level (ADR-058) or
    malformed; either way they name no device.
    """
    parts = channel.address.split("/")
    depth = _prefix_depth(topic_prefix)
    if len(parts) < depth + 2:
        return None
    # Standard: prefix…/device/suffix  →  "device"
    # Nested:   prefix…/device/sub/suffix  →  "device/sub"
    return "/".join(parts[depth:-1])


def _extract_device_names(
    channels: dict[str, ChannelSchema], topic_prefix: str | None = None
) -> frozenset[str]:
    """Extract device names from channel address templates.

    *topic_prefix* is the document's resolved MQTT prefix (ADR-072); ``None``
    keeps the pre-ADR-072 single-leading-segment assumption.
    """
    device_names: set[str] = set()

    for channel in channels.values():
        if "{deviceName}" in channel.address_template:
            name = _device_name_from_template(channel)
            if name:
                device_names.add(name)
        elif channel.archetype and "{" not in channel.address_template:
            name = _device_name_from_archetype(channel, topic_prefix)
            if name:
                device_names.add(name)

    return frozenset(device_names)
