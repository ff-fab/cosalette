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
import dataclasses
import inspect
import logging
import signal
import sys
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple

from cosalette._clock import ClockPort
from cosalette._command_runner import CommandRunner
from cosalette._context import AppContext, DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthCheckRunner, HealthReporter, build_will_config
from cosalette._injection import (
    KNOWN_INJECTABLE_TYPES,
    build_injection_plan,
    resolve_kwargs,
)
from cosalette._mqtt import MqttClient, MqttMessageHandler, MqttPort
from cosalette._registration import (
    IntervalSpec,
    LifespanFunc,
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
    validate_mqtt_name,
)
from cosalette._router import TopicRouter
from cosalette._settings import Settings
from cosalette._stores import Store
from cosalette._telemetry_runner import TelemetryRunner, _TriggerSlot

if TYPE_CHECKING:
    from cosalette._app import App

from cosalette._json import dumps as _json_dumps

logger = logging.getLogger(__name__)

_REGISTRY_PAYLOAD_WARN_BYTES = 131_072  # 128 KiB

DeviceTaskMap = dict[str, list[asyncio.Task[None]]]
"""Maps device name → list of asyncio tasks for that device."""


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


def resolve_store_factory(
    factory: Callable[..., Store],
    settings: Settings,
    adapters: dict[type, object],
) -> Store:
    """Invoke a store factory with signature-based DI.

    Called during bootstrap after settings and adapters are resolved
    but before configure hooks run.  The factory receives whichever
    DI providers its signature requests (Settings subclass, adapter
    ports, etc.).

    Raises:
        TypeError: If the factory is async or returns a non-Store object.
    """
    import inspect

    if inspect.iscoroutinefunction(factory):
        msg = (
            f"store factory {factory!r} is async; store factories must be "
            f"synchronous (bootstrap runs before the async event loop starts)"
        )
        raise TypeError(msg)

    providers: dict[type, Any] = {Settings: settings}
    settings_type = type(settings)
    if settings_type is not Settings:
        providers[settings_type] = settings
    for port_type, instance in adapters.items():
        providers[port_type] = instance

    plan = build_injection_plan(factory)
    kwargs = resolve_kwargs(plan, providers) if plan else {}
    result = factory(**kwargs)

    if not isinstance(result, Store):
        msg = (
            f"store factory {factory!r} returned {type(result).__name__!r}, "
            f"expected a Store instance"
        )
        raise TypeError(msg)
    return result


def _build_configure_providers(
    settings: Settings,
    adapters: dict[type, object],
    clock: ClockPort,
) -> dict[type, Any]:
    """Build the DI providers map for on_configure hooks."""
    providers: dict[type, Any] = {
        Settings: settings,
        logging.Logger: logging.getLogger("cosalette.configure"),
        ClockPort: clock,
    }
    settings_type = type(settings)
    if settings_type is not Settings:
        providers[settings_type] = settings
    for port_type, instance in adapters.items():
        providers[port_type] = instance
    return providers


async def run_configure_hooks(
    hooks: list[Callable[..., Any]],
    settings: Settings,
    adapters: dict[type, object],
    clock: ClockPort,
) -> None:
    """Execute on_configure hooks with dependency injection."""
    if not hooks:
        return
    providers = _build_configure_providers(settings, adapters, clock)
    for hook in hooks:
        plan = build_injection_plan(hook)
        kwargs = resolve_kwargs(plan, providers)
        if inspect.iscoroutinefunction(hook):
            await hook(**kwargs)
        else:
            hook(**kwargs)


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
    for i, reg in enumerate(telemetry_list):
        if callable(reg.interval):
            resolved = reg.interval(settings)  # ty: ignore[call-top-callable]
            if resolved <= 0:
                msg = (
                    f"Telemetry interval for {reg.name!r} must be "
                    f"positive, got {resolved}"
                )
                raise ValueError(msg)
            telemetry_list[i] = dataclasses.replace(reg, interval=resolved)


# ---------------------------------------------------------------------------
# Name-spec expansion
# ---------------------------------------------------------------------------


def _validate_config_type(config: Any) -> None:
    """Reject per-device config whose type shadows a framework injectable."""
    if config is None:
        return
    config_type = type(config)
    if config_type in KNOWN_INJECTABLE_TYPES:
        msg = (
            f"Dict-name config type {config_type.__name__!r} shadows "
            f"a framework-provided type"
        )
        raise TypeError(msg)


def _evaluate_name_spec(
    name_spec: Callable[..., Any],
    settings: Settings,
    qualname: str,
) -> list[tuple[str, Any]]:
    """Evaluate a name-spec callable, returning (name, config|None) pairs."""
    result = name_spec(settings)
    if isinstance(result, dict):
        if not result:
            logger.warning("Dict-name callable returned empty dict for %s", qualname)
        for config in result.values():
            _validate_config_type(config)
        pairs = list(result.items())
    elif isinstance(result, list):
        if not result:
            logger.warning("List-name callable returned empty list for %s", qualname)
        pairs = [(name, None) for name in result]
    else:
        msg = f"name= callable must return dict or list, got {type(result).__name__}"
        raise TypeError(msg)

    for name, _ in pairs:
        if not isinstance(name, str):
            msg = f"name= callable must return str keys, got {type(name).__name__!r}"
            raise TypeError(msg)
        validate_mqtt_name(name)
    return pairs  # ty: ignore[invalid-return-type]


def _resolve_per_device_interval(
    reg: _TelemetryRegistration,
    dev_name: str,
    config: Any,
) -> IntervalSpec:
    """Resolve a callable interval for a single dict-name entry."""
    interval = reg.interval
    if not callable(interval) or config is None:
        return interval
    if reg.group is not None:
        msg = f"Per-device interval (callable) cannot be used with group={reg.group!r}"
        raise ValueError(msg)
    interval = interval(config)  # ty: ignore[call-top-callable]
    if interval <= 0:
        msg = f"Per-device interval for {dev_name!r} must be positive, got {interval}"
        raise ValueError(msg)
    return interval


def _expand_telemetry_names(
    telemetry: list[_TelemetryRegistration],
    settings: Settings,
) -> None:
    """Expand callable name specs in telemetry registrations."""
    expanded: list[_TelemetryRegistration] = []
    for reg in telemetry:
        if reg.name_spec is None:
            expanded.append(reg)
            continue
        for dev_name, config in _evaluate_name_spec(
            reg.name_spec,
            settings,
            reg.func.__qualname__,  # ty: ignore[unresolved-attribute]
        ):
            interval = _resolve_per_device_interval(reg, dev_name, config)
            expanded.append(
                dataclasses.replace(
                    reg,
                    name=dev_name,
                    interval=interval,
                    per_device_config=config,
                    name_spec=None,
                )
            )
    telemetry.clear()
    telemetry.extend(expanded)


def _expand_device_names(
    devices: list[_DeviceRegistration],
    settings: Settings,
) -> None:
    """Expand callable name specs in device registrations."""
    expanded: list[_DeviceRegistration] = []
    for reg in devices:
        if reg.name_spec is None:
            expanded.append(reg)
            continue
        for dev_name, config in _evaluate_name_spec(
            reg.name_spec,
            settings,
            reg.func.__qualname__,  # ty: ignore[unresolved-attribute]
        ):
            expanded.append(
                dataclasses.replace(
                    reg,
                    name=dev_name,
                    per_device_config=config,
                    name_spec=None,
                )
            )
    devices.clear()
    devices.extend(expanded)


def _expand_command_names(
    commands: list[_CommandRegistration],
    settings: Settings,
) -> None:
    """Expand callable name specs in command registrations."""
    expanded: list[_CommandRegistration] = []
    for reg in commands:
        if reg.name_spec is None:
            expanded.append(reg)
            continue
        for dev_name, config in _evaluate_name_spec(
            reg.name_spec,
            settings,
            reg.func.__qualname__,  # ty: ignore[unresolved-attribute]
        ):
            expanded.append(
                dataclasses.replace(
                    reg,
                    name=dev_name,
                    per_device_config=config,
                    name_spec=None,
                )
            )
    commands.clear()
    commands.extend(expanded)


def _check_is_root_consistency(
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
) -> None:
    """Shared tel↔cmd names must agree on is_root (MQTT namespace check)."""
    for tel_reg in telemetry:
        for cmd_reg in commands:
            if tel_reg.name == cmd_reg.name and tel_reg.is_root != cmd_reg.is_root:
                msg = (
                    f"Cannot share name '{tel_reg.name}' between root and named "
                    f"registrations — MQTT topic namespaces would conflict"
                )
                raise ValueError(msg)


def _check_expanded_duplicates(
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
) -> None:
    """Check for name collisions after dict/list expansion."""
    # Device names collide with everything
    device_set: set[str] = set()
    for reg in devices:
        name = reg.name
        if name in device_set:
            msg = f"Device name '{name}' is already registered"
            raise ValueError(msg)
        device_set.add(name)

    # Telemetry names must be unique within telemetry + not collide with devices
    telem_set: set[str] = set()
    for tel_reg in telemetry:
        name = tel_reg.name
        if name in device_set or name in telem_set:
            msg = f"Device name '{name}' is already registered"
            raise ValueError(msg)
        telem_set.add(name)

    # Command names must be unique within commands + not collide with devices
    cmd_set: set[str] = set()
    for cmd_reg in commands:
        name = cmd_reg.name
        if name in device_set or name in cmd_set:
            msg = f"Device name '{name}' is already registered"
            raise ValueError(msg)
        cmd_set.add(name)

    _check_is_root_consistency(telemetry, commands)


def expand_name_specs(
    telemetry: list[_TelemetryRegistration],
    devices: list[_DeviceRegistration],
    commands: list[_CommandRegistration],
    settings: Settings,
) -> None:
    """Expand callable name= specs into concrete registrations."""
    _expand_telemetry_names(telemetry, settings)
    _expand_device_names(devices, settings)
    _expand_command_names(commands, settings)
    _check_expanded_duplicates(devices, telemetry, commands)


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


async def publish_registry_snapshot(
    app: App,
    mqtt: MqttPort,
    prefix: str,
) -> None:
    """Publish a registry snapshot to MQTT (fire-and-forget).

    Serializes the full registry introspection snapshot as compact JSON
    and publishes it as a retained message to ``{prefix}/_meta/registry``.
    Errors are logged but never propagated.

    .. warning:: Security

       The snapshot includes function qualnames, adapter class names,
       injection plans, and telemetry intervals.  In shared-broker
       deployments consider protecting ``_meta/#`` with broker ACLs.
    """
    from cosalette._introspect import build_registry_snapshot

    topic = f"{prefix}/_meta/registry"
    try:
        snapshot = build_registry_snapshot(app)
        payload_size = len(_json_dumps(snapshot).encode("utf-8"))
        if payload_size > _REGISTRY_PAYLOAD_WARN_BYTES:
            logger.warning(
                "Registry snapshot payload is %d bytes (threshold %d); "
                "large payloads may exceed broker max_packet_size limits",
                payload_size,
                _REGISTRY_PAYLOAD_WARN_BYTES,
            )
        await mqtt.publish(topic, snapshot, retain=True, qos=1)
    except Exception:
        logger.exception("Failed to publish registry snapshot to %s", topic)


class DeviceInfo(NamedTuple):
    """Device name paired with its root status for availability routing."""

    name: str
    is_root: bool


def build_adapter_device_map(
    all_registrations: list[
        _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
    ],
    resolved_adapters: dict[type, object],
) -> dict[type, list[DeviceInfo]]:
    """Map each adapter port type to the devices that depend on it.

    Scans each registration's ``injection_plan`` to find adapter port
    types (types present in *resolved_adapters* but not in
    ``KNOWN_INJECTABLE_TYPES``).  Returns a mapping from adapter port
    type to a list of ``DeviceInfo(name, is_root)`` tuples.

    A device name appears at most once per adapter type, even when
    telemetry and command registrations share a name (scoped uniqueness).
    """
    adapter_types = set(resolved_adapters) - set(KNOWN_INJECTABLE_TYPES)
    result: dict[type, list[DeviceInfo]] = {t: [] for t in adapter_types}
    seen: dict[type, set[str]] = {t: set() for t in adapter_types}

    for reg in all_registrations:
        for _, param_type in reg.injection_plan:
            if param_type in adapter_types and reg.name not in seen[param_type]:
                seen[param_type].add(reg.name)
                result[param_type].append(DeviceInfo(reg.name, reg.is_root))

    return result


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
    trigger_slots: dict[str, _TriggerSlot] | None = None,
    telemetry: list[_TelemetryRegistration] | None = None,
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

    # Register triggerable telemetry proxies
    if trigger_slots and telemetry:
        for tel_reg in telemetry:
            if tel_reg.triggerable and tel_reg.name in trigger_slots:
                slot = trigger_slots[tel_reg.name]
                _register_trigger_proxy(tel_reg, slot, prefix, router)

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


def create_trigger_slots(
    telemetry: list[_TelemetryRegistration],
) -> dict[str, _TriggerSlot]:
    """Create trigger slots for all triggerable telemetry registrations."""
    slots: dict[str, _TriggerSlot] = {}
    for reg in telemetry:
        if reg.triggerable:
            slots[reg.name] = _TriggerSlot(event=asyncio.Event())
    return slots


def _register_trigger_proxy(
    reg: _TelemetryRegistration,
    slot: _TriggerSlot,
    prefix: str,
    router: TopicRouter,
) -> None:
    """Register a trigger command proxy for a triggerable telemetry device."""
    expected_topic = f"{prefix}/{reg.name}/set"

    async def _trigger_proxy(
        topic: str,
        payload: str,
        _slot: _TriggerSlot = slot,
        _name: str = reg.name,
        _expected: str = expected_topic,
    ) -> None:
        # Reject sub-topic triggers — only the exact device /set topic is valid.
        if topic != _expected:
            logger.debug(
                "Trigger ignored for '%s': unexpected topic '%s' (expected '%s')",
                _name,
                topic,
                _expected,
            )
            return
        if _slot.event.is_set():
            logger.debug("Trigger coalesced for '%s', run already pending", _name)
        else:
            logger.debug("Trigger received for '%s', scheduling immediate run", _name)
        _slot.arm(payload)  # raw string stored; JSON parsed lazily in consume()

    router.register(reg.name, _trigger_proxy, is_root=reg.is_root)


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
    trigger_slots: dict[str, _TriggerSlot] | None = None,
) -> tuple[list[asyncio.Task[None]], DeviceTaskMap]:
    """Create asyncio tasks for all registered devices.

    Returns a flat task list (for shutdown) and a name→tasks map
    (for per-adapter cancellation during restart).
    """
    runner = TelemetryRunner(store=store)
    tasks: list[asyncio.Task[None]] = []
    task_map: DeviceTaskMap = {}
    for dev_reg in devices:
        task = asyncio.create_task(
            runner.run_device(
                dev_reg,
                contexts[dev_reg.name],
                error_publisher,
            ),
            name=f"device:{dev_reg.name}",
        )
        tasks.append(task)
        task_map.setdefault(dev_reg.name, []).append(task)
    # Partition telemetry by group
    groups: dict[str, list[_TelemetryRegistration]] = {}
    for tel_reg in telemetry:
        if tel_reg.group is None:
            # Ungrouped — independent task (unchanged behavior)
            trigger_slot = trigger_slots.get(tel_reg.name) if trigger_slots else None
            task = asyncio.create_task(
                runner.run_telemetry(
                    tel_reg,
                    contexts[tel_reg.name],
                    error_publisher,
                    health_reporter,
                    trigger_slot=trigger_slot,
                ),
                name=f"device:{tel_reg.name}",
            )
            tasks.append(task)
            task_map.setdefault(tel_reg.name, []).append(task)
        else:
            groups.setdefault(tel_reg.group, []).append(tel_reg)

    # Create one scheduler task per coalescing group
    for group_name, group_regs in groups.items():
        task = asyncio.create_task(
            runner.run_telemetry_group(
                group_name,
                group_regs,
                contexts,
                error_publisher,
                health_reporter,
            ),
            name=f"group:{group_name}",
        )
        tasks.append(task)
        # Map each member device to this group task
        for gr in group_regs:
            task_map.setdefault(gr.name, []).append(task)

    return tasks, task_map


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


def _is_shared_task(
    task: asyncio.Task[None],
    device_task_map: DeviceTaskMap,
    adapter_names: set[str],
) -> bool:
    """Return ``True`` if *task* is still referenced by a non-adapter device."""
    return any(
        task in tasks
        for name, tasks in device_task_map.items()
        if name not in adapter_names
    )


async def cancel_tasks_for_adapter(
    device_task_map: DeviceTaskMap,
    adapter_device_map: dict[type, list[DeviceInfo]],
    adapter_type: type,
) -> tuple[list[str], list[asyncio.Task[None]]]:
    """Cancel tasks for devices that depend on a specific adapter.

    Shared group tasks (referenced by devices of other adapters) are
    NOT cancelled — they are returned separately so the caller can
    cancel them after recreating replacement tasks.

    Returns (cancelled_device_names, deferred_group_tasks).
    """
    device_infos = adapter_device_map.get(adapter_type, [])
    adapter_names = {info.name for info in device_infos}
    cancelled: list[str] = []
    tasks_to_cancel: list[asyncio.Task[None]] = []
    deferred: list[asyncio.Task[None]] = []
    seen_deferred: set[int] = set()

    for info in device_infos:
        name = info.name
        # pop() must precede _is_shared_task — removing the current
        # device first ensures shared-check only finds *other* devices.
        tasks = device_task_map.pop(name, [])
        if not tasks:
            continue
        cancelled.append(name)
        for task in tasks:
            if _is_shared_task(task, device_task_map, adapter_names):
                if id(task) not in seen_deferred:
                    deferred.append(task)
                    seen_deferred.add(id(task))
            else:
                tasks_to_cancel.append(task)

    if tasks_to_cancel:
        await cancel_tasks(tasks_to_cancel)

    return cancelled, deferred


def _expand_group_members(
    names: set[str],
    telemetry: list[_TelemetryRegistration],
) -> set[str]:
    """Expand *names* to include all members of overlapping coalescing groups."""
    affected = {t.group for t in telemetry if t.group is not None and t.name in names}
    return names | {t.name for t in telemetry if t.group in affected}


def start_device_tasks_for_names(
    device_names: list[str],
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    store: Store | None,
    contexts: dict[str, DeviceContext],
    error_publisher: ErrorPublisher,
    health_reporter: HealthReporter,
) -> tuple[list[asyncio.Task[None]], DeviceTaskMap]:
    """Start device tasks only for the specified device names.

    For coalescing groups, if any member device is in *device_names*,
    the entire group is recreated so the shared scheduler covers all
    members.
    """
    names = set(device_names)
    expanded = _expand_group_members(names, telemetry)

    # Only restart device handlers for the originally-requested names;
    # telemetry is expanded to cover full coalescing groups.
    filtered_devices = [d for d in devices if d.name in names]
    filtered_telemetry = [t for t in telemetry if t.name in expanded]
    return start_device_tasks(
        filtered_devices,
        filtered_telemetry,
        store,
        contexts,
        error_publisher,
        health_reporter,
    )


def start_health_check_task(
    health_check_runner: HealthCheckRunner | None,
) -> asyncio.Task[None] | None:
    """Start the periodic health check background task, if enabled.

    Returns ``None`` when health checks are disabled (no runner provided).
    """
    if health_check_runner is None:
        return None
    return asyncio.create_task(health_check_runner.run_loop())


def _validate_lifespan_state(
    lifespan_state: object,
    resolved_adapters: dict[type, object],
    resolved_settings: Settings,
) -> None:
    if lifespan_state is None:
        return
    state_type = type(lifespan_state)
    if state_type in resolved_adapters:
        msg = (
            f"Lifespan yielded type {state_type.__qualname__!r} conflicts "
            f"with existing DI registration"
        )
        raise RuntimeError(msg)
    if state_type in KNOWN_INJECTABLE_TYPES or state_type is type(resolved_settings):
        msg = (
            f"Lifespan yielded type {state_type.__qualname__!r} conflicts "
            f"with framework-provided injectable type"
        )
        raise RuntimeError(msg)
    resolved_adapters[state_type] = lifespan_state


async def _cancel_phase_tasks(
    device_tasks: list[asyncio.Task[None]],
    health_check_task: asyncio.Task[None] | None,
    heartbeat_task: asyncio.Task[None] | None,
) -> None:
    await cancel_tasks(device_tasks)
    if health_check_task is not None:
        health_check_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await health_check_task
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


async def _exit_restartable_adapters(
    restartable_adapters: list[object] | None,
) -> None:
    if not restartable_adapters:
        return
    from cosalette._adapter_lifecycle import exit_single_adapter

    for ra in restartable_adapters:
        try:
            await exit_single_adapter(ra)
        except Exception:
            logger.exception(
                "Error exiting restartable adapter %s",
                type(ra).__name__,
            )


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
    *,
    health_check_runner: HealthCheckRunner | None = None,
    restart_cooldown: float = 5.0,
    adapter_device_map: dict[type, list[DeviceInfo]] | None = None,
    resolved_clock: ClockPort | None = None,
    restartable_adapters: list[object] | None = None,
    trigger_slots: dict[str, _TriggerSlot] | None = None,
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
    lifespan_state = await lifespan_cm.__aenter__()

    try:
        _validate_lifespan_state(lifespan_state, resolved_adapters, resolved_settings)

        if health_check_runner is not None:
            await health_check_runner.run_startup_checks()

        await health_reporter.publish_heartbeat()
        heartbeat_task = start_heartbeat_task(heartbeat_interval, health_reporter)
        health_check_task = start_health_check_task(health_check_runner)

        device_tasks, device_task_map = start_device_tasks(
            devices,
            telemetry,
            store,
            contexts,
            error_publisher,
            health_reporter,
            trigger_slots=trigger_slots,
        )

        # Wire restart callback now that mutable task state exists
        if (
            health_check_runner is not None
            and adapter_device_map is not None
            and resolved_clock is not None
        ):
            from cosalette._adapter_lifecycle import restart_single_adapter

            async def _on_restart(adapter_type: type, adapter: object) -> bool:
                cancelled, deferred_tasks = await cancel_tasks_for_adapter(
                    device_task_map, adapter_device_map, adapter_type
                )
                success = await restart_single_adapter(
                    adapter, restart_cooldown, resolved_clock, shutdown_event
                )
                if not success:
                    # Leave deferred group tasks running — they still
                    # serve healthy adapters' devices.
                    return False
                check = getattr(adapter, "health_check", None)
                if check and not await check():
                    return False
                new_tasks, new_map = start_device_tasks_for_names(
                    cancelled,
                    devices,
                    telemetry,
                    store,
                    contexts,
                    error_publisher,
                    health_reporter,
                )
                device_tasks.extend(new_tasks)
                device_task_map.update(new_map)
                # Cancel old deferred group tasks — new ones replace them
                if deferred_tasks:
                    await cancel_tasks(deferred_tasks)
                # GC: prune all done tasks — cancelled adapter tasks,
                # cancelled deferred tasks, and any naturally-finished
                # tasks from other adapters.  Prevents unbounded list
                # growth across restart cycles.
                device_tasks[:] = [t for t in device_tasks if not t.done()]
                return True

            health_check_runner._on_restart_needed = _on_restart

        await shutdown_event.wait()

        # --- Phase 4: Tear down ---
        await _cancel_phase_tasks(device_tasks, health_check_task, heartbeat_task)
    finally:
        # Exit restartable adapters (managed outside AsyncExitStack)
        await _exit_restartable_adapters(restartable_adapters)
        exc_info = sys.exc_info()
        try:
            await lifespan_cm.__aexit__(*exc_info)
        except Exception:
            logger.exception("Lifespan teardown error")
        finally:
            del exc_info  # avoid reference cycle (PEP 3110)
            # Remove lifespan-yielded state from DI on teardown
            if lifespan_state is not None:
                resolved_adapters.pop(type(lifespan_state), None)
