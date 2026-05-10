"""AsyncAPI document conversion utilities for schema tooling.

Internal helpers used by :mod:`cosalette._schema._cli` to convert
:class:`~cosalette._schema.SchemaRegistry` objects and registry snapshots
into AsyncAPI-compatible dicts for YAML output.  Also exposes
:func:`build_app_asyncapi`, the canonical builder used by
:meth:`cosalette.App.asyncapi`, the CLI ``dump`` subcommand, and the MCP
``cosalette_manifest`` tool.

None of the functions here depend on typer — they are pure data transformations.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, NamedTuple, get_args, get_origin

if TYPE_CHECKING:
    from cosalette._app import App
    from cosalette._schema import ChannelSchema, SchemaRegistry


# ---------------------------------------------------------------------------
# Contract-shape version — bump when the generated dict structure changes
# ---------------------------------------------------------------------------

_CONTRACT_VERSION = "1"


# ---------------------------------------------------------------------------
# AsyncAPI / MQTT naming conventions
# ---------------------------------------------------------------------------

_COMMAND_SUFFIX = "Command"
_STATE_SUFFIX = "State"
_COMMAND_ADDRESS = "set"
_STATE_ADDRESS = "state"
_SEND_ACTION = "send"
_RECEIVE_ACTION = "receive"
_PUBLISH_VERB = "publish"
_RECEIVE_VERB = "receive"


# ---------------------------------------------------------------------------
# Channel dict assembly helpers
# ---------------------------------------------------------------------------


def _add_channel_extensions(
    channel: ChannelSchema, channel_dict: dict[str, Any]
) -> None:
    """Add x-cosalette extensions to channel dict.

    Args:
        channel: The ChannelSchema to read extensions from.
        channel_dict: The channel dict to add extensions to.
    """
    if channel.app_name:
        channel_dict["x-cosalette-app"] = channel.app_name
    if channel.archetype:
        channel_dict["x-cosalette-archetype"] = channel.archetype
    if channel.scope:
        channel_dict["x-cosalette-scope"] = channel.scope
    if channel.coalescing_group:
        channel_dict["x-cosalette-coalescing-group"] = channel.coalescing_group


def _add_mqtt_binding(channel: ChannelSchema, channel_dict: dict[str, Any]) -> None:
    """Add MQTT binding to channel dict if non-default.

    Args:
        channel: The ChannelSchema to read MQTT binding from.
        channel_dict: The channel dict to add binding to.
    """
    if channel.mqtt_binding.qos != 1 or channel.mqtt_binding.retain is not False:
        channel_dict["bindings"] = {
            "mqtt": {
                "qos": channel.mqtt_binding.qos,
                "retain": channel.mqtt_binding.retain,
            }
        }


def _add_capability_requirements(
    channel: ChannelSchema, channel_dict: dict[str, Any]
) -> None:
    """Add capability requirements to channel dict.

    Args:
        channel: The ChannelSchema to read requirements from.
        channel_dict: The channel dict to add requirements to.
    """
    if channel.capability_requirements:
        reqs = []
        for req in channel.capability_requirements:
            req_dict = {"tag": req.tag}
            if req.description:
                req_dict["description"] = req.description
            reqs.append(req_dict)
        channel_dict["x-cosalette-requires"] = reqs


def _add_payload_schema(channel: ChannelSchema, channel_dict: dict[str, Any]) -> None:
    """Add message payload schema to channel dict if present.

    Args:
        channel: The ChannelSchema to read payload schema from.
        channel_dict: The channel dict to add schema to.
    """
    if channel.payload_schema:
        channel_dict["messages"] = {
            channel.message_name or "message": {"payload": channel.payload_schema}
        }


def _channel_to_dict(channel: ChannelSchema) -> dict[str, Any]:
    """Convert a single ChannelSchema to a dict for AsyncAPI output.

    Args:
        channel: The ChannelSchema to convert.

    Returns:
        Channel dict suitable for AsyncAPI document.
    """
    channel_dict: dict[str, Any] = {
        "address": channel.address,
    }

    _add_channel_extensions(channel, channel_dict)
    _add_mqtt_binding(channel, channel_dict)
    _add_capability_requirements(channel, channel_dict)
    _add_payload_schema(channel, channel_dict)

    return channel_dict


# ---------------------------------------------------------------------------
# Snapshot-to-AsyncAPI conversion
# ---------------------------------------------------------------------------


def _to_camel_case(name: str) -> str:
    """Convert an underscore- or slash-separated name to PascalCase.

    Handles router prefix names such as ``sensors/temperature`` by treating
    each ``/``-separated segment as a word boundary in addition to ``_``.
    """
    parts: list[str] = []
    for segment in name.split("/"):
        parts.extend(word.capitalize() for word in segment.split("_") if word)
    return "".join(parts)


def _reg_name_to_channel_id(reg_name: str, suffix: str) -> str:
    """Convert a registration name to a JSON-Pointer-safe AsyncAPI channel ID.

    Router inclusion names may contain ``/`` (e.g. ``sensors/temperature``),
    which would produce an invalid JSON Pointer path if used verbatim as a
    channel key.  This helper camelCase-joins slash-separated segments so
    the resulting ID contains no slashes.

    Plain single-segment names are returned unchanged (preserves existing
    behaviour for un-prefixed registrations).

    Args:
        reg_name: The registration name, possibly slash-prefixed.
        suffix:   AsyncAPI channel suffix (``"State"`` or ``"Command"``).

    Returns:
        A valid JSON-Pointer path component such as ``"sensorsTemperatureState"``.
    """
    parts = reg_name.split("/")
    if len(parts) == 1:
        # No slashes — keep existing behaviour (no normalisation)
        return f"{reg_name}{suffix}"
    # camelCase join: first segment as-is, subsequent segments capitalised
    camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
    return f"{camel}{suffix}"


class _ChannelOperation(NamedTuple):
    """Channel + operation pair produced by :func:`_build_snapshot_channel`."""

    channel_name: str
    channel_dict: dict[str, Any]
    operation_name: str
    operation_dict: dict[str, Any]


def _apply_contract_extensions(
    channel_dict: dict[str, Any], entry: dict[str, Any]
) -> None:
    """Emit x-cosalette contract metadata extensions into *channel_dict*."""
    for key, ext in (
        ("summary", "x-cosalette-summary"),
        ("behavior", "x-cosalette-behavior"),
        ("effects", "x-cosalette-effects"),
    ):
        if (val := entry.get(key)) is not None:
            channel_dict[ext] = val


def _build_snapshot_channel(
    app_name: str,
    device_name: str,
    *,
    kind: str,
    include_extensions: bool,
    entry: dict[str, Any] | None = None,
) -> _ChannelOperation:
    """Build a channel+operation pair from a snapshot entry.

    Args:
        app_name: App name for address prefix.
        device_name: Device name from the snapshot.
        kind: One of ``"device"``, ``"telemetry"``, or ``"command"``.
        include_extensions: Whether to include ``x-cosalette-*`` extensions,
            including ``x-cosalette-archetype`` and any available contract
            metadata extensions (``x-cosalette-summary``, ``x-cosalette-behavior``,
            ``x-cosalette-effects``).
        entry: Full snapshot entry dict; used to emit contract metadata extensions.

    Returns:
        A :class:`_ChannelOperation` named tuple.
    """
    camel = _to_camel_case(device_name)
    is_command = kind == "command"
    suffix = _COMMAND_SUFFIX if is_command else _STATE_SUFFIX
    channel_name = f"{device_name}{suffix}"
    action = _RECEIVE_ACTION if is_command else _SEND_ACTION
    verb = _RECEIVE_VERB if is_command else _PUBLISH_VERB
    address_suffix = _COMMAND_ADDRESS if is_command else _STATE_ADDRESS

    channel_dict: dict[str, Any] = {
        "address": f"{app_name}/{device_name}/{address_suffix}",
        "messages": {"message": {"payload": {"type": "object"}}},
    }
    if include_extensions:
        channel_dict["x-cosalette-archetype"] = kind
        _apply_contract_extensions(channel_dict, entry or {})

    operation_name = f"{verb}{camel}{suffix}"
    operation_dict: dict[str, Any] = {
        "action": action,
        "channel": {"$ref": f"#/channels/{channel_name}"},
    }

    return _ChannelOperation(channel_name, channel_dict, operation_name, operation_dict)


def _registry_to_asyncapi_dict(registry: SchemaRegistry) -> dict[str, Any]:
    """Convert a SchemaRegistry back to an AsyncAPI-like dict for YAML output.

    Reconstructs a minimal AsyncAPI document from the filtered SchemaRegistry.
    Does not include operations as they reference channels by $ref and would
    break without the full document.

    Args:
        registry: The SchemaRegistry to convert.

    Returns:
        AsyncAPI-compatible dict structure.
    """
    result: dict[str, Any] = {
        "asyncapi": registry.asyncapi_version,
        "info": {
            "title": registry.app_name or "Filtered Schema",
            "version": registry.app_version,
        },
    }

    # Add enforcement config if present
    if (
        registry.enforcement.mode != "off"
        or registry.enforcement.on_configure is not True
        or registry.enforcement.on_publish is not False
        or registry.enforcement.network_level is not False
    ):
        result["x-cosalette-enforcement"] = {
            "mode": registry.enforcement.mode,
            "on_configure": registry.enforcement.on_configure,
            "on_publish": registry.enforcement.on_publish,
            "network_level": registry.enforcement.network_level,
        }

    # Add channels
    channels: dict[str, Any] = {}
    for name, channel in registry.channels.items():
        channels[name] = _channel_to_dict(channel)

    if channels:
        result["channels"] = channels

    return result


def _snapshot_to_asyncapi(
    app_name: str,
    app_version: str,
    snapshot: dict[str, Any],
    *,
    include_extensions: bool = False,
) -> dict[str, Any]:
    """Convert a registry snapshot to an AsyncAPI document dict.

    Args:
        app_name: App name from snapshot.
        app_version: App version from snapshot.
        snapshot: Dict returned by build_registry_snapshot().
        include_extensions: Whether to include x-cosalette extensions.

    Returns:
        AsyncAPI 3.0.0-compliant document dict.
    """
    result: dict[str, Any] = {
        "asyncapi": "3.0.0",
        "info": {
            "title": app_name,
            "version": app_version,
        },
    }

    if include_extensions:
        result["x-cosalette-enforcement"] = {
            "mode": "warn",
            "on_configure": True,
            "on_publish": False,
            "network_level": False,
        }

    channels: dict[str, Any] = {}
    operations: dict[str, Any] = {}

    kind_map = {"devices": "device", "telemetry": "telemetry", "commands": "command"}
    for key, kind in kind_map.items():
        for entry in snapshot.get(key, []):
            ch_name, ch_dict, op_name, op_dict = _build_snapshot_channel(
                app_name,
                entry["name"],
                kind=kind,
                include_extensions=include_extensions,
                entry=entry,
            )
            channels[ch_name] = ch_dict
            operations[op_name] = op_dict

    # Add to result if any channels exist
    if channels:
        result["channels"] = channels
        result["operations"] = operations

    return result


# ---------------------------------------------------------------------------
# Canonical App → AsyncAPI builder (cos-bnq)
# ---------------------------------------------------------------------------


def _type_to_json_schema(tp: type | None) -> dict[str, Any] | None:
    """Return a JSON Schema dict for *tp* via Pydantic TypeAdapter.

    Returns ``None`` for types that cannot be introspected (e.g. bare
    ``None``, ``NoneType``, or annotation resolution failures).
    """
    if tp is None:
        return None
    # Skip NoneType (handlers that return None suppress publish)
    import types as _types

    if tp is type(None) or tp is _types.NoneType:
        return None
    try:
        from pydantic import TypeAdapter

        return TypeAdapter(tp).json_schema(ref_template="#/components/schemas/{model}")
    except Exception:
        return None


def _extract_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pull top-level ``$defs`` out of a JSON Schema and return them."""
    return dict(schema.pop("$defs", {}))


def _build_mqtt_address(
    app_name: str,
    reg_name: str,
    address_suffix: str,
    *,
    is_root: bool,
) -> str:
    """Compute the MQTT topic address for a channel."""
    if not is_root:
        return f"{app_name}/{reg_name}/{address_suffix}"
    segments = reg_name.split("/")
    if len(segments) > 1:
        prefix_path = "/".join(segments[:-1])
        return f"{app_name}/{prefix_path}/{address_suffix}"
    return f"{app_name}/{address_suffix}"


def _build_channel_dict(
    address: str,
    payload: dict[str, Any],
    archetype_label: str,
    tags: tuple[str, ...],
    summary: str | None,
    behavior: list[str] | None,
    effects: list[str] | None,
) -> dict[str, Any]:
    """Assemble the channel object for an AsyncAPI channel entry."""
    channel: dict[str, Any] = {
        "address": address,
        "messages": {"message": {"payload": payload}},
        "x-cosalette-archetype": archetype_label,
    }
    if tags:
        channel["tags"] = [{"name": t} for t in sorted(tags)]
    if summary is not None:
        channel["x-cosalette-summary"] = summary
    if behavior is not None:
        channel["x-cosalette-behavior"] = behavior
    if effects is not None:
        channel["x-cosalette-effects"] = effects
    return channel


def _build_operation_dict(
    action: str,
    channel_name: str,
    verb: str,
    camel: str,
    suffix: str,
    tags: tuple[str, ...],
    summary: str | None,
) -> tuple[str, dict[str, Any]]:
    """Assemble the operation name and object for an AsyncAPI operation entry."""
    operation_name = f"{verb}{camel}{suffix}"
    operation: dict[str, Any] = {
        "action": action,
        "channel": {"$ref": f"#/channels/{channel_name}"},
    }
    if summary is not None:
        operation["summary"] = summary
    if tags:
        operation["tags"] = [{"name": t} for t in sorted(tags)]
    return operation_name, operation


def _build_channel_entry(
    app_name: str,
    reg_name: str,
    *,
    kind: str,
    schema: dict[str, Any] | None,
    tags: tuple[str, ...],
    summary: str | None,
    behavior: list[str] | None,
    effects: list[str] | None,
    is_root: bool = False,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    """Build a (channel_name, channel_dict, op_name, op_dict) quad.

    Args:
        app_name: The App's MQTT prefix / name.
        reg_name: The registration name (device/telemetry/command name).
            May contain ``/`` when a Router prefix has been applied.
        kind: ``"device"``, ``"telemetry"``, ``"command"``, or
            ``"command_state"`` (state output channel for a command that
            publishes state back after execution).
        schema: JSON Schema dict for the message payload, or ``None``.
        tags: Sequence of tag strings.
        summary: Optional human summary.
        behavior: Optional behavior list.
        effects: Optional effects list.
        is_root: When ``True`` the registration occupies the app-level
            topic (no device-name segment).  The MQTT address becomes
            ``{app}/{state|set}`` instead of ``{app}/{name}/{state|set}``.

    Returns:
        4-tuple ``(channel_name, channel_dict, operation_name, operation_dict)``.
    """
    camel = _to_camel_case(reg_name)
    is_command_input = kind == "command"
    # "command_state" emits a *state* (send) channel that belongs to a command
    archetype_label = "command" if kind in {"command", "command_state"} else kind
    suffix = _COMMAND_SUFFIX if is_command_input else _STATE_SUFFIX
    channel_name = _reg_name_to_channel_id(reg_name, suffix)
    action = _RECEIVE_ACTION if is_command_input else _SEND_ACTION
    verb = _RECEIVE_VERB if is_command_input else _PUBLISH_VERB
    address_suffix = _COMMAND_ADDRESS if is_command_input else _STATE_ADDRESS

    address = _build_mqtt_address(app_name, reg_name, address_suffix, is_root=is_root)
    payload: dict[str, Any] = schema if schema is not None else {"type": "object"}
    channel_dict = _build_channel_dict(
        address, payload, archetype_label, tags, summary, behavior, effects
    )
    operation_name, operation_dict = _build_operation_dict(
        action, channel_name, verb, camel, suffix, tags, summary
    )

    return channel_name, channel_dict, operation_name, operation_dict


def _infer_command_payload_type(
    injection_plan: list[tuple[str, type]],
) -> type | None:
    """Infer the inbound payload type for a command from its injection plan.

    Priority:
    1. ``Annotated[T, Payload()]`` — explicit PEP 593 binding marker.
    2. Parameter named ``payload`` with a non-``str`` concrete type.

    Args:
        injection_plan: List of ``(param_name, type)`` pairs from registration.

    Returns:
        The inferred payload type, or ``None`` if no typed payload is found.
    """
    from cosalette.mqtt import _PayloadMarker

    for param_name, param_type in injection_plan:
        # 1. Annotated[T, Payload()] — explicit marker
        if get_origin(param_type) is Annotated:
            args = get_args(param_type)
            if len(args) >= 2 and isinstance(args[1], _PayloadMarker):
                return args[0]
        # 2. Named `payload` with non-str type (convention-based binding)
        if param_name == "payload" and param_type is not str:
            return param_type
    return None


def _merge_command_state_channel(
    channels: dict[str, Any],
    operations: dict[str, Any],
    component_defs: dict[str, Any],
    s_ch_name: str,
    s_ch_dict: dict[str, Any],
    s_op_name: str,
    s_op_dict: dict[str, Any],
    state_defs: dict[str, Any],
) -> None:
    """Merge a command-state channel into *channels*/*operations*.

    If *s_ch_name* is already occupied (e.g. by a same-name telemetry), the
    payloads are merged with ``oneOf`` rather than clobbering existing metadata.
    Identical schemas are de-duplicated (no-op).
    """
    component_defs.update(state_defs)
    if s_ch_name in channels:
        existing_ch = channels[s_ch_name]
        existing_payload = (
            existing_ch.get("messages", {}).get("message", {}).get("payload")
        )
        new_payload = s_ch_dict.get("messages", {}).get("message", {}).get("payload")
        if existing_payload != new_payload:
            merged = dict(existing_ch)
            merged["messages"] = {
                "message": {"payload": {"oneOf": [existing_payload, new_payload]}}
            }
            channels[s_ch_name] = merged
        # else: identical schemas — keep existing as-is (no-op)
        # Do not overwrite the operation; existing op wins.
    else:
        channels[s_ch_name] = s_ch_dict
        operations[s_op_name] = s_op_dict


def _register_entry(
    app_name: str,
    channels: dict[str, Any],
    operations: dict[str, Any],
    component_defs: dict[str, Any],
    reg_name: str,
    kind: str,
    *,
    state_model: type | None,
    payload_model: type | None,
    func: Any,
    injection_plan: list[tuple[str, type]] | None = None,
    tags: tuple[str, ...],
    summary: str | None,
    behavior: list[str] | None,
    effects: list[str] | None,
    is_root: bool = False,
) -> None:
    """Resolve schema, build channel/operation dicts, and write into shared maps."""
    from cosalette._contracts import get_return_annotation

    if kind == "command":
        schema_type: type | None = payload_model or _infer_command_payload_type(
            injection_plan or []
        )
    else:
        schema_type = state_model or get_return_annotation(func)

    schema = _type_to_json_schema(schema_type)
    if schema is not None:
        component_defs.update(_extract_defs(schema))

    ch_name, ch_dict, op_name, op_dict = _build_channel_entry(
        app_name,
        reg_name,
        kind=kind,
        schema=schema,
        tags=tags,
        summary=summary,
        behavior=behavior,
        effects=effects,
        is_root=is_root,
    )
    channels[ch_name] = ch_dict
    operations[op_name] = op_dict

    if kind != "command":
        return

    # Command state output channel — emitted only when a concrete type is known.
    # Priority: explicit state_model > return annotation > omit (no noise for voids)
    cmd_state_type: type | None = state_model or get_return_annotation(func)
    if cmd_state_type is None:
        return

    state_schema = _type_to_json_schema(cmd_state_type)
    if state_schema is None:
        return

    s_ch_name, s_ch_dict, s_op_name, s_op_dict = _build_channel_entry(
        app_name,
        reg_name,
        kind="command_state",
        schema=state_schema,
        tags=tags,
        summary=summary,
        behavior=behavior,
        effects=effects,
        is_root=is_root,
    )
    _merge_command_state_channel(
        channels,
        operations,
        component_defs,
        s_ch_name,
        s_ch_dict,
        s_op_name,
        s_op_dict,
        _extract_defs(state_schema),
    )


def build_app_asyncapi(app: App) -> dict[str, Any]:
    """Build a canonical AsyncAPI 3.0.0 document dict from *app* registrations.

    This is the single source of truth used by :meth:`~cosalette.App.asyncapi`,
    the CLI ``schema dump`` subcommand, and the MCP ``cosalette_manifest`` tool.

    Schema inference priority (explicit decorator wins over annotation):

    **Commands** (inbound ``/set`` channel payload):

    1. ``payload_model`` explicitly set on the registration.
    2. Inferred from injection plan: ``Annotated[T, Payload()]`` or
       ``payload: T`` (non-``str``) convention.
    3. Fallback: ``{"type": "object"}``.

    **Commands** (outbound ``/state`` channel, emitted only when a type is known):

    1. ``state_model`` explicitly set on the registration.
    2. Handler return-type annotation via
       :func:`~cosalette._contracts.get_return_annotation`.
    3. Omitted when neither is present (avoids noise for void commands).

    **Telemetry / devices** (outbound state schema):

    1. ``state_model`` explicitly set on the registration.
    2. Handler return-type annotation via
       :func:`~cosalette._contracts.get_return_annotation`.
    3. Fallback: ``{"type": "object"}``.

    Generated document includes ``x-cosalette-contract-version`` in the ``info``
    section to track the contract-shape version independently from the app version.

    Args:
        app: The :class:`~cosalette.App` instance to introspect.

    Returns:
        A deterministic, JSON-serialisable ``dict`` representing the AsyncAPI
        3.0.0 document.  Keys within each section are ordered alphabetically to
        ensure stable output across Python versions.
    """
    channels: dict[str, Any] = {}
    operations: dict[str, Any] = {}
    component_defs: dict[str, Any] = {}

    for reg in app.telemetry_registrations:
        _register_entry(
            app.name,
            channels,
            operations,
            component_defs,
            reg.name,
            "telemetry",
            state_model=reg.state_model,
            payload_model=reg.payload_model,
            func=reg.func,
            tags=reg.tags,
            summary=reg.summary,
            behavior=reg.behavior,
            effects=reg.effects,
            is_root=reg.is_root,
        )

    for reg in app.commands:
        _register_entry(
            app.name,
            channels,
            operations,
            component_defs,
            reg.name,
            "command",
            state_model=reg.state_model,
            payload_model=reg.payload_model,
            func=reg.func,
            injection_plan=reg.injection_plan,
            tags=reg.tags,
            summary=reg.summary,
            behavior=reg.behavior,
            effects=reg.effects,
            is_root=reg.is_root,
        )

    for reg in app.devices:
        _register_entry(
            app.name,
            channels,
            operations,
            component_defs,
            reg.name,
            "device",
            state_model=None,
            payload_model=None,
            func=reg.func,
            tags=reg.tags,
            summary=reg.summary,
            behavior=reg.behavior,
            effects=reg.effects,
            is_root=reg.is_root,
        )

    result: dict[str, Any] = {
        "asyncapi": "3.0.0",
        "info": {
            "title": app.name,
            "version": app.version,
            "x-cosalette-contract-version": _CONTRACT_VERSION,
        },
    }

    if channels:
        result["channels"] = dict(sorted(channels.items()))
        result["operations"] = dict(sorted(operations.items()))

    if component_defs:
        result["components"] = {"schemas": dict(sorted(component_defs.items()))}

    return result
