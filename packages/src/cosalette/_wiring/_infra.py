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
from cosalette._mqtt import MqttClient, MqttConnectAware, MqttPort
from cosalette._persistence._state import StateRegistration, _FactoryVariant
from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._command_runner import _FRAMEWORK_ERROR_TYPE_MAP
from cosalette._settings import Settings
from cosalette._wiring._retained_cleanup import reconcile_retained_topics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from cosalette._persistence._stores import Store

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
    *,
    error_publish_verbose: bool = False,
    error_type_map: dict[type[Exception], str] | None = None,
) -> tuple[HealthReporter, ErrorPublisher]:
    """Build the HealthReporter and ErrorPublisher.

    The ErrorPublisher's map is the framework command-exception map merged
    with the app-provided *error_type_map*.  **Framework entries are
    authoritative** — an app cannot override or shadow framework error
    handling; app entries only extend the map for app-owned exception types
    (LEAK-01 targeted opt-in; see ADR-011).
    """
    health_reporter = HealthReporter(
        mqtt=mqtt,
        topic_prefix=prefix,
        version=version,
        clock=clock,
    )
    merged_error_type_map = {**(error_type_map or {}), **_FRAMEWORK_ERROR_TYPE_MAP}
    error_publisher = ErrorPublisher(
        mqtt=mqtt,
        topic_prefix=prefix,
        error_type_map=merged_error_type_map,
        verbose=error_publish_verbose,
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
        # Use cached broker-ready JSON to avoid re-serialising the AsyncAPI doc
        # on every reconnect — the schema is immutable after app setup.
        payload_str: str | None = getattr(app, "_asyncapi_broker_cache", None)
        if payload_str is None:
            asyncapi_doc = _asyncapi_doc_for_broker(app.asyncapi())
            payload_str = _json_dumps(asyncapi_doc)
            # Size check only on first serialisation; char count is a
            # conservative upper bound for ASCII-dominated JSON.
            payload_size = len(payload_str)
            if payload_size > _REGISTRY_PAYLOAD_WARN_BYTES:
                logger.warning(
                    "AsyncAPI document payload is %d bytes (threshold %d); "
                    "large payloads may exceed broker max_packet_size limits",
                    payload_size,
                    _REGISTRY_PAYLOAD_WARN_BYTES,
                )
            with contextlib.suppress(TypeError, AttributeError):
                object.__setattr__(app, "_asyncapi_broker_cache", payload_str)
        await mqtt.publish(topic, payload_str, retain=True, qos=1)
    except Exception:
        logger.exception("Failed to publish canonical AsyncAPI document to %s", topic)


def register_connect_reannounce(
    mqtt: MqttPort,
    app: Any,  # App — Any to avoid circular import
    health_reporter: HealthReporter,
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
    prefix: str,
    store: Store | None,
) -> bool:
    """Register a connect-reannounce callback if the adapter is connect-aware.

    Returns ``True`` when *mqtt* implements :class:`MqttConnectAware` and a
    callback was registered; ``False`` otherwise (the caller should then fall
    back to eager startup publishes). See ADR-012 amendment / ADR-016.

    On the first successful MQTT connect, also clears orphaned retained topics
    for entities removed from config since the last run (ADR-048).
    """
    if not isinstance(mqtt, MqttConnectAware):
        return False
    _first_connect_done = False

    async def _on_connect() -> None:
        nonlocal _first_connect_done
        initial = not _first_connect_done
        _first_connect_done = True
        # First connect: optimistic full announce for all registrations.
        # Reconnects: re-assert only currently-tracked-online devices.
        if initial:
            await reconcile_retained_topics(mqtt, all_registrations, prefix, store)
            await publish_device_availability(all_registrations, health_reporter)
        else:
            await health_reporter.reannounce()
        await publish_registry_snapshot(app, mqtt, prefix)
        await health_reporter.publish_heartbeat()

    mqtt.add_connect_callback(_on_connect)
    return True


async def publish_startup_snapshot(
    app: Any,  # App — Any to avoid circular import
    mqtt: MqttPort,
    health_reporter: HealthReporter,
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
    prefix: str,
    store: Store | None,
    *,
    connect_aware: bool,
) -> None:
    """Eagerly publish startup availability + registry for non-connect-aware adapters.

    No-op when *connect_aware* is ``True`` (the connect callback registered by
    :func:`register_connect_reannounce` handles announces on (re)connect instead).

    For non-connect-aware adapters, also clears orphaned retained topics for
    entities removed from config since the last run (ADR-048).
    """
    if connect_aware:
        return
    await reconcile_retained_topics(mqtt, all_registrations, prefix, store)
    await publish_device_availability(all_registrations, health_reporter)
    await publish_registry_snapshot(app, mqtt, prefix)
