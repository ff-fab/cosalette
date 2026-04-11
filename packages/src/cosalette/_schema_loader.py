"""Schema loading and parsing for AsyncAPI 3.0.0 + x-cosalette-* extensions.

I/O module: loads AsyncAPI YAML, resolves $ref, validates extensions,
returns SchemaRegistry.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from cosalette._schema import (
    CapabilityRequirement,
    ChannelSchema,
    ConsumerMetadata,
    EnforcementConfig,
    HaDiscoveryOverrides,
    MqttBinding,
    OpenHabOverrides,
    OperationSchema,
    PropertySchema,
    SchemaRegistry,
    _extract_device_names,
)


@dataclass
class SchemaLoadError(Exception):
    """Raised when an AsyncAPI document cannot be loaded."""

    errors: list[str]
    source_description: str

    def __str__(self) -> str:
        header = f"Failed to load schema from {self.source_description}"
        if len(self.errors) == 1:
            return f"{header}: {self.errors[0]}"
        bullet_list = "\n".join(f"  - {e}" for e in self.errors)
        return f"{header} ({len(self.errors)} errors):\n{bullet_list}"


@runtime_checkable
class SchemaSource(Protocol):
    """Source for schema content."""

    async def load(self) -> str: ...

    @property
    def description(self) -> str: ...


@dataclass(frozen=True)
class FileSchemaSource:
    """Schema source from file."""

    path: Path

    async def load(self) -> str:
        def _read() -> str:
            return self.path.read_text(encoding="utf-8")

        return await asyncio.to_thread(_read)

    @property
    def description(self) -> str:
        return f"file://{self.path}"


@dataclass(frozen=True)
class InlineSchemaSource:
    """Schema source from inline content."""

    content: str

    async def load(self) -> str:
        return self.content

    @property
    def description(self) -> str:
        return "<inline>"


_schema_deps_checked = False


def _ensure_schema_deps() -> None:
    """Verify that optional schema dependencies are available."""
    global _schema_deps_checked  # noqa: PLW0603
    if _schema_deps_checked:
        return
    try:
        import jsonschema  # noqa: F401
        import yaml  # noqa: F401
    except ImportError as exc:
        msg = (
            "Schema support requires optional dependencies. "
            "Install with: pip install cosalette[schema]"
        )
        raise ImportError(msg) from exc
    _schema_deps_checked = True


def _follow_pointer(root: dict[str, Any], pointer: str) -> Any:
    """Navigate JSON Pointer like #/components/schemas/Foo."""
    if not pointer.startswith("#/"):
        raise ValueError(f"Invalid pointer: {pointer}")

    path = pointer[2:]  # Remove "#/"
    parts = path.split("/") if path else []

    current = root
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Pointer {pointer} not found")
        current = current[part]

    return current


_MAX_REF_DEPTH = 50


def _resolve_refs(
    doc: dict[str, Any],
    root: dict[str, Any],
    visited: frozenset[str] = frozenset(),
    *,
    _depth: int = 0,
) -> dict[str, Any]:
    """Resolve internal $ref recursively with circular detection."""
    if _depth > _MAX_REF_DEPTH:
        raise ValueError(f"Maximum $ref nesting depth ({_MAX_REF_DEPTH}) exceeded")

    if "$ref" in doc:
        ref = doc["$ref"]
        if ref in visited:
            raise ValueError(f"Circular reference detected: {ref}")

        try:
            resolved = _follow_pointer(root, ref)
            return _resolve_refs(resolved, root, visited | {ref}, _depth=_depth + 1)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Cannot resolve $ref {ref}: {exc}") from exc

    if isinstance(doc, dict):
        return {
            key: _resolve_refs(value, root, visited, _depth=_depth + 1)
            if isinstance(value, dict)
            else value
            for key, value in doc.items()
        }

    return doc


def _validate_enforcement(
    doc: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate x-cosalette-enforcement at document level."""
    if "x-cosalette-enforcement" not in doc:
        return
    enforcement = doc["x-cosalette-enforcement"]
    if not isinstance(enforcement, dict):
        errors.append("x-cosalette-enforcement must be a mapping")
        return
    mode = enforcement.get("mode")
    if mode and mode not in {"strict", "warn", "off"}:
        errors.append(
            "x-cosalette-enforcement.mode must be "
            f"'strict', 'warn', or 'off', got: {mode}"
        )


def _validate_requires(
    name: str,
    channel: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate x-cosalette-requires on a channel."""
    if "x-cosalette-requires" not in channel:
        return
    requires = channel["x-cosalette-requires"]
    if not isinstance(requires, list):
        errors.append(f"Channel {name}: x-cosalette-requires must be a list")
        return
    for i, req in enumerate(requires):
        if not isinstance(req, dict) or "tag" not in req:
            errors.append(
                f"Channel {name}: x-cosalette-requires[{i}] must have 'tag' field"
            )


def _validate_archetype(
    name: str,
    channel: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate x-cosalette-archetype on a channel."""
    archetype = channel.get("x-cosalette-archetype")
    if archetype is None:
        return
    if not isinstance(archetype, str):
        errors.append(f"Channel {name}: x-cosalette-archetype must be a string")
    elif archetype not in {"telemetry", "command", "device"}:
        errors.append(
            f"Channel {name}: "
            "x-cosalette-archetype must be "
            "'telemetry', 'command', or 'device'"
        )


def _validate_channel_extensions(
    name: str,
    channel: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate x-cosalette-* extensions on a single channel."""
    if not isinstance(channel, dict):
        return

    _validate_requires(name, channel, errors)
    _validate_archetype(name, channel, errors)

    cg = channel.get("x-cosalette-coalescing-group")
    if cg is not None and (not isinstance(cg, str) or not cg.strip()):
        errors.append(
            f"Channel {name}: x-cosalette-coalescing-group must be a non-empty string"
        )

    app = channel.get("x-cosalette-app")
    if app is not None and (not isinstance(app, str) or not app.strip()):
        errors.append(f"Channel {name}: x-cosalette-app must be a non-empty string")

    scope = channel.get("x-cosalette-scope")
    if scope is not None and not isinstance(scope, str):
        errors.append(f"Channel {name}: x-cosalette-scope must be a string")


def _validate_extensions(doc: dict[str, Any]) -> list[str]:
    """Validate all x-cosalette-* extensions."""
    errors: list[str] = []
    _validate_enforcement(doc, errors)
    for ch_name, ch_data in doc.get("channels", {}).items():
        _validate_channel_extensions(ch_name, ch_data, errors)
    return errors


def _build_enforcement_config(raw: dict[str, Any]) -> EnforcementConfig:
    """Build enforcement config from raw dict."""
    return EnforcementConfig(
        mode=raw.get("mode", "off"),
        on_configure=raw.get("on_configure", True),
        on_publish=raw.get("on_publish", False),
        network_level=raw.get("network_level", False),
    )


def _build_consumer_metadata(
    raw: dict[str, Any],
) -> ConsumerMetadata:
    """Build ConsumerMetadata from raw x-cosalette-consumer dict."""
    return ConsumerMetadata(
        device_class=raw.get("device_class"),
        unit=raw.get("unit"),
        display_name=raw.get("display_name"),
        icon=raw.get("icon"),
        state_class=raw.get("state_class"),
        read_only=raw.get("read_only", False),
    )


def _build_property_schema(
    name: str,
    prop_schema: dict[str, Any],
) -> PropertySchema:
    """Build PropertySchema with consumer metadata extraction."""
    consumer = None
    consumer_raw = prop_schema.get("x-cosalette-consumer")
    if isinstance(consumer_raw, dict):
        consumer = _build_consumer_metadata(consumer_raw)

    ha_discovery = None
    ha_raw = prop_schema.get("x-cosalette-ha-discovery")
    if isinstance(ha_raw, dict):
        ha_discovery = HaDiscoveryOverrides(
            component=ha_raw.get("component"),
            value_template=ha_raw.get("value_template"),
            command_template=ha_raw.get("command_template"),
            expire_after=ha_raw.get("expire_after"),
        )

    openhab = None
    openhab_raw = prop_schema.get("x-cosalette-openhab")
    if isinstance(openhab_raw, dict):
        openhab = OpenHabOverrides(
            item_type=openhab_raw.get("item_type"),
            label=openhab_raw.get("label"),
            groups=tuple(openhab_raw.get("groups", [])),
            tags=tuple(openhab_raw.get("tags", [])),
        )

    clean_schema = {
        k: v for k, v in prop_schema.items() if not k.startswith("x-cosalette-")
    }

    return PropertySchema(
        name=name,
        json_schema=clean_schema,
        consumer=consumer,
        ha_discovery=ha_discovery,
        openhab=openhab,
    )


def _extract_properties(
    payload_schema: dict[str, Any] | None,
) -> dict[str, PropertySchema]:
    """Extract PropertySchema objects from payload properties."""
    if not payload_schema or "properties" not in payload_schema:
        return {}
    return {
        name: _build_property_schema(name, schema)
        for name, schema in payload_schema["properties"].items()
    }


def _extract_channels(doc: dict[str, Any]) -> dict[str, ChannelSchema]:
    """Extract channels from AsyncAPI document."""
    channels = {}

    for channel_name, channel_data in doc.get("channels", {}).items():
        address = channel_data["address"]

        # Extract first message payload
        payload_schema = None
        message_name = None
        messages = channel_data.get("messages", {})
        if messages:
            first_message = next(iter(messages.values()))
            message_name = next(iter(messages.keys()))
            payload_schema = first_message.get("payload")

        # Build MQTT binding
        mqtt_raw = channel_data.get("bindings", {}).get("mqtt", {})
        mqtt_binding = MqttBinding(
            qos=mqtt_raw.get("qos", 1),
            retain=mqtt_raw.get("retain", False),
        )

        # Build capability requirements
        cap_reqs = tuple(
            CapabilityRequirement(
                tag=r["tag"],
                description=r.get("description"),
            )
            for r in channel_data.get("x-cosalette-requires", [])
        )

        channels[channel_name] = ChannelSchema(
            address=address,
            address_template=address,
            direction="send",
            payload_schema=payload_schema,
            mqtt_binding=mqtt_binding,
            capability_requirements=cap_reqs,
            archetype=channel_data.get("x-cosalette-archetype"),
            coalescing_group=channel_data.get("x-cosalette-coalescing-group"),
            message_name=message_name,
            app_name=channel_data.get("x-cosalette-app"),
            scope=channel_data.get("x-cosalette-scope"),
            properties=_extract_properties(payload_schema),
        )

    return channels


def _extract_operations_raw(
    doc: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Extract raw operations before ref resolution."""
    result: dict[str, dict[str, Any]] = doc.get("operations", {})
    return result


def _build_operations_from_raw(
    operations_raw: dict[str, dict[str, Any]],
    channels: dict[str, ChannelSchema],
) -> dict[str, OperationSchema]:
    """Build operations from raw data and resolved channels."""
    operations: dict[str, OperationSchema] = {}

    for op_name, op_data in operations_raw.items():
        channel_ref_raw = op_data.get("channel", {}).get("$ref", "")
        channel_ref = channel_ref_raw.split("/")[-1] if channel_ref_raw else ""

        mqtt_raw = op_data.get("bindings", {}).get("mqtt", {})
        fallback = channels.get(channel_ref)
        default_binding = fallback.mqtt_binding if fallback else MqttBinding()
        mqtt_binding = MqttBinding(
            qos=mqtt_raw.get("qos", default_binding.qos),
            retain=mqtt_raw.get("retain", default_binding.retain),
        )

        operations[op_name] = OperationSchema(
            action=op_data["action"],
            channel_ref=channel_ref,
            archetype=op_data.get("x-cosalette-archetype"),
            coalescing_group=op_data.get("x-cosalette-coalescing-group"),
            mqtt_binding=mqtt_binding,
        )

    return operations


def _infer_channel_directions(
    channels: dict[str, ChannelSchema],
    operations: dict[str, OperationSchema],
) -> dict[str, ChannelSchema]:
    """Infer direction for channels based on operations."""
    channel_actions: dict[str, set[str]] = {}

    # Group operations by channel
    for operation in operations.values():
        channel_ref = operation.channel_ref
        if channel_ref not in channel_actions:
            channel_actions[channel_ref] = set()
        channel_actions[channel_ref].add(operation.action)

    # Update channel directions
    updated_channels = {}
    for name, channel in channels.items():
        actions = channel_actions.get(name, set())

        if actions == {"send"}:
            direction: Literal["send", "receive", "both"] = "send"
        elif actions == {"receive"}:
            direction = "receive"
        elif actions == {"send", "receive"}:
            direction = "both"
        else:
            direction = "send"

        updated_channels[name] = replace(channel, direction=direction)

    return updated_channels


async def load_schema(source: SchemaSource) -> SchemaRegistry:
    """Load and parse AsyncAPI schema from source."""
    _ensure_schema_deps()

    import yaml

    try:
        # Load YAML content
        content = await source.load()
        doc = yaml.safe_load(content)
    except Exception as exc:
        raise SchemaLoadError(
            errors=[f"Failed to parse YAML: {exc}"],
            source_description=source.description,
        ) from exc

    if not isinstance(doc, dict):
        raise SchemaLoadError(
            errors=["Schema must be a YAML mapping, got: " + type(doc).__name__],
            source_description=source.description,
        )

    errors = []

    # Validate AsyncAPI version
    asyncapi_version = doc.get("asyncapi", "")
    if not asyncapi_version.startswith("3.0."):
        errors.append(
            f"Unsupported AsyncAPI version: {asyncapi_version}. Expected 3.0.x"
        )

    # Validate extensions BEFORE resolving refs
    extension_errors = _validate_extensions(doc)
    errors.extend(extension_errors)

    if errors:
        raise SchemaLoadError(errors=errors, source_description=source.description)

    # Extract operations BEFORE resolving refs (so $ref is still intact)
    operations_raw = _extract_operations_raw(doc)

    try:
        # Resolve $ref (internal only)
        doc = _resolve_refs(doc, doc)
    except ValueError as exc:
        errors.append(str(exc))
        raise SchemaLoadError(
            errors=errors,
            source_description=source.description,
        ) from exc

    # Extract enforcement config
    enforcement_raw = doc.get("x-cosalette-enforcement", {})
    enforcement = _build_enforcement_config(enforcement_raw)

    # Extract channels AFTER resolving refs
    channels = _extract_channels(doc)

    # Build operations using the raw refs and resolved channels
    operations = _build_operations_from_raw(operations_raw, channels)

    # Infer channel directions from operations
    channels = _infer_channel_directions(channels, operations)

    # Extract component schemas
    component_schemas: dict[str, dict[str, Any]] = doc.get("components", {}).get(
        "schemas", {}
    )

    # Extract device names
    device_names = _extract_device_names(channels)

    # Determine app_name
    app_name = doc.get("info", {}).get("title")
    if enforcement.network_level:
        app_name = None

    return SchemaRegistry(
        app_name=app_name,
        app_version=doc.get("info", {}).get("version", "0.0.0"),
        asyncapi_version=asyncapi_version,
        enforcement=enforcement,
        channels=channels,
        operations=operations,
        component_schemas=component_schemas,
        device_names=device_names,
    )


def load_schema_sync(source: SchemaSource) -> SchemaRegistry:
    """Synchronous wrapper around :func:`load_schema` for CLI contexts.

    Intended for CLI commands (``cosalette schema …``) where no event
    loop is running.  Calls :func:`asyncio.run` internally — do **not**
    call from within an existing async context.

    Raises:
        SchemaLoadError: When the schema document is invalid.
        ImportError: When optional ``[schema]`` dependencies are missing.
        RuntimeError: When called from within a running event loop.
    """
    return asyncio.run(load_schema(source))
