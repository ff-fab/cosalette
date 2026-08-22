"""Helpers for parsing and building schema objects from AsyncAPI documents."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Literal

from cosalette._schema import (
    X_COSALETTE_CONSUMER,
    X_COSALETTE_HA_DISCOVERY,
    X_COSALETTE_OPENHAB,
    CapabilityRequirement,
    ChannelSchema,
    ConsumerMetadata,
    EnforcementConfig,
    HaDiscoveryOverrides,
    HaEntitySpec,
    MqttBinding,
    OpenHabOverrides,
    OperationSchema,
    PropertySchema,
)

# ---------------------------------------------------------------------------
# Extension validation
# ---------------------------------------------------------------------------


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
    elif archetype not in {"telemetry", "command", "device", "stream"}:
        errors.append(
            f"Channel {name}: "
            "x-cosalette-archetype must be "
            "'telemetry', 'command', 'device', or 'stream'"
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


# ---------------------------------------------------------------------------
# Object builders
# ---------------------------------------------------------------------------


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


def _coerce_dict_field(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """Return raw[key] as a dict, treating None/absent as {}."""
    val = raw.get(key)
    return dict(val) if isinstance(val, dict) else {}


def _coerce_tuple_field(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    """Return raw[key] as a tuple of strings, treating None/absent as ()."""
    val = raw.get(key)
    return tuple(val) if isinstance(val, (list, tuple)) else ()


def _build_property_schema(
    name: str,
    prop_schema: dict[str, Any],
    *,
    path: tuple[str, ...] | None = None,
    is_array_item: bool = False,
) -> PropertySchema:
    """Build PropertySchema with consumer metadata extraction."""
    consumer = None
    consumer_raw = prop_schema.get(X_COSALETTE_CONSUMER)
    if isinstance(consumer_raw, dict):
        built = _build_consumer_metadata(consumer_raw)
        # An empty / all-default consumer block (e.g. from consumer() with no
        # args) carries no discovery information. Treat it as absent so the
        # generators' `prop.consumer is None` guard skips it instead of emitting
        # a degenerate, name-only discovery entity.
        if built != ConsumerMetadata():
            consumer = built

    ha_discovery = None
    ha_raw = prop_schema.get(X_COSALETTE_HA_DISCOVERY)
    if isinstance(ha_raw, dict):
        ha_discovery = HaDiscoveryOverrides(
            component=ha_raw.get("component"),
            value_template=ha_raw.get("value_template"),
            command_template=ha_raw.get("command_template"),
            expire_after=ha_raw.get("expire_after"),
            extra=_coerce_dict_field(ha_raw, "extra"),
        )

    openhab = None
    openhab_raw = prop_schema.get(X_COSALETTE_OPENHAB)
    if isinstance(openhab_raw, dict):
        openhab = OpenHabOverrides(
            item_type=openhab_raw.get("item_type"),
            label=openhab_raw.get("label"),
            groups=_coerce_tuple_field(openhab_raw, "groups"),
            tags=_coerce_tuple_field(openhab_raw, "tags"),
            channel_type=openhab_raw.get("channel_type"),
            channel_params=_coerce_dict_field(openhab_raw, "channel_params"),
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
        path=path if path is not None else (name,),
        is_array_item=is_array_item,
    )


# Valid HA component: lowercase letters/digits/underscores (e.g. "binary_sensor").
# Rejects MQTT wildcards (+, #), slashes, control characters, and whitespace.
_VALID_COMPONENT_RE = re.compile(r"[a-z][a-z0-9_]*")


def _build_ha_entity_specs(
    payload_schema: dict[str, Any] | None,
) -> tuple[HaEntitySpec, ...]:
    """Build composite HaEntitySpecs from a payload schema's model-level extension.

    Reads ``x-cosalette-ha-discovery.entities`` at the top of *payload_schema*
    (populated by pydantic's model-level ``json_schema_extra``, see
    :func:`cosalette.schema.ha_entities`) — distinct from the per-property
    override block of the same extension key read in
    :func:`_build_property_schema`. Entries missing a valid ``component`` are
    skipped rather than raising, matching the loader's tolerant style
    elsewhere (ADR-057). ``component`` must match ``[a-z][a-z0-9_]*`` so it
    is safe for MQTT topic interpolation; whitespace-only strings are also
    rejected.  Non-string ``name`` values are coerced to ``None``.
    """
    if not payload_schema:
        return ()
    raw = payload_schema.get(X_COSALETTE_HA_DISCOVERY)
    if not isinstance(raw, dict):
        return ()
    entities_raw = raw.get("entities")
    if not isinstance(entities_raw, list):
        return ()
    specs: list[HaEntitySpec] = []
    for entity in entities_raw:
        if not isinstance(entity, dict):
            continue
        component = entity.get("component")
        if not isinstance(component, str) or not _VALID_COMPONENT_RE.fullmatch(
            component.strip()
        ):
            continue
        name_raw = entity.get("name")
        name: str | None = name_raw if isinstance(name_raw, str) else None
        specs.append(
            HaEntitySpec(
                component=component,
                name=name,
                extra=_coerce_dict_field(entity, "extra"),
            )
        )
    return tuple(specs)


_MAX_COMPOSITION_DEPTH = 64
"""Maximum recursion depth for ``_collect_properties``.

Satisfies all real-world JSON Schema composition depth needs while
guarding against pathologically deep or malformed user-supplied schemas.
"""


def _collect_properties(
    schema: dict[str, Any] | None,
    *,
    _depth: int = 0,
) -> dict[str, dict[str, Any]]:
    """Recursively gather ``properties`` maps across oneOf/anyOf/allOf variants.

    Merges direct top-level ``properties`` and any ``properties`` found inside
    ``oneOf`` / ``anyOf`` / ``allOf`` variants.  First-writer wins on a name
    collision: callers that need a specific variant to take precedence should
    order variants accordingly.

    Purely structural variants without ``properties`` (e.g. ``{type: null}``)
    contribute nothing and are silently skipped.

    Raises:
        ValueError: If composition nesting exceeds ``_MAX_COMPOSITION_DEPTH``.
    """
    if not schema:
        return {}
    if _depth > _MAX_COMPOSITION_DEPTH:
        msg = (
            f"Schema composition nesting exceeds maximum depth "
            f"({_MAX_COMPOSITION_DEPTH}). Check the schema for pathological "
            f"nesting or circular composition."
        )
        raise ValueError(msg)
    # Seed with any direct properties first — they take precedence.
    merged: dict[str, dict[str, Any]] = dict(schema.get("properties", {}))
    for keyword in ("oneOf", "anyOf", "allOf"):
        for variant in schema.get(keyword, ()):
            if isinstance(variant, dict):
                for name, prop in _collect_properties(
                    variant, _depth=_depth + 1
                ).items():
                    merged.setdefault(name, prop)
    return merged


def _expand_property_children(
    name: str,
    prop_schema: dict[str, Any],
) -> list[tuple[str, dict[str, Any], tuple[str, ...], bool]]:
    """Return child ``(flattened_name, sub_schema, path, is_array_item)`` tuples
    for one level of nested/array descent.

    An array property whose ``items`` is an object schema contributes the
    items' properties as ``{name}[].{sub}`` with ``is_array_item=True``.
    Any other schema that carries ``properties`` or composition keywords
    (``oneOf``/``anyOf``/``allOf``) contributes them as ``{name}.{sub}``
    with ``is_array_item=False``.  Tuple-form ``items`` (a list), scalar
    properties, and bare array schemas produce no child entries.

    ``path`` is the structural accessor segment tuple ``(name, sub_name)``
    used by consumer generators to build correct JSONPath / Jinja accessors
    instead of treating the flattened label as a literal key.

    By this point ``$ref`` has already been resolved document-wide (see
    :func:`cosalette._schema._loader._resolve_refs`), so *prop_schema* and
    ``items_schema`` are plain inline schemas.

    Only one level is descended: an array of arrays or an object three
    levels deep is not flattened (Finding 16 scope).
    """
    children: list[tuple[str, dict[str, Any], tuple[str, ...], bool]] = []
    items_schema = prop_schema.get("items")
    if isinstance(items_schema, dict):
        for sub_name, sub_schema in _collect_properties(items_schema).items():
            children.append(
                (f"{name}[].{sub_name}", sub_schema, (name, sub_name), True)
            )
    elif not isinstance(items_schema, list) and any(
        # Non-array or array-without-items: descend only if object-shaped.
        k in prop_schema
        for k in ("properties", "oneOf", "anyOf", "allOf")
    ):
        for sub_name, sub_schema in _collect_properties(prop_schema).items():
            children.append((f"{name}.{sub_name}", sub_schema, (name, sub_name), False))
    # tuple-form items (a list): no descent.
    return children


def _count_consumer_annotations(node: Any) -> int:
    """Count ``x-cosalette-consumer`` blocks anywhere under *node*.

    Unlike :func:`_collect_properties` / :func:`_expand_property_children`
    (which only descend one level into arrays/nested objects), this walks the
    payload schema without a depth limit — it exists solely to detect
    consumer annotations placed deeper than the loader can reach, so a
    channel author gets a diagnostic instead of silent data loss (F23).
    """
    count = 0
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            raw = current.get(X_COSALETTE_CONSUMER)
            if isinstance(raw, dict) and raw:
                count += 1
            # Skip the consumer block itself — it is metadata, not a schema node.
            stack.extend(v for k, v in current.items() if k != X_COSALETTE_CONSUMER)
        elif isinstance(current, list):
            stack.extend(current)
    return count


def _channel_has_unreachable_consumer_annotations(channel: ChannelSchema) -> bool:
    """True if *channel*'s payload schema carries more consumer() blocks than
    were actually extracted onto its properties (F23 diagnostic)."""
    reachable = sum(1 for p in channel.properties.values() if p.consumer is not None)
    return _count_consumer_annotations(channel.payload_schema) > reachable


def find_unreachable_consumer_channels(
    channels: dict[str, ChannelSchema],
) -> frozenset[str]:
    """Return names of channels with a consumer() block the loader could not
    reach — deeper than one level of array/object descent (F23).
    """
    return frozenset(
        name
        for name, channel in channels.items()
        if _channel_has_unreachable_consumer_annotations(channel)
    )


def _extract_properties(
    payload_schema: dict[str, Any] | None,
) -> dict[str, PropertySchema]:
    """Extract PropertySchema objects, descending into composition keywords.

    Handles flat ``properties`` objects as well as ``oneOf`` / ``anyOf`` /
    ``allOf`` union payloads (e.g. telemetry+command shared channels whose
    ``schema init`` output wraps the typed model in a ``oneOf``), plus one
    level of nested object / array-item descent (Finding 16) so annotations
    like ``events[].title`` are reachable.

    Direct top-level properties win over same-named flattened child entries,
    regardless of definition order in the schema.
    """
    top_level = _collect_properties(payload_schema)
    # Pass 1: direct properties always win, regardless of schema order.
    result: dict[str, PropertySchema] = {
        name: _build_property_schema(name, prop) for name, prop in top_level.items()
    }
    # Pass 2: child entries do not override direct properties.
    for name, prop in top_level.items():
        for child_name, child_schema, path, is_array_item in _expand_property_children(
            name, prop
        ):
            if child_name not in result:
                result[child_name] = _build_property_schema(
                    child_name, child_schema, path=path, is_array_item=is_array_item
                )
    return result


# ---------------------------------------------------------------------------
# Channel / operation extraction
# ---------------------------------------------------------------------------


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
            ha_entities=_build_ha_entity_specs(payload_schema),
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
