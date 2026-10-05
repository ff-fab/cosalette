"""Task lifecycle helpers: creation, cancellation, and adapter restart."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from cosalette._clock import ClockPort
from cosalette._constants import EXIT_LOOP_STALL
from cosalette._context import DeviceContext
from cosalette._health import HealthCheckRunner, HealthReporter
from cosalette._health._liveness import (
    DEFAULT_HEALTH_FILE_INTERVAL,
    HealthFileWriter,
    StaleTelemetryError,
)
from cosalette._health._loop_stall import LoopStallWatchdog
from cosalette._injection import KNOWN_INJECTABLE_TYPES
from cosalette._persistence._stores import Store
from cosalette._registration import (
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._periodic import _PeriodicRegistration, run_periodic
from cosalette._runners._stream_runner import run_stream
from cosalette._settings import Settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence
    from pathlib import Path

    from cosalette._errors import ErrorPublisher
    from cosalette._registration import _ReactorRegistration
    from cosalette._runners._telemetry_runner import TelemetryRunner, _TriggerSlot
    from cosalette._runners._telemetry_types import _ReconnectWake
    from cosalette._supervisor import TaskSupervisor
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
        name="cosalette-heartbeat-loop",
    )


_FRESHNESS_CHECK_CAP = 60.0
"""Upper bound (seconds) on the freshness watchdog's check interval."""


async def freshness_loop(
    health_reporter: HealthReporter,
    interval: float,
    *,
    exit_after_stale: float | None = None,
    on_stale_exit: Callable[[StaleTelemetryError], None] | None = None,
    on_newly_stale: Callable[[list[str]], Awaitable[None]] | None = None,
) -> None:
    """Check telemetry freshness at a fixed interval until cancelled (ADR-080).

    *on_newly_stale* receives the entities that became stale in a check
    (``restart_on_stale``, ADR-084).  With *exit_after_stale*,
    *on_stale_exit* is called once with a :class:`StaleTelemetryError`
    when an entity has been stale that long (ADR-083).  Uses
    ``health_reporter.clock.sleep()`` so that :class:`FakeClock` can drive
    the watchdog in tests.
    """
    exiting = False
    restart_tasks: set[asyncio.Task[None]] = set()
    try:
        while True:
            await health_reporter.clock.sleep(interval)
            try:
                newly_stale = await health_reporter.check_freshness()
                if newly_stale and on_newly_stale is not None:
                    # A restart can block in adapter code. Keep checking stale
                    # deadlines while it runs so exit_after_stale remains an
                    # independent recovery path.
                    task = asyncio.create_task(
                        _run_stale_restart(on_newly_stale, newly_stale)
                    )
                    restart_tasks.add(task)
                    task.add_done_callback(restart_tasks.discard)
                    task.add_done_callback(_log_stale_restart_failure)
            except Exception:
                logger.exception("Freshness check failed")
            if not exiting and on_stale_exit is not None:
                error = _stale_too_long(health_reporter, exit_after_stale)
                if error is not None:
                    exiting = True
                    on_stale_exit(error)
    finally:
        for task in restart_tasks:
            task.cancel()
        if restart_tasks:
            await asyncio.gather(*restart_tasks, return_exceptions=True)


def _log_stale_restart_failure(task: asyncio.Task[None]) -> None:
    """Retrieve and log an exception from a detached stale restart request."""
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("Stale-triggered adapter restart failed: %s", error)


async def _run_stale_restart(
    callback: Callable[[list[str]], Awaitable[None]], names: list[str]
) -> None:
    """Adapt a callback awaitable to the coroutine required by create_task."""
    await callback(names)


def _stale_too_long(
    health_reporter: HealthReporter, exit_after_stale: float | None
) -> StaleTelemetryError | None:
    """Return the error to exit with once an entity outlived *exit_after_stale*."""
    if exit_after_stale is None:
        return None
    longest = health_reporter.longest_stale()
    if longest is None or longest[1] < exit_after_stale:
        return None
    return StaleTelemetryError(*longest)


def stale_restart_callback(
    restart_on_stale: bool,
    health_check_runner: HealthCheckRunner | None,
    telemetry_adapter_device_map: dict[type, list[DeviceInfo]] | None,
) -> Callable[[list[str]], Awaitable[None]] | None:
    """Return the ``restart_on_stale`` action, or ``None`` when off (ADR-084).

    The action requests one restart per adapter that a newly stale entity
    depends on; the runner skips adapters that are not restartable.
    """
    if not restart_on_stale:
        return None
    if health_check_runner is None or not telemetry_adapter_device_map:
        logger.warning(
            "restart_on_stale has no effect: it needs health_check_interval "
            "and a health-checkable adapter"
        )
        return None
    runner = health_check_runner
    device_map = telemetry_adapter_device_map

    async def _restart(names: list[str]) -> None:
        stale = set(names)
        for adapter_type, infos in device_map.items():
            entity = next((i.name for i in infos if i.name in stale), None)
            if entity is not None:
                await runner.request_restart(
                    adapter_type, f"stale telemetry {entity!r}"
                )

    return _restart


def track_telemetry_freshness(
    telemetry: Sequence[_TelemetryRegistration],
    health_reporter: HealthReporter,
) -> None:
    """Register telemetry freshness before any startup heartbeat is published."""
    for reg in telemetry:
        bound = reg.stale_after
        # Unresolved specs (registrations built outside App._run_async)
        # are treated as disabled rather than guessed at.
        stale_after = (
            float(bound)
            if isinstance(bound, (int, float)) and not isinstance(bound, bool)
            else None
        )
        health_reporter.track_freshness(reg.name, stale_after, is_root=reg.is_root)


def track_streams(
    streams: Sequence[_StreamRegistration],
    health_reporter: HealthReporter,
) -> None:
    """Register stream health before any startup heartbeat (ADR-081 amendment).

    Every stream reports ``ok`` in the heartbeat from startup.  A root
    stream is heartbeat-only: its availability sources (supervisor, manual,
    freshness) change its heartbeat status but never publish, so it cannot
    touch the app-wide ``{prefix}/availability``.  A resolved
    ``stale_after`` is tracked like a telemetry bound, and ``feeds`` are
    recorded for availability propagation.
    """
    for reg in streams:
        if reg.is_root:
            health_reporter.track_heartbeat_only(reg.name)
        else:
            health_reporter.set_device_status(reg.name, "ok")
        bound = reg.stale_after
        if isinstance(bound, (int, float)) and not isinstance(bound, bool):
            health_reporter.track_freshness(
                reg.name, float(bound), is_root=reg.is_root, label="Stream"
            )
        health_reporter.set_feeds(reg.name, reg.feeds)


def start_freshness_task(
    telemetry: Sequence[_TelemetryRegistration],
    heartbeat_interval: float | None,
    health_reporter: HealthReporter,
    *,
    exit_after_stale: float | None = None,
    on_stale_exit: Callable[[StaleTelemetryError], None] | None = None,
    on_newly_stale: Callable[[list[str]], Awaitable[None]] | None = None,
) -> asyncio.Task[None] | None:
    """Track freshness for every telemetry entity and start the watchdog.

    Every entity is tracked so the heartbeat reports its freshness fields;
    only entities with a resolved ``stale_after`` are checked.  Returns
    ``None`` (no task) when no entity has one.  The watchdog runs every
    ``min(heartbeat_interval, 60 s, smallest stale_after)``.
    """
    track_telemetry_freshness(telemetry, health_reporter)
    smallest = health_reporter.min_stale_after()
    if smallest is None:
        return None
    interval = min(heartbeat_interval or _FRESHNESS_CHECK_CAP, _FRESHNESS_CHECK_CAP)
    return asyncio.create_task(
        freshness_loop(
            health_reporter,
            min(interval, smallest),
            exit_after_stale=exit_after_stale,
            on_stale_exit=on_stale_exit,
            on_newly_stale=on_newly_stale,
        ),
        name="cosalette-freshness-loop",
    )


async def health_file_loop(writer: HealthFileWriter, clock: ClockPort) -> None:
    """Write the health file now and then every ``writer.interval`` (ADR-083)."""
    while True:
        writer.write()
        await clock.sleep(writer.interval)


def start_health_file_task(
    path: Path | None,
    heartbeat_interval: float | None,
    health_reporter: HealthReporter,
) -> tuple[HealthFileWriter, asyncio.Task[None]] | None:
    """Start writing the opt-in health file, or return ``None`` when off.

    The file is written every *heartbeat_interval* seconds, or every
    :data:`DEFAULT_HEALTH_FILE_INTERVAL` when heartbeats are disabled.
    """
    if path is None:
        return None
    writer = HealthFileWriter(
        path=path,
        reporter=health_reporter,
        interval=heartbeat_interval or DEFAULT_HEALTH_FILE_INTERVAL,
    )
    task = asyncio.create_task(
        health_file_loop(writer, health_reporter.clock),
        name="cosalette-health-file-loop",
    )
    return writer, task


def start_loop_stall_watchdog(timeout: float | None) -> LoopStallWatchdog | None:
    """Arm the opt-in loop-stall watchdog, or return ``None`` when off (ADR-088)."""
    if timeout is None:
        return None
    watchdog = LoopStallWatchdog(timeout)
    watchdog.start()
    logger.info(
        "Loop-stall watchdog armed: exit code %d after %gs without the event loop",
        EXIT_LOOP_STALL,
        timeout,
    )
    return watchdog


def stop_loop_stall_watchdog(watchdog: LoopStallWatchdog | None) -> None:
    """Disarm *watchdog* if one was armed; safe to call more than once."""
    if watchdog is not None:
        watchdog.stop()


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
        if issubclass(cls, Settings):
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
    reactors: list[_ReactorRegistration] | None = None,
    stream_contexts: dict[str, DeviceContext] | None = None,
    store: Store | None = None,
    health_reporter: HealthReporter | None = None,
) -> list[asyncio.Task[None]]:
    """Create asyncio tasks for all registered stream handlers.

    *health_reporter* lets a re-created stream clear its task-failure mark
    at its first item (ADR-081).
    """
    tasks: list[asyncio.Task[None]] = []
    for reg in streams:
        stream_providers: dict[type, Any] = {
            **providers,
            logging.Logger: logging.getLogger(f"cosalette.stream.{reg.name}"),
        }
        # Inject stream-scoped DeviceContext when available
        if stream_contexts is not None and reg.name in stream_contexts:
            stream_providers[DeviceContext] = stream_contexts[reg.name]
        task = asyncio.create_task(
            run_stream(
                reg,
                resolved_adapters,
                stream_providers,
                shutdown_event,
                reactors,
                store=store,
                health_reporter=health_reporter,
            ),
            name=f"stream:{reg.name}",
        )
        tasks.append(task)
    return tasks


def _expect_cancel(
    supervisor: TaskSupervisor | None, tasks: Iterable[asyncio.Task[None] | None]
) -> None:
    """Tell the supervisor the framework is cancelling *tasks* (ADR-081)."""
    if supervisor is None:
        return
    for task in tasks:
        if task is not None:
            supervisor.expect_cancel(task)


async def cancel_periodic_tasks(
    tasks: list[asyncio.Task[None]],
    *,
    supervisor: TaskSupervisor | None = None,
) -> None:
    """Cancel periodic tasks and wait up to 5 s for graceful completion.

    Uses a grace period so handlers that are mid-execution get a chance
    to finish their current cycle cleanly.
    """
    _expect_cancel(supervisor, tasks)
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


async def cancel_tasks(
    tasks: list[asyncio.Task[None]],
    *,
    supervisor: TaskSupervisor | None = None,
) -> None:
    """Cancel device tasks and wait for graceful completion.

    With *supervisor*, the cancellations are recorded as expected first, and
    a task whose failure the supervisor already reported is not logged
    again.
    """
    _expect_cancel(supervisor, tasks)
    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results, strict=True):
        if supervisor is not None and supervisor.was_handled(task):
            continue
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
    *,
    supervisor: TaskSupervisor | None = None,
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
        await cancel_tasks(tasks_to_cancel, supervisor=supervisor)

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
    trigger_slots: dict[str, _TriggerSlot] | None = None,
    reconnect_wake: _ReconnectWake | None = None,
    *,
    reactors: list[_ReactorRegistration] | None = None,
    defer_first_cycle: bool = False,
    supervisor: TaskSupervisor | None = None,
) -> tuple[list[asyncio.Task[None]], DeviceTaskMap]:
    """Start device tasks only for the specified device names.

    *defer_first_cycle* is set by the task supervisor only (ADR-081): the
    re-created telemetry and group tasks skip their immediate first poll.

    For coalescing groups, if any member device is in *device_names*,
    the entire group is recreated so the shared scheduler covers all
    members.

    *trigger_slots* must be forwarded so that a restarted triggerable
    entity keeps the slot its :class:`~cosalette.EntityNotifier` is
    bound to.  The notifier holds the slot objects from the original
    :class:`TriggerConfig`; recreating a task without them would leave
    the entity permanently unwakeable (ADR-064, ADR-065).
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
        trigger_slots=trigger_slots,
        reactors=reactors,
        reconnect_wake=reconnect_wake,
        defer_first_cycle=defer_first_cycle,
        supervisor=supervisor,
    )


def start_health_check_task(
    health_check_runner: HealthCheckRunner | None,
) -> asyncio.Task[None] | None:
    """Start the periodic health check background task, if enabled.

    Returns ``None`` when health checks are disabled (no runner provided).
    """
    if health_check_runner is None:
        return None
    return asyncio.create_task(
        health_check_runner.run_loop(), name="cosalette-health-check-loop"
    )


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
    freshness_task: asyncio.Task[None] | None = None,
    supervisor: TaskSupervisor | None = None,
    health_file_task: asyncio.Task[None] | None = None,
) -> None:
    _expect_cancel(
        supervisor,
        (health_check_task, heartbeat_task, freshness_task, health_file_task),
    )
    await cancel_tasks(device_tasks, supervisor=supervisor)
    if periodic_tasks:
        await cancel_periodic_tasks(periodic_tasks, supervisor=supervisor)
    if stream_tasks:
        await cancel_tasks(stream_tasks, supervisor=supervisor)
    if health_check_task is not None:
        health_check_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await health_check_task
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
    for loop_task in (freshness_task, health_file_task):
        if loop_task is not None:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task


async def _exit_restartable_adapters(
    restartable_adapters: list[object] | None,
) -> None:
    if not restartable_adapters:
        return
    from cosalette._wiring._adapter_lifecycle import exit_single_adapter

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
    reactors: list[Any] | None = None,  # list[_ReactorRegistration]
    *,
    defer_first_cycle: bool = False,
) -> None:
    """Create asyncio tasks for all telemetry registrations, including groups.

    Ungrouped registrations each get their own task; grouped registrations
    share a single scheduler task per group.  Either shape may carry a
    trigger source: an ungrouped entity gets its slot directly, while a
    group scheduler takes the whole mapping and picks out its own members
    (ADR-067).  Mutates *tasks* and *task_map* in place.
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
                    reactors=reactors,
                    defer_first_cycle=defer_first_cycle,
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
                reactors,
                trigger_slots=trigger_slots,
                defer_first_cycle=defer_first_cycle,
            ),
            name=f"group:{group_name}",
        )
        tasks.append(task)
        for gr in group_regs:
            task_map.setdefault(gr.name, []).append(task)


async def _adapter_healthy_after_restart(adapter_type: type, adapter: object) -> bool:
    """Probe an optional adapter health check after a successful restart."""
    check = getattr(adapter, "health_check", None)
    if check is None:
        return True
    try:
        return bool(await check())
    except Exception:
        logger.exception(
            "Health check after restarting %s raised", adapter_type.__qualname__
        )
        return False


def wire_restart_callback(
    health_check_runner: HealthCheckRunner | None,
    adapter_device_map: dict[type, list[DeviceInfo]] | None,
    resolved_clock: ClockPort | None,
    device_task_map: DeviceTaskMap,
    start_tasks: Callable[[list[str]], tuple[list[asyncio.Task[None]], DeviceTaskMap]],
    restart_cooldown: float,
    shutdown_event: asyncio.Event,
    device_tasks: list[asyncio.Task[None]],
    *,
    supervisor: TaskSupervisor | None = None,
    on_tasks_started: Callable[[list[asyncio.Task[None]]], None] | None = None,
) -> None:
    """Wire the adaptive restart callback onto *health_check_runner*.

    A no-op when any of the three required restart prerequisites
    (*health_check_runner*, *adapter_device_map*, *resolved_clock*)
    is ``None``.

    With *supervisor*, an adapter restart owns its tasks for its whole
    duration (ADR-081 section 7): pending supervisor restarts for them are
    cancelled, their cancellation is expected, and failures meanwhile do
    not count against the task budget.  *on_tasks_started* receives the
    re-created tasks so they are supervised too.

    *start_tasks* re-creates the tasks for the given entity names.  It is
    the same bound :func:`start_device_tasks_for_names` the supervisor
    restart path uses, so both paths wire reactors, trigger slots and the
    reconnect wake identically.

    Also wires the recovery callback: an adapter that passes a health check
    after a failed restart attempt gets its stranded tasks back the same
    way, once it is entered again (ADR-029).
    """
    if (
        health_check_runner is None
        or adapter_device_map is None
        or resolved_clock is None
    ):
        return

    from cosalette._wiring._adapter_lifecycle import restart_single_adapter

    async def _owned(adapter_type: type, body: Awaitable[bool]) -> bool:
        if supervisor is None:
            return await body
        owned = supervisor.begin_adapter_restart(
            info.name for info in adapter_device_map.get(adapter_type, [])
        )
        try:
            return await body
        finally:
            supervisor.end_adapter_restart(owned)

    async def _on_restart(adapter_type: type, adapter: object) -> bool:
        return await _owned(adapter_type, _restart(adapter_type, adapter))

    # Devices (and deferred group tasks) a failed attempt left without
    # tasks; the next attempt for the same adapter must still recreate
    # them, since cancel_tasks_for_adapter() no longer finds them.
    stranded: dict[type, tuple[list[str], list[asyncio.Task[None]]]] = {}
    entered: dict[type, bool] = dict.fromkeys(adapter_device_map, True)

    async def _on_recovered(adapter_type: type) -> bool:
        # An adapter that passes its health check after a failed attempt
        # gets back the tasks that attempt cancelled — but only once it is
        # entered again; until then the next restart attempt re-enters it.
        if not entered.get(adapter_type, True):
            return False
        pending = stranded.get(adapter_type)
        if pending is not None:
            await _owned(adapter_type, _recreate(*pending))
            stranded.pop(adapter_type, None)
        return True

    async def _restart(adapter_type: type, adapter: object) -> bool:
        cancelled, deferred_tasks = await cancel_tasks_for_adapter(
            device_task_map, adapter_device_map, adapter_type, supervisor=supervisor
        )
        prev_cancelled, prev_deferred = stranded.pop(adapter_type, ([], []))
        cancelled = list(dict.fromkeys([*prev_cancelled, *cancelled]))
        deferred_tasks = list(dict.fromkeys([*prev_deferred, *deferred_tasks]))

        def _record_entry_state(is_entered: bool) -> None:
            entered[adapter_type] = is_entered

        if not (
            await restart_single_adapter(
                adapter,
                restart_cooldown,
                resolved_clock,
                shutdown_event,
                was_entered=entered.get(adapter_type, True),
                on_entry_state_changed=_record_entry_state,
            )
            and await _adapter_healthy_after_restart(adapter_type, adapter)
        ):
            # Leave deferred group tasks running — they still
            # serve healthy adapters' devices.
            stranded[adapter_type] = (cancelled, deferred_tasks)
            return False
        return await _recreate(cancelled, deferred_tasks)

    async def _recreate(
        cancelled: list[str], deferred_tasks: list[asyncio.Task[None]]
    ) -> bool:
        # Tear down the old deferred group tasks *before* creating their
        # replacements: a restarted group must never share its per-member
        # trigger slots / wake event with the scheduler being cancelled
        # (ADR-067).  Pending arms survive on the persistent slots, so the
        # fresh scheduler still sees them on its first scan.
        if deferred_tasks:
            await cancel_tasks(deferred_tasks, supervisor=supervisor)
        new_tasks, new_map = start_tasks(cancelled)
        device_tasks.extend(new_tasks)
        device_task_map.update(new_map)
        if on_tasks_started is not None:
            on_tasks_started(new_tasks)
        # GC: prune all done tasks across restart cycles
        device_tasks[:] = [t for t in device_tasks if not t.done()]
        return True

    health_check_runner._on_restart_needed = _on_restart
    health_check_runner._on_recovered = _on_recovered
