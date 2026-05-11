"""Infrastructure: MQTT, services, state, and availability."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import uuid
from typing import TYPE_CHECKING, Any

from cosalette._clock import ClockPort
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter, build_will_config
from cosalette._mqtt import MqttClient, MqttPort
from cosalette._persistence._state import StateRegistration, _FactoryVariant
from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._command_runner import _FRAMEWORK_ERROR_TYPE_MAP
from cosalette._settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from cosalette._json import dumps as _json_dumps

logger = logging.getLogger("cosalette._wiring")

_REGISTRY_PAYLOAD_WARN_BYTES = 131_072  # 128 KiB


def create_mqtt(
    mqtt: MqttPort | None,
    resolved_settings: Settings,
    prefix: str,
    app_name: str,
) -> MqttPort:
    """Create the MQTT client, or return the injected one.

    When no explicit ``client_id`` is configured, generates one from
    the app name and a short random suffix (e.g.
    ``"velux2mqtt-a1b2c3d4"``) for debuggability.
    """
    if mqtt is not None:
        return mqtt
    mqtt_settings = resolved_settings.mqtt
    if not mqtt_settings.client_id:
        generated_id = f"{app_name}-{uuid.uuid4().hex[:8]}"
        mqtt_settings = mqtt_settings.model_copy(
            update={"client_id": generated_id},
        )
    will = build_will_config(prefix)
    return MqttClient(settings=mqtt_settings, will=will)


def create_services(
    mqtt: MqttPort,
    prefix: str,
    version: str,
    clock: ClockPort,
) -> tuple[HealthReporter, ErrorPublisher]:
    """Build the HealthReporter and ErrorPublisher."""
    health_reporter = HealthReporter(
        mqtt=mqtt,
        topic_prefix=prefix,
        version=version,
        clock=clock,
    )
    error_publisher = ErrorPublisher(
        mqtt=mqtt,
        topic_prefix=prefix,
        error_type_map=dict(_FRAMEWORK_ERROR_TYPE_MAP),
    )
    return health_reporter, error_publisher


def install_signal_handlers(
    shutdown_event: asyncio.Event | None,
) -> asyncio.Event:
    """Install SIGTERM/SIGINT handlers.  Returns the shutdown event."""
    if shutdown_event is not None:
        return shutdown_event
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, event.set)
    return event


async def _enter_one_state(
    reg: StateRegistration,
    kwargs: dict[str, Any],
    exit_stack: contextlib.AsyncExitStack,
) -> Any:
    """Enter a single state factory and return the resolved instance."""
    if reg.variant == _FactoryVariant.SYNC:
        return reg.factory(**kwargs)
    if reg.variant == _FactoryVariant.CONTEXT_MANAGER:
        return exit_stack.enter_context(reg.factory(**kwargs))
    if reg.variant == _FactoryVariant.ASYNC_GEN:
        result = reg.factory(**kwargs)
        if hasattr(result, "__aenter__") and hasattr(result, "__aexit__"):
            # @asynccontextmanager wraps the generator in an async CM
            return await exit_stack.enter_async_context(result)
        # Raw async generator: advance to the yield, close on teardown
        instance = await anext(result)
        exit_stack.push_async_callback(result.aclose)
        return instance
    if reg.variant == _FactoryVariant.ASYNC_CM:
        return await exit_stack.enter_async_context(reg.factory(**kwargs))
    raise ValueError(f"Unsupported factory variant: {reg.variant}")  # pragma: no cover


@contextlib.asynccontextmanager
async def enter_state_factories(
    registrations: list[StateRegistration],
    settings: Settings,
    overrides: dict[type, Any] | None = None,
) -> AsyncIterator[dict[type, Any]]:
    """Run @app.state factories in registration order; yield DI providers dict.

    Teardown runs in reverse registration order via AsyncExitStack LIFO semantics.
    Test overrides bypass the factory entirely.
    """
    if not registrations and not overrides:
        yield {}
        return

    state_objects: dict[type, Any] = dict(overrides) if overrides else {}

    async with contextlib.AsyncExitStack() as exit_stack:
        for reg in registrations:
            if overrides and reg.state_type in overrides:
                continue
            kwargs: dict[str, Any] = {}
            if reg.has_settings_param:
                kwargs[reg.settings_param_name] = settings
            state_objects[reg.state_type] = await _enter_one_state(
                reg, kwargs, exit_stack
            )

        yield state_objects


async def publish_device_availability(
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
    health_reporter: HealthReporter,
) -> None:
    """Publish availability for all registered devices.

    When telemetry and command share a name (scoped uniqueness),
    availability is published once for the shared name.
    """
    seen: set[str] = set()
    for reg in all_registrations:
        if reg.name not in seen:
            seen.add(reg.name)
            await health_reporter.publish_device_available(
                reg.name,
                is_root=reg.is_root,
            )


def _asyncapi_doc_for_broker(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *doc* with inbound command channels stripped.

    Removes channels referenced only by ``receive``-action operations so that
    the retained broker document does not expose the command surface to
    unprivileged subscribers.  State and device channels (``send`` action)
    are preserved unchanged.

    If no ``receive``-action operations exist the original dict is returned
    without copying (fast path).
    """
    receive_channels: set[str] = set()
    for op in doc.get("operations", {}).values():
        if op.get("action") == "receive":
            ref = op.get("channel", {}).get("$ref", "")
            if ref.startswith("#/channels/"):
                receive_channels.add(ref.removeprefix("#/channels/"))

    if not receive_channels:
        return doc

    filtered_channels = {
        k: v for k, v in doc.get("channels", {}).items() if k not in receive_channels
    }
    filtered_operations = {
        k: v
        for k, v in doc.get("operations", {}).items()
        if v.get("action") != "receive"
    }
    result = {**doc}
    if filtered_channels:
        result["channels"] = filtered_channels
        result["operations"] = filtered_operations
    else:
        result.pop("channels", None)
        result.pop("operations", None)
    return result


async def publish_registry_snapshot(
    app: Any,  # App type - using Any to avoid circular import
    mqtt: MqttPort,
    prefix: str,
) -> None:
    """Publish the canonical AsyncAPI document to MQTT (fire-and-forget).

    Serializes the canonical AsyncAPI 3.0.0 document as compact JSON
    and publishes it as a retained message to ``{prefix}/_meta/registry``.
    The topic name is preserved for backward compatibility with broker ACL rules
    and subscribers.  Errors are logged but never propagated.

    Inbound command channels (``receive``-action operations) are stripped from
    the published document to avoid exposing the command surface on shared
    brokers.  Only state/device channels are retained.

    .. warning:: Security

       The document exposes MQTT channel addresses, payload schemas, and
       operation metadata.  In shared-broker deployments consider protecting
       ``_meta/#`` with broker ACLs in addition to the built-in command
       channel redaction.
    """
    topic = f"{prefix}/_meta/registry"
    try:
        asyncapi_doc = _asyncapi_doc_for_broker(app.asyncapi())
        payload_str = _json_dumps(asyncapi_doc)
        payload_size = len(payload_str.encode("utf-8"))
        if payload_size > _REGISTRY_PAYLOAD_WARN_BYTES:
            logger.warning(
                "AsyncAPI document payload is %d bytes (threshold %d); "
                "large payloads may exceed broker max_packet_size limits",
                payload_size,
                _REGISTRY_PAYLOAD_WARN_BYTES,
            )
        await mqtt.publish(topic, payload_str, retain=True, qos=1)
    except Exception:
        logger.exception("Failed to publish canonical AsyncAPI document to %s", topic)
