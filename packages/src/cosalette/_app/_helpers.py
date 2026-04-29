"""Private helper functions used by multiple mixins."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, get_args, get_origin

from cosalette._mqtt import MqttPort
from cosalette._registration import validate_mqtt_name
from cosalette._schema import SchemaRegistry
from cosalette._stream import Stream, StreamablePort
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


def _collect_stream_params(
    func: Callable[..., Any], hints: dict[str, Any]
) -> list[tuple[str, type]]:
    """Return [(param_name, item_type)] for all Stream[T] params in hints."""
    stream_params = []
    for param_name, annotation in hints.items():
        if annotation is Stream:
            msg = (
                f"Stream parameter '{param_name}' in {_callable_qualname(func)} "
                "must be parameterized: Stream[T]"
            )
            raise TypeError(msg)
        if get_origin(annotation) is Stream:
            args = get_args(annotation)
            stream_params.append((param_name, args[0]))
    return stream_params


def _find_compatible_stream_adapter(
    adapters: dict[Any, Any], item_type: type
) -> object | None:
    """Return first StreamablePort[item_type] adapter entry, or None."""
    for port_type, adapter_entry in adapters.items():
        if get_origin(port_type) is StreamablePort:
            port_args = get_args(port_type)
            if port_args and port_args[0] == item_type:
                return adapter_entry
    return None


def _check_no_port_in_signature(
    func: Callable[..., Any], hints: dict[str, Any], item_type: type
) -> None:
    """Raise TypeError if func declares StreamablePort[item_type] directly."""
    for _, ann in hints.items():
        if get_origin(ann) is StreamablePort:
            port_ann_args = get_args(ann)
            if port_ann_args and port_ann_args[0] == item_type:
                item_type_name = getattr(item_type, "__name__", repr(item_type))
                msg = (
                    f"Function {_callable_qualname(func)!r} declares both "
                    f"Stream[{item_type_name}] and"
                    f" StreamablePort[{item_type_name}]. "
                    "The port lifecycle is managed by the framework"
                    " — remove the port parameter."
                )
                raise TypeError(msg)
