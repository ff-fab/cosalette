"""Private helper functions used by multiple mixins."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, get_args, get_origin

from cosalette._mqtt import MqttPort
from cosalette._registration import validate_mqtt_name
from cosalette._schema import SchemaRegistry
from cosalette._stream import AsyncStreamablePort, StreamablePort
from cosalette._utils import _callable_qualname

if TYPE_CHECKING:
    from cosalette._schema._validator import ValidatingMqttPort


def _validate_positive_interval(name: str, value: float | None) -> None:
    """Raise ``ValueError`` if *value* is non-``None`` and not positive."""
    if value is not None and value <= 0:
        msg = f"{name} must be positive, got {value}"
        raise ValueError(msg)


def _apply_schema_enforcement(
    mqtt_client: MqttPort,
    schema_registry: SchemaRegistry | None,
    prefix: str,
    registered_names: frozenset[str],
) -> tuple[MqttPort, ValidatingMqttPort | None]:
    """Wrap *mqtt_client* with validation if enforcement is active.

    Returns ``(mqtt_client, validating_port)``; the second element is
    ``None`` when enforcement is off.
    """
    if schema_registry is None or not schema_registry.enforcement.on_publish:
        return mqtt_client, None

    from cosalette._schema._validator import (
        PayloadValidator,
        ValidatingMqttPort,
        build_skip_topics,
    )

    skip = build_skip_topics(prefix, registered_names)
    validator = PayloadValidator(schema_registry)
    port = ValidatingMqttPort(
        inner=mqtt_client,
        validator=validator,
        enforcement=schema_registry.enforcement,
        skip_topics=skip,
    )
    return port, port


async def _publish_schema_status(
    mqtt_client: MqttPort,
    validating_port: ValidatingMqttPort | None,
    schema_registry: SchemaRegistry | None,
    prefix: str,
) -> None:
    """Publish initial schema status if validation is active."""
    if validating_port is None or schema_registry is None:
        return

    from cosalette._schema._validator import SchemaStatusPublisher

    publisher = SchemaStatusPublisher(
        _mqtt=mqtt_client,
        _topic_prefix=prefix,
        _enforcement_mode=schema_registry.enforcement.mode,
        _validating_port=validating_port,
    )
    await publisher.publish_status()


def _validate_periodic_early(
    name: str,
    registered_names: frozenset[str] | set[str],
    interval: object,
) -> None:
    """Validate name uniqueness and interval positivity at decoration time."""
    validate_mqtt_name(name)
    if name in registered_names:
        msg = f"Name '{name}' is already registered"
        raise ValueError(msg)
    if isinstance(interval, (int, float)) and interval <= 0:
        msg = f"Periodic interval for '{name}' must be positive, got {interval}"
        raise ValueError(msg)


# NOTE: _collect_stream_params moved to cosalette._registration._shared
# to eliminate circular import risk (App/Router import registration helpers,
# registration helpers should not depend on _app modules).


def _check_no_port_in_signature(
    func: Callable[..., Any], hints: dict[str, Any], item_type: type
) -> None:
    """Raise TypeError if func declares a port parameter for item_type directly."""
    for _, ann in hints.items():
        origin = get_origin(ann)
        if origin is StreamablePort or origin is AsyncStreamablePort:
            port_ann_args = get_args(ann)
            if port_ann_args and port_ann_args[0] == item_type:
                item_type_name = getattr(item_type, "__name__", repr(item_type))
                port_type_name = (
                    "AsyncStreamablePort"
                    if origin is AsyncStreamablePort
                    else "StreamablePort"
                )
                msg = (
                    f"Function {_callable_qualname(func)!r} declares both "
                    f"Stream[{item_type_name}] and"
                    f" {port_type_name}[{item_type_name}]. "
                    "The framework owns the stream-source lifecycle "
                    "(open, start_scan, stop_scan, close) — "
                    "remove the port parameter. "
                    "To access the adapter for non-lifecycle operations, "
                    "inject its concrete type instead."
                )
                raise TypeError(msg)
