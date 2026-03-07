"""Bootstrap wiring for cosalette applications.

Stateless functions that wire together settings, MQTT, services,
signal handlers, device contexts, routing, and the run-loop.
Originally private methods on :class:`~cosalette._app.App`; extracted
to shrink the god-class and turn ``_run_async`` into a clean recipe.

.. note::

   The module is private (``_wiring``), so the functions omit the
   leading underscore that they carried as ``App`` methods.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import uuid

from cosalette._clock import ClockPort
from cosalette._command_runner import CommandRunner
from cosalette._context import AppContext, DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter, build_will_config
from cosalette._mqtt import MqttClient, MqttMessageHandler, MqttPort
from cosalette._registration import (
    LifespanFunc,
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
)
from cosalette._router import TopicRouter
from cosalette._settings import Settings
from cosalette._stores import Store
from cosalette._telemetry_runner import TelemetryRunner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 1: Bootstrap
# ---------------------------------------------------------------------------


def resolve_settings(
    settings: Settings | None,
    eager_settings: Settings | None,
    settings_class: type[Settings],
) -> Settings:
    """Return the effective settings instance.

    Priority: explicit override > eagerly-created > fresh from class.
    """
    if settings is not None:
        return settings
    if eager_settings is not None:
        return eager_settings
    return settings_class()


def resolve_intervals(
    telemetry_list: list[_TelemetryRegistration],
    settings: Settings,
) -> None:
    """Resolve any callable intervals to concrete floats.

    Called once after settings are resolved.  Replaces
    ``_TelemetryRegistration`` entries that have callable intervals
    with new frozen instances containing the resolved float value.
    Mutates *telemetry_list* in place.

    Raises:
        ValueError: If a resolved interval is zero or negative.
    """
    import dataclasses

    for i, reg in enumerate(telemetry_list):
        if callable(reg.interval):
            resolved = reg.interval(settings)
            if resolved <= 0:
                msg = (
                    f"Telemetry interval for {reg.name!r} must be "
                    f"positive, got {resolved}"
                )
                raise ValueError(msg)
            telemetry_list[i] = dataclasses.replace(reg, interval=resolved)


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


# ---------------------------------------------------------------------------
# Phase 2: Wire
# ---------------------------------------------------------------------------


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


def build_contexts(
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
    settings: Settings,
    mqtt: MqttPort,
    prefix: str,
    shutdown_event: asyncio.Event,
    adapters: dict[type, object],
    clock: ClockPort,
) -> dict[str, DeviceContext]:
    """Build a DeviceContext for every registered device.

    When a telemetry and command registration share the same name
    (scoped name uniqueness), only one :class:`DeviceContext` is
    created for that name — they share a single context.
    """
    contexts: dict[str, DeviceContext] = {}
    for reg in all_registrations:
        if reg.name not in contexts:
            contexts[reg.name] = DeviceContext(
                name=reg.name,
                settings=settings,
                mqtt=mqtt,
                topic_prefix=prefix,
                shutdown_event=shutdown_event,
                adapters=adapters,
                clock=clock,
                is_root=reg.is_root,
            )
    return contexts


async def wire_router(
    devices: list[_DeviceRegistration],
    commands: list[_CommandRegistration],
    store: Store | None,
    contexts: dict[str, DeviceContext],
    prefix: str,
    error_publisher: ErrorPublisher,
) -> TopicRouter:
    """Create a TopicRouter and register command-handler proxies."""
    cmd_runner = CommandRunner(store=store)
    router = TopicRouter(topic_prefix=prefix)
    for reg in devices:
        CommandRunner.register_device_proxy(
            reg, contexts[reg.name], error_publisher, router
        )
    for cmd_reg in commands:
        await cmd_runner.register_command_proxy(
            cmd_reg, contexts[cmd_reg.name], error_publisher, router
        )
    return router


async def subscribe_and_connect(
    mqtt: MqttPort,
    router: TopicRouter,
) -> None:
    """Subscribe to command topics and wire message handler."""
    for topic in router.subscriptions:
        await mqtt.subscribe(topic)
    if isinstance(mqtt, MqttMessageHandler):
        mqtt.on_message(router.route)


# ---------------------------------------------------------------------------
# Phase 3: Run
# ---------------------------------------------------------------------------


def start_device_tasks(
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    store: Store | None,
    contexts: dict[str, DeviceContext],
    error_publisher: ErrorPublisher,
    health_reporter: HealthReporter,
) -> list[asyncio.Task[None]]:
    """Create asyncio tasks for all registered devices."""
    runner = TelemetryRunner(store=store)
    tasks: list[asyncio.Task[None]] = []
    for dev_reg in devices:
        tasks.append(
            asyncio.create_task(
                runner.run_device(
                    dev_reg,
                    contexts[dev_reg.name],
                    error_publisher,
                ),
            ),
        )
    # Partition telemetry by group
    groups: dict[str, list[_TelemetryRegistration]] = {}
    for tel_reg in telemetry:
        if tel_reg.group is None:
            # Ungrouped — independent task (unchanged behavior)
            tasks.append(
                asyncio.create_task(
                    runner.run_telemetry(
                        tel_reg,
                        contexts[tel_reg.name],
                        error_publisher,
                        health_reporter,
                    ),
                ),
            )
        else:
            groups.setdefault(tel_reg.group, []).append(tel_reg)

    # Create one scheduler task per coalescing group
    for group_name, group_regs in groups.items():
        tasks.append(
            asyncio.create_task(
                runner.run_telemetry_group(
                    group_name,
                    group_regs,
                    contexts,
                    error_publisher,
                    health_reporter,
                ),
            ),
        )

    return tasks


def start_heartbeat_task(
    heartbeat_interval: float | None,
    health_reporter: HealthReporter,
) -> asyncio.Task[None] | None:
    """Start the periodic heartbeat background task, if enabled.

    Returns ``None`` when *heartbeat_interval* is ``None``
    (heartbeats disabled).
    """
    if heartbeat_interval is None:
        return None
    return asyncio.create_task(
        heartbeat_loop(health_reporter, heartbeat_interval),
    )


async def heartbeat_loop(
    health_reporter: HealthReporter,
    interval: float,
) -> None:
    """Publish heartbeats at a fixed interval until cancelled.

    The loop sleeps *first*, then publishes — the initial heartbeat
    is published separately before this task starts so there is no
    delay on startup.  ``publish_heartbeat()`` is fire-and-forget
    (errors are logged, never propagated).

    Uses ``health_reporter.clock.sleep()`` so that :class:`FakeClock`
    can accelerate heartbeat timing in tests.
    """
    while True:
        await health_reporter.clock.sleep(interval)
        await health_reporter.publish_heartbeat()


async def cancel_tasks(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel device tasks and wait for graceful completion."""
    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception) and not isinstance(
            result,
            asyncio.CancelledError,
        ):
            logger.error("Task error during shutdown: %s", result)


async def run_lifespan_and_devices(
    lifespan: LifespanFunc,
    store: Store | None,
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    heartbeat_interval: float | None,
    resolved_settings: Settings,
    resolved_adapters: dict[type, object],
    health_reporter: HealthReporter,
    error_publisher: ErrorPublisher,
    contexts: dict[str, DeviceContext],
    shutdown_event: asyncio.Event,
) -> None:
    """Enter lifespan, run devices, and tear down.

    Startup errors in the lifespan propagate immediately,
    preventing device launch.  Teardown errors are logged but
    do not mask device errors.
    """
    app_context = AppContext(
        settings=resolved_settings,
        adapters=resolved_adapters,
    )

    lifespan_cm = lifespan(app_context)
    await lifespan_cm.__aenter__()

    try:
        await health_reporter.publish_heartbeat()
        heartbeat_task = start_heartbeat_task(heartbeat_interval, health_reporter)

        device_tasks = start_device_tasks(
            devices, telemetry, store, contexts, error_publisher, health_reporter
        )

        await shutdown_event.wait()

        # --- Phase 4: Tear down ---
        await cancel_tasks(device_tasks)
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
    finally:
        exc_info = sys.exc_info()
        try:
            await lifespan_cm.__aexit__(*exc_info)
        except Exception:
            logger.exception("Lifespan teardown error")
        finally:
            del exc_info  # avoid reference cycle (PEP 3110)
