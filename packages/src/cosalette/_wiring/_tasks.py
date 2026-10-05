"""Task lifecycle and main run-loop functions."""

from __future__ import annotations

import asyncio
import contextlib
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
    stale_restart_callback,
    start_device_tasks_for_names,
    start_freshness_task,
    start_health_check_task,
    start_health_file_task,
    start_heartbeat_task,
    start_loop_stall_watchdog,
    start_periodic_tasks,
    start_stream_tasks,
    stop_loop_stall_watchdog,
    track_streams,
    track_telemetry_freshness,
    wire_restart_callback,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from cosalette._context import DeviceContext
    from cosalette._health._liveness import HealthFileWriter, StaleTelemetryError
    from cosalette._health._loop_stall import LoopStallWatchdog
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


def _supervise_periodic(
    supervisor: TaskSupervisor,
    periodic: Sequence[_PeriodicRegistration],
    periodic_tasks: list[asyncio.Task[None]],
    periodic_providers: dict[type, Any],
) -> None:
    """Supervise periodic tasks with their restart factories.

    The starter creates one task per registration in order, so tasks and
    registrations pair up positionally.  A re-created task is appended to
    the same list so phase-4 teardown cancels it.
    """

    def _restart(reg: _PeriodicRegistration) -> asyncio.Task[None]:
        (new_task,) = start_periodic_tasks([reg], periodic_providers)
        prune_done(periodic_tasks)
        periodic_tasks.append(new_task)
        return new_task

    for reg, task in zip(periodic, periodic_tasks, strict=True):
        supervisor.supervise(
            task, registrations=[reg], restart=functools.partial(_restart, reg)
        )


def _with_streams(
    start_entities: Callable[..., tuple[list[asyncio.Task[None]], DeviceTaskMap]],
    streams: Sequence[_StreamRegistration],
    start_streams: Callable[[list[_StreamRegistration]], list[asyncio.Task[None]]],
) -> Callable[..., tuple[list[asyncio.Task[None]], DeviceTaskMap]]:
    """Extend an entity starter so it also starts the named streams.

    Streams are in the adapter-to-device map (ADR-029), so both restart
    paths, adapter and supervisor, re-create them by name and keep them in
    the name->tasks map.
    """

    def _start(
        names: list[str], *, defer_first_cycle: bool = False
    ) -> tuple[list[asyncio.Task[None]], DeviceTaskMap]:
        tasks, task_map = start_entities(names, defer_first_cycle=defer_first_cycle)
        regs = [reg for reg in streams if reg.name in names]
        tasks.extend(_start_mapped_streams(start_streams, regs, task_map))
        return tasks, task_map

    return _start


def _start_mapped_streams(
    start_streams: Callable[[list[_StreamRegistration]], list[asyncio.Task[None]]],
    streams: Sequence[_StreamRegistration],
    task_map: DeviceTaskMap,
) -> list[asyncio.Task[None]]:
    """Start *streams* and record each task in *task_map* under its name."""
    tasks = start_streams(list(streams))
    for reg, task in zip(streams, tasks, strict=True):
        task_map[reg.name] = [task]
    return tasks


def _stale_exit_callback(
    supervisor: TaskSupervisor | None, shutdown_event: asyncio.Event
) -> Callable[[StaleTelemetryError], None]:
    """Return the ``exit_after_stale`` action: end the app with *error*."""

    def _exit(error: StaleTelemetryError) -> None:
        if supervisor is not None:
            supervisor.request_exit(error)
            return
        logger.critical("Shutting down: %s", error)
        shutdown_event.set()

    return _exit


def _start_pre_connect_loops(
    health_check_runner: HealthCheckRunner | None,
    health_file: Path | None,
    heartbeat_interval: float | None,
    health_reporter: HealthReporter,
    supervisor: TaskSupervisor | None,
) -> tuple[
    asyncio.Task[None] | None, HealthFileWriter | None, asyncio.Task[None] | None
]:
    """Start the loops that run before the first MQTT connect.

    The health-check loop and the opt-in health-file loop (ADR-083) start
    before waiting for the broker.  They are supervised here, rather than
    after that wait, so an early failure cannot be missed.
    """
    health_check_task = start_health_check_task(health_check_runner)
    health_file_writer, health_file_task = start_health_file_task(
        health_file, heartbeat_interval, health_reporter
    ) or (None, None)
    if supervisor is not None:
        supervisor.supervise_internal(health_check_task)
        supervisor.supervise_internal(health_file_task)
    return health_check_task, health_file_writer, health_file_task


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
    telemetry_adapter_device_map: dict[type, list[DeviceInfo]] | None = None,
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
    health_file: Path | None = None,
    loop_stall_timeout: float | None = None,
    exit_after_stale: float | None = None,
    restart_on_stale: bool = False,
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

    *health_file* turns on the opt-in health file, written from here on
    whether or not the broker is reachable; *exit_after_stale* ends the app
    once a telemetry entity or stream has been stale that long (ADR-083).
    *restart_on_stale* restarts the adapters a newly stale entity depends
    on through *health_check_runner* (ADR-084).

    *loop_stall_timeout* arms the loop-stall watchdog together with the
    health file, after the lifespan has started, and disarms it when
    shutdown begins (ADR-088).
    """
    app_context = AppContext(
        settings=resolved_settings,
        adapters=resolved_adapters,
    )

    lifespan_cm = lifespan(app_context)
    lifespan_state = await lifespan_cm.__aenter__()
    health_check_task: asyncio.Task[None] | None = None
    health_file_writer: HealthFileWriter | None = None
    health_file_task: asyncio.Task[None] | None = None
    loop_stall_watchdog: LoopStallWatchdog | None = None

    try:
        _validate_lifespan_state(lifespan_state, resolved_adapters, resolved_settings)

        if health_check_runner is not None:
            await health_check_runner.run_startup_checks()

        health_check_task, health_file_writer, health_file_task = (
            _start_pre_connect_loops(
                health_check_runner,
                health_file,
                heartbeat_interval,
                health_reporter,
                supervisor,
            )
        )
        loop_stall_watchdog = start_loop_stall_watchdog(loop_stall_timeout)

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
        track_streams(stream_list, health_reporter)
        if publish_initial_heartbeat:
            await health_reporter.publish_heartbeat()
        heartbeat_task = start_heartbeat_task(heartbeat_interval, health_reporter)
        freshness_task = start_freshness_task(
            telemetry,
            heartbeat_interval,
            health_reporter,
            exit_after_stale=exit_after_stale,
            on_stale_exit=_stale_exit_callback(supervisor, shutdown_event),
            on_newly_stale=stale_restart_callback(
                restart_on_stale,
                health_check_runner,
                telemetry_adapter_device_map,
            ),
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

        start_streams = functools.partial(
            start_stream_tasks,
            resolved_adapters=resolved_adapters,
            providers=periodic_providers,
            shutdown_event=shutdown_event,
            reactors=reactors,
            stream_contexts=stream_contexts,
            store=store,
            health_reporter=health_reporter,
        )
        stream_tasks = _start_mapped_streams(
            start_streams, stream_list, device_task_map
        )

        # One bound starter for both restart paths (ADR-029 adapter restart,
        # ADR-081 supervisor restart), so they cannot drift apart.
        start_tasks_for_names = _with_streams(
            functools.partial(
                start_device_tasks_for_names,
                devices=devices,
                telemetry=telemetry,
                store=store,
                contexts=contexts,
                error_publisher=error_publisher,
                health_reporter=health_reporter,
                trigger_slots=trigger_slots,
                reconnect_wake=reconnect_wake,
                reactors=reactors,
                supervisor=supervisor,
            ),
            stream_list,
            start_streams,
        )

        on_tasks_started = None
        if supervisor is not None:
            for internal in (heartbeat_task, freshness_task):
                supervisor.supervise_internal(internal)
            supervisor.set_adapter_exhausted_check(
                adapter_exhausted_check(health_check_runner, adapter_device_map)
            )

            def _restart_entities(names: list[str]) -> asyncio.Task[None]:
                new_tasks, new_map = start_tasks_for_names(
                    names, defer_first_cycle=True
                )
                device_task_map.update(new_map)
                prune_done(device_tasks)
                device_tasks.extend(new_tasks)
                # One registration (or one whole group) maps to one task.
                (new_task,) = new_tasks
                return new_task

            def _supervise_entity_tasks(tasks: list[asyncio.Task[None]]) -> None:
                supervise_entity_tasks(
                    supervisor,
                    tasks,
                    devices,
                    telemetry,
                    _restart_entities,
                    streams=stream_list,
                )

            on_tasks_started = _supervise_entity_tasks
            _supervise_entity_tasks([*device_tasks, *stream_tasks])
            _supervise_periodic(
                supervisor, periodic, periodic_tasks, periodic_providers
            )

        # Wire restart callback now that mutable task state exists
        wire_restart_callback(
            health_check_runner,
            adapter_device_map,
            resolved_clock,
            device_task_map,
            start_tasks_for_names,
            restart_cooldown,
            shutdown_event,
            device_tasks,
            supervisor=supervisor,
            on_tasks_started=on_tasks_started,
        )

        await shutdown_event.wait()

        # --- Phase 4: Tear down ---
        # Teardown may block legitimately; a stall here must not exit 6.
        stop_loop_stall_watchdog(loop_stall_watchdog)
        await _cancel_phase_tasks(
            device_tasks,
            health_check_task,
            heartbeat_task,
            periodic_tasks,
            stream_tasks=stream_tasks,
            freshness_task=freshness_task,
            supervisor=supervisor,
            health_file_task=health_file_task,
        )
    finally:
        stop_loop_stall_watchdog(loop_stall_watchdog)
        # Startup cancellation and errors can bypass the normal Phase 4 path.
        # Always stop the writer before removing its snapshot.
        try:
            if health_file_task is not None:
                health_file_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await health_file_task
        finally:
            if health_file_writer is not None:
                health_file_writer.remove()
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
