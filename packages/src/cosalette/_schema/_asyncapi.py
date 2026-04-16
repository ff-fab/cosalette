"""AsyncAPI document conversion utilities for schema tooling.

Internal helpers used by :mod:`cosalette._schema._cli` to convert
:class:`~cosalette._schema.SchemaRegistry` objects and registry snapshots
into AsyncAPI-compatible dicts for YAML output.

None of the functions here depend on typer — they are pure data transformations.

See Also:
    ADR-033 — MQTT schema enforcement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from cosalette._schema import ChannelSchema, SchemaRegistry


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
    """Convert an underscore-separated name to CamelCase."""
    return "".join(word.capitalize() for word in name.split("_"))


class _ChannelOperation(NamedTuple):
    """Channel + operation pair produced by :func:`_build_snapshot_channel`."""

    channel_name: str
    channel_dict: dict[str, Any]
    operation_name: str
    operation_dict: dict[str, Any]


def _build_snapshot_channel(
    app_name: str,
    device_name: str,
    *,
    kind: str,
    include_extensions: bool,
) -> _ChannelOperation:
    """Build a channel+operation pair from a snapshot entry.

    Args:
        app_name: App name for address prefix.
        device_name: Device name from the snapshot.
        kind: One of ``"device"``, ``"telemetry"``, or ``"command"``.
        include_extensions: Whether to add x-cosalette-archetype.

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
            )
            channels[ch_name] = ch_dict
            operations[op_name] = op_dict

    # Add to result if any channels exist
    if channels:
        result["channels"] = channels
        result["operations"] = operations

    return result
