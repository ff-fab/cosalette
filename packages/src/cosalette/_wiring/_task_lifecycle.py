"""Task lifecycle helpers: creation, cancellation, and adapter restart."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from cosalette._clock import ClockPort
from cosalette._health import HealthCheckRunner, HealthReporter
from cosalette._injection import KNOWN_INJECTABLE_TYPES
from cosalette._periodic import _PeriodicRegistration, run_periodic
from cosalette._registration import (
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._stream_runner import run_stream
from cosalette._settings import Settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cosalette._errors import ErrorPublisher
    from cosalette._runners._telemetry_runner import TelemetryRunner, _TriggerSlot
    from cosalette._wiring._context import DeviceInfo

logger = logging.getLogger("cosalette._wiring")

DeviceTaskMap = dict[str, list[asyncio.Task[None]]]
"""Maps device name → list of asyncio tasks for that device."""


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


def _build_periodic_providers(
    resolved_settings: Settings,
    resolved_adapters: dict[type, object],
    lifespan_state: Any,
    resolved_clock: ClockPort | None = None,
) -> dict[type, Any]:
    """Build a DI provider map for periodic task handlers.

    Includes all resolved adapters, the settings instance (registered
    under every Settings base class for subclass-aware injection), the
    clock port, and any lifespan-yielded state object.
    """
    providers: dict[type, Any] = {**resolved_adapters}
    for cls in type(resolved_settings).__mro__:
        if isinstance(cls, type) and issubclass(cls, Settings):
            providers[cls] = resolved_settings
    if lifespan_state is not None:
        providers[type(lifespan_state)] = lifespan_state
    if resolved_clock is not None:
        providers[ClockPort] = resolved_clock
    return providers


def start_periodic_tasks(
    periodic: Sequence[_PeriodicRegistration],
    providers: dict[type, Any],
) -> list[asyncio.Task[None]]:
    """Create asyncio tasks for all registered periodic handlers.

    Args:
        periodic: Resolved periodic registrations (intervals are floats).
        providers: DI provider map passed to each :func:`run_periodic` call.

    Returns:
        Flat list of running tasks (for shutdown cancellation).
    """
    tasks: list[asyncio.Task[None]] = []
    for reg in periodic:
        task_providers = {
            **providers,
            logging.Logger: logging.getLogger(f"cosalette.periodic.{reg.name}"),
        }
        task = asyncio.create_task(
            run_periodic(reg, task_providers),
            name=f"periodic:{reg.name}",
        )
        tasks.append(task)
    return tasks


def start_stream_tasks(
    streams: Sequence[_StreamRegistration],
    resolved_adapters: dict[type, object],
    providers: dict[type, Any],
    shutdown_event: asyncio.Event,
) -> list[asyncio.Task[None]]:
    """Create asyncio tasks for all registered stream handlers."""
    tasks: list[asyncio.Task[None]] = []
    for reg in streams:
        stream_providers = {
            **providers,
            logging.Logger: logging.getLogger(f"cosalette.stream.{reg.name}"),
        }
        task = asyncio.create_task(
            run_stream(reg, resolved_adapters, stream_providers, shutdown_event),
            name=f"stream:{reg.name}",
        )
        tasks.append(task)
    return tasks


async def cancel_periodic_tasks(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel periodic tasks and wait up to 5 s for graceful completion.

    Uses a grace period so handlers that are mid-execution get a chance
    to finish their current cycle cleanly.
    """
    for task in tasks:
        task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=5.0,
        )
    except TimeoutError:
        still_running = sum(1 for t in tasks if not t.done())
        logger.warning(
            "%d periodic task(s) did not finish within 5 s grace period",
            still_running,
        )


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
    store: Any,  # Store | None
    contexts: dict[str, Any],
    error_publisher: Any,  # ErrorPublisher
    health_reporter: HealthReporter,
) -> tuple[list[asyncio.Task[None]], DeviceTaskMap]:
    """Start device tasks only for the specified device names.

    For coalescing groups, if any member device is in *device_names*,
    the entire group is recreated so the shared scheduler covers all
    members.
    """
    from cosalette._wiring._tasks import start_device_tasks

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
    periodic_tasks: list[asyncio.Task[None]] | None = None,
    stream_tasks: list[asyncio.Task[None]] | None = None,
) -> None:
    await cancel_tasks(device_tasks)
    if periodic_tasks:
        await cancel_periodic_tasks(periodic_tasks)
    if stream_tasks:
        await cancel_tasks(stream_tasks)
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


def _start_telemetry_tasks(
    runner: TelemetryRunner,
    telemetry: list[_TelemetryRegistration],
    contexts: dict[str, Any],
    error_publisher: ErrorPublisher,
    health_reporter: HealthReporter,
    trigger_slots: dict[str, _TriggerSlot] | None,
    tasks: list[asyncio.Task[None]],
    task_map: DeviceTaskMap,
) -> None:
    """Create asyncio tasks for all telemetry registrations, including groups.

    Ungrouped registrations each get their own task; grouped registrations
    share a single scheduler task per group.  Mutates *tasks* and *task_map*
    in place.
    """
    groups: dict[str, list[_TelemetryRegistration]] = {}
    for tel_reg in telemetry:
        if tel_reg.group is None:
            trigger_slot = trigger_slots.get(tel_reg.name) if trigger_slots else None
            task = asyncio.create_task(
                runner.run_telemetry(
                    tel_reg,
                    contexts[tel_reg.name],
                    error_publisher,
                    health_reporter,
                    trigger_slot=trigger_slot,
                ),
                name=f"telemetry:{tel_reg.name}",
            )
            tasks.append(task)
            task_map.setdefault(tel_reg.name, []).append(task)
        else:
            groups.setdefault(tel_reg.group, []).append(tel_reg)
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
        for gr in group_regs:
            task_map.setdefault(gr.name, []).append(task)


def wire_restart_callback(
    health_check_runner: HealthCheckRunner | None,
    adapter_device_map: dict[type, list[DeviceInfo]] | None,
    resolved_clock: ClockPort | None,
    device_task_map: DeviceTaskMap,
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    store: Any,
    contexts: dict[str, Any],
    error_publisher: ErrorPublisher,
    health_reporter: HealthReporter,
    restart_cooldown: float,
    shutdown_event: asyncio.Event,
    device_tasks: list[asyncio.Task[None]],
) -> None:
    """Wire the adaptive restart callback onto *health_check_runner*.

    A no-op when any of the three required restart prerequisites
    (*health_check_runner*, *adapter_device_map*, *resolved_clock*)
    is ``None``.
    """
    if (
        health_check_runner is None
        or adapter_device_map is None
        or resolved_clock is None
    ):
        return

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
        # GC: prune all done tasks across restart cycles
        device_tasks[:] = [t for t in device_tasks if not t.done()]
        return True

    health_check_runner._on_restart_needed = _on_restart
