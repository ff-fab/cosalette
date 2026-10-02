"""Task lifecycle and main run-loop functions."""

from __future__ import annotations

import asyncio
import functools
import logging
import sys
from typing import TYPE_CHECKING, Any

from cosalette._clock import ClockPort, SystemClock
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
from cosalette._runners._telemetry_types import _ReconnectWake
from cosalette._settings import Settings
from cosalette._wiring._infra import await_first_connect
from cosalette._wiring._supervision import (
    adapter_exhausted_check,
    prune_done,
    supervise_entity_tasks,
)
from cosalette._wiring._task_lifecycle import (
    DeviceTaskMap,
    _build_periodic_providers,
    _cancel_phase_tasks,
    _exit_restartable_adapters,
    _start_telemetry_tasks,
    _validate_lifespan_state,
    start_device_tasks_for_names,
    start_freshness_task,
    start_health_check_task,
    start_heartbeat_task,
    start_periodic_tasks,
    start_stream_tasks,
    track_telemetry_freshness,
    wire_restart_callback,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from cosalette._context import DeviceContext
    from cosalette._registration import _ReactorRegistration
    from cosalette._runners._periodic import _PeriodicRegistration
    from cosalette._supervisor import TaskSupervisor
    from cosalette._wiring._context import DeviceInfo

logger = logging.getLogger("cosalette._wiring")


def start_device_tasks(
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    store: Store | None,
    contexts: dict[str, DeviceContext],
    error_publisher: ErrorPublisher,
    health_reporter: HealthReporter,
    trigger_slots: dict[str, _TriggerSlot] | None = None,
    reactors: list[_ReactorRegistration] | None = None,
    reconnect_wake: _ReconnectWake | None = None,
    *,
    defer_first_cycle: bool = False,
    supervisor: TaskSupervisor | None = None,
) -> tuple[list[asyncio.Task[None]], DeviceTaskMap]:
    """Create asyncio tasks for all registered devices.

    Returns a flat task list (for shutdown) and a name→tasks map
    (for per-adapter cancellation during restart).  *defer_first_cycle*
    is set only by the task supervisor's restarts (ADR-081).  With
    *supervisor*, a coalescing-group member whose ``init=`` fails is
    isolated to that member (ADR-081).
    """
    runner = TelemetryRunner(
        store=store, reconnect=reconnect_wake, supervisor=supervisor
    )
    tasks: list[asyncio.Task[None]] = []
    task_map: DeviceTaskMap = {}
    for dev_reg in devices:
        task = asyncio.create_task(
            runner.run_device(
                dev_reg,
                contexts[dev_reg.name],
                health_reporter,
                reactors,
                trigger_slot=trigger_slots.get(dev_reg.name) if trigger_slots else None,
            ),
            name=f"device:{dev_reg.name}",
        )
        tasks.append(task)
        task_map.setdefault(dev_reg.name, []).append(task)
    _start_telemetry_tasks(
        runner,
        telemetry,
        contexts,
        error_publisher,
        health_reporter,
        trigger_slots,
        tasks,
        task_map,
        reactors,
        defer_first_cycle=defer_first_cycle,
    )
    return tasks, task_map


def _supervise_periodic_and_streams(
    supervisor: TaskSupervisor,
    periodic: Sequence[_PeriodicRegistration],
    periodic_tasks: list[asyncio.Task[None]],
    periodic_providers: dict[type, Any],
    streams: Sequence[_StreamRegistration],
    stream_tasks: list[asyncio.Task[None]],
    start_stream: Callable[[_StreamRegistration], list[asyncio.Task[None]]],
) -> None:
    """Supervise periodic and stream tasks with their restart factories.

    The starters create one task per registration in order, so tasks and
    registrations pair up positionally.  A re-created task is appended to
    the same list so phase-4 teardown cancels it.
    """

    def _restart(
        start: Callable[[], list[asyncio.Task[None]]],
        tasks: list[asyncio.Task[None]],
    ) -> asyncio.Task[None]:
        (new_task,) = start()
        prune_done(tasks)
        tasks.append(new_task)
        return new_task

    for reg, task in zip(periodic, periodic_tasks, strict=True):
        supervisor.supervise(
            task,
            registrations=[reg],
            restart=functools.partial(
                _restart,
                functools.partial(start_periodic_tasks, [reg], periodic_providers),
                periodic_tasks,
            ),
        )
    for reg, task in zip(streams, stream_tasks, strict=True):
        supervisor.supervise(
            task,
            # A stream has no availability topic: a failure shows as "error"
            # in the heartbeat only, cleared at the first item after a
            # restart.  A root stream's error payload goes to {prefix}/error.
            entities=[(reg.name, reg.is_root)],
            registrations=[reg],
            restart=functools.partial(
                _restart, functools.partial(start_stream, reg), stream_tasks
            ),
            availability=False,
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
    periodic: Sequence[_PeriodicRegistration] = (),
    stream_list: Sequence[_StreamRegistration] = (),
    stream_contexts: dict[str, DeviceContext] | None = None,
    reactors: list[_ReactorRegistration] | None = None,
    publish_initial_heartbeat: bool = True,
    first_connect: asyncio.Event | None = None,
    startup_connect_timeout: float | None = None,
    reconnect_wake: _ReconnectWake | None = None,
    supervisor: TaskSupervisor | None = None,
) -> None:
    """Enter lifespan, run devices, and tear down.

    With *supervisor*, every task started here is supervised (ADR-081):
    entity, periodic and stream tasks under the app's ``on_task_failure``
    policy, the heartbeat, freshness and health-check loops as
    framework-internal loops.

    Startup errors in the lifespan propagate immediately,
    preventing device launch.  Teardown errors are logged but
    do not mask device errors.

    When *first_connect* is given, entity tasks start only after it is set
    or *startup_connect_timeout* has elapsed (see :func:`await_first_connect`).
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

        health_check_task = start_health_check_task(health_check_runner)
        # This loop starts before waiting for the broker.  Supervise it here,
        # rather than after that wait, so an early failure cannot be missed.
        if supervisor is not None:
            supervisor.supervise_internal(health_check_task)

        await await_first_connect(
            first_connect,
            startup_connect_timeout,
            shutdown_event,
            resolved_clock or SystemClock(),
        )

        # Tracking is normally registered before the connect callback.  Keep
        # this call for direct users of this wiring helper too, before its
        # first heartbeat.
        track_telemetry_freshness(telemetry, health_reporter)
        if publish_initial_heartbeat:
            await health_reporter.publish_heartbeat()
        heartbeat_task = start_heartbeat_task(heartbeat_interval, health_reporter)
        freshness_task = start_freshness_task(
            telemetry, heartbeat_interval, health_reporter
        )

        device_tasks, device_task_map = start_device_tasks(
            devices,
            telemetry,
            store,
            contexts,
            error_publisher,
            health_reporter,
            trigger_slots=trigger_slots,
            reactors=reactors,
            reconnect_wake=reconnect_wake,
            supervisor=supervisor,
        )

        # Build providers for periodic tasks and spawn them
        periodic_providers = _build_periodic_providers(
            resolved_settings, resolved_adapters, lifespan_state, resolved_clock
        )
        periodic_tasks = start_periodic_tasks(periodic, periodic_providers)

        stream_tasks = start_stream_tasks(
            stream_list,
            resolved_adapters,
            periodic_providers,
            shutdown_event,
            reactors,
            stream_contexts=stream_contexts,
            store=store,
            health_reporter=health_reporter,
        )

        on_tasks_started = None
        if supervisor is not None:
            for internal in (heartbeat_task, freshness_task):
                supervisor.supervise_internal(internal)
            supervisor.set_adapter_exhausted_check(
                adapter_exhausted_check(health_check_runner, adapter_device_map)
            )

            def _restart_entities(names: list[str]) -> asyncio.Task[None]:
                new_tasks, new_map = start_device_tasks_for_names(
                    names,
                    devices,
                    telemetry,
                    store,
                    contexts,
                    error_publisher,
                    health_reporter,
                    trigger_slots=trigger_slots,
                    reconnect_wake=reconnect_wake,
                    reactors=reactors,
                    defer_first_cycle=True,
                    supervisor=supervisor,
                )
                device_task_map.update(new_map)
                prune_done(device_tasks)
                device_tasks.extend(new_tasks)
                # One registration (or one whole group) maps to one task.
                (new_task,) = new_tasks
                return new_task

            def _supervise_entity_tasks(tasks: list[asyncio.Task[None]]) -> None:
                supervise_entity_tasks(
                    supervisor, tasks, devices, telemetry, _restart_entities
                )

            on_tasks_started = _supervise_entity_tasks
            _supervise_entity_tasks(device_tasks)
            _supervise_periodic_and_streams(
                supervisor,
                periodic,
                periodic_tasks,
                periodic_providers,
                stream_list,
                stream_tasks,
                lambda reg: start_stream_tasks(
                    [reg],
                    resolved_adapters,
                    periodic_providers,
                    shutdown_event,
                    reactors,
                    stream_contexts=stream_contexts,
                    store=store,
                    health_reporter=health_reporter,
                ),
            )

        # Wire restart callback now that mutable task state exists
        wire_restart_callback(
            health_check_runner,
            adapter_device_map,
            resolved_clock,
            device_task_map,
            devices,
            telemetry,
            store,
            contexts,
            error_publisher,
            health_reporter,
            restart_cooldown,
            shutdown_event,
            device_tasks,
            trigger_slots=trigger_slots,
            reconnect_wake=reconnect_wake,
            supervisor=supervisor,
            on_tasks_started=on_tasks_started,
        )

        await shutdown_event.wait()

        # --- Phase 4: Tear down ---
        await _cancel_phase_tasks(
            device_tasks,
            health_check_task,
            heartbeat_task,
            periodic_tasks,
            stream_tasks=stream_tasks,
            freshness_task=freshness_task,
            supervisor=supervisor,
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
