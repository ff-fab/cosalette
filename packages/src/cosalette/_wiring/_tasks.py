"""Task lifecycle and main run-loop functions."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Any

from cosalette._clock import ClockPort
from cosalette._context import AppContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthCheckRunner, HealthReporter
from cosalette._persistence._stores import Store
from cosalette._registration import (
    LifespanFunc,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._telemetry_runner import TelemetryRunner, _TriggerSlot
from cosalette._settings import Settings
from cosalette._wiring._task_lifecycle import (
    DeviceTaskMap,
    _build_periodic_providers,
    _cancel_phase_tasks,
    _exit_restartable_adapters,
    _validate_lifespan_state,
    cancel_tasks,
    cancel_tasks_for_adapter,
    start_device_tasks_for_names,
    start_health_check_task,
    start_heartbeat_task,
    start_periodic_tasks,
    start_stream_tasks,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cosalette._periodic import _PeriodicRegistration
    from cosalette._wiring._context import DeviceInfo

logger = logging.getLogger("cosalette._wiring")


def start_device_tasks(
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    store: Store | None,
    contexts: dict[str, Any],  # DeviceContext
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
    contexts: dict[str, Any],  # DeviceContext
    shutdown_event: asyncio.Event,
    *,
    health_check_runner: HealthCheckRunner | None = None,
    restart_cooldown: float = 5.0,
    adapter_device_map: dict[type, list[DeviceInfo]] | None = None,
    resolved_clock: ClockPort | None = None,
    restartable_adapters: list[object] | None = None,
    trigger_slots: dict[str, _TriggerSlot] | None = None,
    periodic: Sequence[_PeriodicRegistration] = (),
    stream_list: Sequence[_StreamRegistration] = (),
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

        # Build providers for periodic tasks and spawn them
        periodic_providers = _build_periodic_providers(
            resolved_settings, resolved_adapters, lifespan_state, resolved_clock
        )
        periodic_tasks = start_periodic_tasks(periodic, periodic_providers)

        stream_tasks = start_stream_tasks(
            stream_list, resolved_adapters, periodic_providers, shutdown_event
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
        await _cancel_phase_tasks(
            device_tasks,
            health_check_task,
            heartbeat_task,
            periodic_tasks,
            stream_tasks=stream_tasks,
        )
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
