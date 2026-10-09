"""Private helper functions used by multiple mixins."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from cosalette._mqtt import MqttLifecycle, MqttPort
from cosalette._schema import SchemaRegistry

if TYPE_CHECKING:
    from cosalette._schema._validator import ValidatingMqttPort


def _validate_positive_interval(name: str, value: float | None) -> None:
    """Raise ``ValueError`` if *value* is non-``None`` and not positive."""
    if value is not None and (not math.isfinite(value) or value <= 0):
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
        build_skip_topics,
        build_validating_port,
    )

    skip = build_skip_topics(prefix, registered_names)
    validator = PayloadValidator(schema_registry)
    port = build_validating_port(
        mqtt_client,
        validator,
        schema_registry.enforcement,
        skip_topics=skip,
    )
    return port, port


async def _publish_schema_status(
    mqtt_client: MqttPort,
    validating_port: ValidatingMqttPort | None,
    schema_registry: SchemaRegistry | None,
    prefix: str,
    *,
    connect_aware: bool = False,
) -> None:
    """Publish initial schema status if validation is active.

    When *connect_aware* is ``True`` and *mqtt_client* implements
    :class:`MqttConnectAware`, registers ``publish_status`` as a connect
    callback so the retained status message is published after the broker
    connection is established (and re-published on every reconnect).
    Otherwise publishes eagerly — suitable for non-connect-aware adapters
    that are already "connected" at call time.
    """
    if validating_port is None or schema_registry is None:
        return

    from cosalette._mqtt import MqttConnectAware
    from cosalette._schema._validator import SchemaStatusPublisher

    publisher = SchemaStatusPublisher(
        _mqtt=mqtt_client,
        _topic_prefix=prefix,
        _enforcement_mode=schema_registry.enforcement.mode,
        _validating_port=validating_port,
    )
    if connect_aware and isinstance(mqtt_client, MqttConnectAware):
        mqtt_client.add_connect_callback(publisher.publish_status)
        return
    await publisher.publish_status()


async def _start_mqtt_and_publish_schema_status(
    mqtt_client: MqttPort,
    validating_port: ValidatingMqttPort | None,
    schema_registry: SchemaRegistry | None,
    prefix: str,
    *,
    connect_aware: bool,
) -> None:
    """Start MQTT and publish schema status in the safe lifecycle order."""
    if connect_aware:
        await _publish_schema_status(
            mqtt_client,
            validating_port,
            schema_registry,
            prefix,
            connect_aware=True,
        )

    if isinstance(mqtt_client, MqttLifecycle):
        await mqtt_client.start()

    if not connect_aware:
        await _publish_schema_status(
            mqtt_client,
            validating_port,
            schema_registry,
            prefix,
            connect_aware=False,
        )
