"""Telemetry and device execution runner.

Encapsulates the telemetry polling loops, coalescing-group scheduler,
and device execution. The runner is constructed with a persistence store
reference and exposes three public async methods:

- :meth:`~TelemetryRunner.run_telemetry` — single-telemetry polling loop
- :meth:`~TelemetryRunner.run_telemetry_group` — coalescing-group scheduler
- :meth:`~TelemetryRunner.run_device` — device execution with error isolation

"""

from __future__ import annotations

import asyncio
import heapq
import inspect
import logging
from typing import Annotated, Any, cast, get_args, get_origin

from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._injection import build_providers, resolve_request_kwargs
from cosalette._persistence._stores import DeviceStore, Store
from cosalette._registration import (
    _call_init,
    _DeviceRegistration,
    _ReactorRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._asyncio_utils import _cancel_task
from cosalette._runners._contracts import normalize_handler_return, parse_payload
from cosalette._runners._device_trigger import DeviceTrigger
from cosalette._runners._runner_utils import (
    create_device_store,
    maybe_persist,
    save_store_on_shutdown,
)
from cosalette._runners._telemetry_types import (
    _TICK_PRECISION,
    _GroupState,
    _resolved_interval,
    _RetryResult,
    _sleep_seconds,
    _to_ms,
    _TriggerSlot,
)
from cosalette._runners._trigger import TriggerPayload
from cosalette._strategies import PublishStrategy
from cosalette._utils import _callable_qualname

logger = logging.getLogger(__name__)


def _normalize_telemetry_return(
    reg: _TelemetryRegistration,
    value: Any,
) -> dict[str, Any] | None:
    """Normalise a telemetry handler return value to a JSON-compatible dict.

    Delegates to :func:`cosalette._contracts.normalize_handler_return`
    (shared helper, caches return annotation per function).
    """
    return normalize_handler_return(
        reg.func, value, reg.state_model, handler_name=reg.name
    )


class TelemetryRunner:
    """Executes telemetry polling loops, group scheduling, and device tasks.

    Constructed with the optional persistence :class:`Store`, the runner
    owns no other mutable state — everything else (contexts, registrations,
    error publishers, health reporters) is passed as method arguments.
    """

    def __init__(self, store: Store | None) -> None:
        self._store = store

    # --- Public entry points -----------------------------------------------

    async def run_device(
        self,
        reg: _DeviceRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        reactors: list[_ReactorRegistration] | None = None,
        trigger_slot: _TriggerSlot | None = None,
    ) -> None:
        """Run a single device function with error isolation.

        Supports async generator device handlers only.
        For async generators, dispatches reactors after each yielded
        boundary and once at completion.

        When *trigger_slot* is set (the device declared
        ``triggerable="local"``), a :class:`DeviceTrigger` bound to that
        slot is injected so the handler can await in-process wakes
        instead of polling on a fixed cadence (ADR-065).
        """
        device_store: DeviceStore | None = None
        try:
            providers = build_providers(ctx, reg.name, reg.per_device_config)
            # Create per-device store if app has a store backend
            if self._store is not None:
                device_store = create_device_store(self._store, reg.name)
                providers[DeviceStore] = device_store
            if trigger_slot is not None:
                providers[DeviceTrigger] = DeviceTrigger(
                    trigger_slot, reg.name, ctx.clock
                )

            if reg.init is not None:
                init_result = _call_init(reg.init, reg.init_injection_plan, providers)
                providers[type(init_result)] = init_result
            kwargs = resolve_request_kwargs(reg.injection_plan, providers)

            result = reg.func(**kwargs)

            # Handle async generator device handlers.
            if inspect.isasyncgen(result):
                await self._run_async_generator_device(
                    result, providers, reactors, reg.name
                )
            # Reject coroutine-style device handlers.
            elif inspect.iscoroutine(result):
                # Clean up the coroutine to prevent unawaited coroutine warnings
                result.close()
                type_name = type(result).__qualname__
                msg = (
                    f"Device handler {_callable_qualname(reg.func)!r} must return "
                    f"an async generator, got {type_name!r}. "
                    f"Update to 'async def' that yields after each unit of work."
                )
                raise TypeError(msg)
            else:
                # Non-async-generator device return
                type_name = type(result).__qualname__
                msg = (
                    f"Device handler {_callable_qualname(reg.func)!r} must return "
                    f"an async generator, got {type_name!r}. "
                    f"Update to 'async def' that yields after each unit of work."
                )
                raise TypeError(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Device '%s' crashed: %s", reg.name, exc)
            await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)
        finally:
            save_store_on_shutdown(device_store, reg.name)

    async def _run_async_generator_device(
        self,
        async_gen: Any,  # AsyncGenerator[Any, None]
        providers: dict[type, Any],
        reactors: list[_ReactorRegistration] | None,
        device_name: str,  # noqa: ARG002
    ) -> None:
        """Run an async generator device handler with reactor dispatch.

        Dispatches reactors after each yielded value and once after
        normal completion only (not on cancellation or error).
        """
        from cosalette._wiring._reactors import run_reactor_boundaries

        await run_reactor_boundaries(async_gen, providers, reactors)

    async def run_telemetry(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        trigger_slot: _TriggerSlot | None = None,
        reactors: list[_ReactorRegistration] | None = None,
    ) -> None:
        """Run a telemetry polling loop with optional publish strategy.

        Strategy lifecycle (when ``reg.publish_strategy`` is set):

        1. ``_bind(clock)`` — inject the clock before the loop.
        2. First non-``None`` result is always published.
        3. Subsequent results gated by ``strategy.should_publish()``.
        4. ``strategy.on_published()`` called after each publish.
        """
        providers, device_store = self._prepare_telemetry_providers(reg, ctx)

        if not await self._init_telemetry_handler(
            reg,
            providers,
            error_publisher,
            health_reporter,
        ):
            return

        kwargs = resolve_request_kwargs(reg.injection_plan, providers)
        trigger_info = self._find_trigger_kwarg(reg.injection_plan)

        strategy = reg.publish_strategy
        if strategy is not None:
            strategy._bind(ctx.clock)

        # Create shutdown_task once per device lifetime so _sleep_or_trigger
        # can reuse it across cycles instead of spawning a new task each time.
        shutdown_task: asyncio.Task[Any] | None = (
            asyncio.create_task(ctx._shutdown_event.wait())
            if trigger_slot is not None
            else None
        )
        last_published: dict[str, object] | None = None
        last_error_type: type[Exception] | None = None
        retry_count = 0  # cumulative counter, resets on success
        trigger_task: asyncio.Task[Any] | None = None
        # Seed the first execute attempt as a non-trigger wake; later sleep
        # cycles decide whether the next run was resumed by a trigger.
        woke_by_trigger = False
        try:
            while not ctx.shutdown_requested:
                if self._circuit_breaker_skip(reg, health_reporter):
                    trigger_task, woke_by_trigger = await self._sleep_cycle(
                        ctx, reg, trigger_slot, shutdown_task, trigger_task
                    )
                    continue

                rr, last_error_type, retry_count = await self._execute_cycle_attempt(
                    reg,
                    ctx,
                    kwargs,
                    trigger_slot,
                    trigger_info,
                    retry_count,
                    last_error_type,
                    error_publisher,
                    health_reporter,
                    woke_by_trigger,
                )
                if rr is None:
                    trigger_task, woke_by_trigger = await self._sleep_cycle(
                        ctx, reg, trigger_slot, shutdown_task, trigger_task
                    )
                    continue

                last_published, last_error_type = await self._process_cycle_result(
                    reg,
                    ctx,
                    rr,
                    strategy,
                    last_published,
                    last_error_type,
                    error_publisher,
                    health_reporter,
                    device_store,
                    providers,
                    reactors,
                )
                trigger_task, woke_by_trigger = await self._sleep_cycle(
                    ctx, reg, trigger_slot, shutdown_task, trigger_task
                )
        finally:
            if shutdown_task is not None and not shutdown_task.done():
                shutdown_task.cancel()
            if trigger_task is not None:
                trigger_task.cancel()  # cancel() on a done task is a safe no-op
            save_store_on_shutdown(device_store, reg.name)

    async def _execute_cycle_attempt(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
        kwargs: dict[str, Any],
        trigger_slot: _TriggerSlot | None,
        trigger_info: tuple[str, Any, type | None] | None,
        retry_count: int,
        last_error_type: type[Exception] | None,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        woke_by_trigger: bool = False,
    ) -> tuple[_RetryResult | None, type[Exception] | None, int]:
        """Update trigger kwargs and run the handler attempt.

        Returns ``(None, last_error_type, retry_count)`` when the trigger
        update fails — the caller should skip to the next cycle.
        Returns ``(rr, last_error_type, rr.retry_count)`` on success.
        ``asyncio.CancelledError`` propagates unchanged.
        """
        try:
            self._update_trigger_kwargs(
                trigger_slot, trigger_info, kwargs, woke_by_trigger
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error_type = await self._handle_telemetry_error(
                reg, exc, last_error_type, error_publisher, health_reporter
            )
            return None, last_error_type, retry_count
        rr = await self._attempt_with_retry(reg, kwargs, retry_count, ctx)
        return rr, last_error_type, rr.retry_count

    async def _process_cycle_result(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
        rr: _RetryResult,
        strategy: PublishStrategy | None,
        last_published: dict[str, object] | None,
        last_error_type: type[Exception] | None,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        device_store: DeviceStore | None,
        providers: dict[type, Any],
        reactors: list[_ReactorRegistration] | None,
    ) -> tuple[dict[str, object] | None, type[Exception] | None]:
        """Route a retry result to success or error handling."""
        if rr.outcome == "success":
            (
                last_published,
                last_error_type,
                outcome_ok,
            ) = await self._handle_telemetry_outcome(
                reg,
                ctx,
                rr.result,
                strategy,
                last_published,
                last_error_type,
                error_publisher,
                health_reporter,
                device_store,
            )
            if outcome_ok:
                # Dispatch reactors only after a fully successful cycle
                last_error_type = await self._dispatch_telemetry_reactors(
                    reactors,
                    providers,
                    reg,
                    last_error_type,
                    error_publisher,
                    health_reporter,
                )
                self._circuit_breaker_record(reg, rr)
        elif rr.outcome in ("error", "exhausted"):
            last_error_type = await self._handle_telemetry_error(
                reg,
                cast(Exception, rr.error),
                last_error_type,
                error_publisher,
                health_reporter,
            )
            self._circuit_breaker_record(reg, rr)
        return last_published, last_error_type

    @staticmethod
    def _find_trigger_kwarg(
        injection_plan: list[tuple[str, Any]],
    ) -> tuple[str, Any, type | None] | None:
        """Return (kwarg_name, annotation, inner_type) for the trigger param, or None.

        Detects both legacy ``TriggerPayload`` params and new-style
        ``Annotated[T, Payload()]`` params used for typed trigger binding.
        The ``inner_type`` is pre-computed for ``Annotated`` params to avoid
        repeated ``get_args()`` calls in the hot polling loop.
        """
        from cosalette.mqtt import _PayloadMarker

        for pname, ptype in injection_plan:
            if ptype is TriggerPayload:
                return pname, TriggerPayload, None
            # Detect Annotated[T, Payload()] — typed trigger binding
            if get_origin(ptype) is Annotated:
                args = get_args(ptype)
                if len(args) >= 2 and isinstance(args[1], _PayloadMarker):
                    inner_type = args[0]
                    return pname, ptype, inner_type
        return None

    @staticmethod
    def _update_trigger_kwargs(
        trigger_slot: _TriggerSlot | None,
        trigger_info: tuple[str, Any, type | None] | None,
        kwargs: dict[str, Any],
        woke_by_trigger: bool = False,
    ) -> None:
        """Inject the current trigger value into kwargs before each invocation.

        **Unthrottled slots (``min_interval is None``) — unchanged.**  The
        slot event is always consumed when set, regardless of what woke the
        cycle or whether the handler declares a trigger parameter.  Failing
        to consume the event leaves it set permanently, causing
        _sleep_or_trigger to return True on every subsequent call —
        producing a tight loop with no event-loop yields that can never be
        interrupted by other tasks.  This matters even though an arm can
        normally never outlive a cycle boundary: ``_cleanup_sleep_tasks``
        awaits ``_cancel_task``, so a ``call_soon_threadsafe`` arm can land
        there, and today the following interval run consumes it.

        **Throttled slots (``min_interval`` set, ADR-066) — one exception.**
        A throttled arm is *meant* to outlive a cycle boundary: an
        ``interval=`` heartbeat must not consume it, or the trailing run the
        throttle promised is deleted along with its payload.  So a set event
        is consumed only when *woke_by_trigger* says the throttle gate
        released this cycle.  The tight loop the paragraph above warns about
        cannot happen here: ``_sleep_or_trigger`` bounds a throttled slot to
        one trigger-initiated run per ``min_interval`` by sleeping out the
        window before it returns ``True``.  Do not "fix" this back to an
        unconditional consume.

        Binding the resolved payload (or the scheduled placeholder) onto the
        handler kwargs is delegated to :meth:`_bind_trigger_kwarg`.
        """
        if trigger_slot is None:
            return

        consume = woke_by_trigger or trigger_slot.min_interval is None
        payload = (
            trigger_slot.consume()  # clear the event
            if consume and trigger_slot.event.is_set()
            else None
        )
        TelemetryRunner._bind_trigger_kwarg(trigger_info, kwargs, payload)

    @staticmethod
    def _bind_trigger_kwarg(
        trigger_info: tuple[str, Any, type | None] | None,
        kwargs: dict[str, Any],
        payload: TriggerPayload | None,
    ) -> None:
        """Bind *payload* (``None`` = a scheduled run) into *kwargs*.

        Supports both the legacy ``TriggerPayload`` parameter and the typed
        ``Annotated[T, Payload()]`` form.  For typed params the raw trigger
        string is parsed via
        :func:`~cosalette._contracts.parse_payload`; on a scheduled run
        ``None`` is passed instead, so that ``T | None`` optional types
        succeed.
        """
        if trigger_info is None:
            return
        kwarg_name, annotation, inner_type = trigger_info
        if annotation is TriggerPayload:
            kwargs[kwarg_name] = (
                TriggerPayload.scheduled() if payload is None else payload
            )
            return
        if payload is None:
            # Scheduled run with typed payload — validate None for optional types
            kwargs[kwarg_name] = parse_payload(None, inner_type, param=kwarg_name)
            return
        # Typed Annotated[T, Payload()] — parse raw trigger string.  A blank
        # trigger payload ("" or whitespace) is the "just re-run" form → treat
        # it as {} so typed payloads get an empty model rather than None
        # (framework-findings F-1).
        kwargs[kwarg_name] = parse_payload(
            (payload.raw or "").strip() or "{}", inner_type, param=kwarg_name
        )

    async def _sleep_cycle(
        self,
        ctx: DeviceContext,
        reg: _TelemetryRegistration,
        trigger_slot: _TriggerSlot | None,
        shutdown_task: asyncio.Task[Any] | None,
        trigger_task: asyncio.Task[Any] | None = None,
    ) -> tuple[asyncio.Task[Any] | None, bool]:
        """Sleep until the next cycle.

        Returns ``(trigger_task_for_reuse, woke_by_trigger)``.  The flag
        tells :meth:`_update_trigger_kwargs` whether this cycle was
        trigger-initiated, which is what lets a throttled arm survive an
        ``interval=`` heartbeat (ADR-066).
        """
        if trigger_slot is not None:
            triggered, trigger_task = await self._sleep_or_trigger(
                ctx, _sleep_seconds(reg), trigger_slot, shutdown_task, trigger_task
            )
            if triggered:
                logger.debug(
                    "Trigger received for '%s', scheduling immediate run",
                    reg.name,
                )
            return trigger_task, triggered
        else:
            await ctx.sleep(_sleep_seconds(reg))
            return None, False

    @staticmethod
    async def _cleanup_sleep_tasks(
        done: set[asyncio.Task[Any]],
        sleep_task: asyncio.Task[Any],
        trigger_task: asyncio.Task[Any],
        owned_shutdown: bool,
        shutdown_task: asyncio.Task[Any],
        *,
        cancel_trigger: bool = True,
    ) -> None:
        """Cancel tasks created by _sleep_or_trigger that did not complete."""
        if sleep_task not in done:
            await _cancel_task(sleep_task)
        if cancel_trigger and trigger_task not in done:
            await _cancel_task(trigger_task)
        if owned_shutdown and shutdown_task not in done:
            shutdown_task.cancel()

    @staticmethod
    async def _sleep_or_trigger(
        ctx: DeviceContext,
        seconds: float,
        trigger_slot: _TriggerSlot,
        shutdown_task: asyncio.Task[Any] | None,
        trigger_task: asyncio.Task[Any] | None = None,
    ) -> tuple[bool, asyncio.Task[Any] | None]:
        """Sleep for *seconds*, returning early if a trigger fires.

        Returns ``(triggered, trigger_task_to_reuse)`` where ``triggered`` is
        ``True`` if woken by a trigger and the second element is the trigger
        task for reuse in the next cycle (or ``None`` if a new one is needed).
        The *shutdown_task* is hoisted by the caller across cycles to
        avoid spawning a new task on every sleep.

        An unthrottled slot (``min_interval is None``, the default) takes
        :meth:`_race_sleep_and_trigger` verbatim — the throttle adds no
        branch to the default path.
        """
        if trigger_slot.min_interval is None:
            return await TelemetryRunner._race_sleep_and_trigger(
                ctx, seconds, trigger_slot, shutdown_task, trigger_task
            )
        return await TelemetryRunner._sleep_or_trigger_throttled(
            ctx, seconds, trigger_slot, shutdown_task, trigger_task
        )

    @staticmethod
    async def _sleep_or_trigger_throttled(
        ctx: DeviceContext,
        seconds: float,
        trigger_slot: _TriggerSlot,
        shutdown_task: asyncio.Task[Any] | None,
        trigger_task: asyncio.Task[Any] | None,
    ) -> tuple[bool, asyncio.Task[Any] | None]:
        """``min_interval=`` variant of :meth:`_sleep_or_trigger` (ADR-066).

        A bounded loop around the same race.  While the slot is armed but
        the throttle window is closed, the loop waits out whichever comes
        first — the window reopening or this cycle's ``interval=`` deadline.
        A heartbeat returns ``False`` with the arm still pending, so the
        trailing run (and its payload) survives.
        """
        deadline = ctx._clock.now() + seconds
        while True:
            if not trigger_slot.event.is_set():
                fired, trigger_task = await TelemetryRunner._race_sleep_and_trigger(
                    ctx,
                    max(0.0, deadline - ctx._clock.now()),
                    trigger_slot,
                    shutdown_task,
                    trigger_task,
                )
                if not fired:
                    return False, trigger_task
                continue  # armed now — re-enter the throttle gate
            outcome = await TelemetryRunner._await_throttle_window(
                ctx, trigger_slot, deadline, shutdown_task
            )
            if outcome == "trigger":
                return True, None
            if outcome != "retry":  # "interval" or "shutdown"
                return False, trigger_task

    @staticmethod
    async def _await_throttle_window(
        ctx: DeviceContext,
        trigger_slot: _TriggerSlot,
        deadline: float,
        shutdown_task: asyncio.Task[Any] | None,
    ) -> str:
        """Take one step of the ADR-066 throttle gate for an armed slot.

        Returns ``"trigger"`` when the run may start now (the window is
        recorded), ``"interval"`` when this cycle's ``interval=`` deadline
        comes first (the arm deliberately stays pending), ``"shutdown"``,
        or ``"retry"`` when the window was slept out and the caller should
        look again.
        """
        now = ctx._clock.now()
        delay = trigger_slot.throttle_delay(now)
        if delay <= 0.0:
            trigger_slot.note_trigger_start(now)
            return "trigger"
        # The window and the heartbeat both have fixed deadlines, so sleep
        # the nearer one rather than racing two clock sleeps.  A tie goes to
        # the trigger: it serves the heartbeat's purpose too, and returning
        # the heartbeat first would only place two runs at the same instant.
        remaining = max(0.0, deadline - now)
        heartbeat = remaining < delay
        if await TelemetryRunner._sleep_or_shutdown(
            ctx, remaining if heartbeat else delay, shutdown_task
        ):
            return "shutdown"
        return "interval" if heartbeat else "retry"

    @staticmethod
    async def _sleep_or_shutdown(
        ctx: DeviceContext,
        seconds: float,
        shutdown_task: asyncio.Task[Any] | None,
    ) -> bool:
        """Sleep *seconds* on the injected clock. ``True`` if shutdown won."""
        if ctx.shutdown_requested:
            return True
        sleep_task = asyncio.create_task(ctx._clock.sleep(seconds))
        owned_shutdown = shutdown_task is None
        if owned_shutdown:
            shutdown_task = asyncio.create_task(ctx._shutdown_event.wait())

        assert shutdown_task is not None  # noqa: S101  # set above or just created
        done, _ = await asyncio.wait(
            {sleep_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if sleep_task not in done:
            await _cancel_task(sleep_task)
        if owned_shutdown and shutdown_task not in done:
            shutdown_task.cancel()
        return shutdown_task in done

    @staticmethod
    async def _race_sleep_and_trigger(
        ctx: DeviceContext,
        seconds: float,
        trigger_slot: _TriggerSlot,
        shutdown_task: asyncio.Task[Any] | None,
        trigger_task: asyncio.Task[Any] | None = None,
    ) -> tuple[bool, asyncio.Task[Any] | None]:
        """Race one sleep against the trigger event and shutdown.

        The un-throttled sleep path, unchanged since ADR-036.
        """
        if trigger_slot.event.is_set():
            return True, None  # event already set; caller creates new task next cycle

        if ctx.shutdown_requested:
            return False, trigger_task

        sleep_task = asyncio.create_task(ctx._clock.sleep(seconds))
        # Reuse trigger_task if still pending, create new one if consumed or absent
        if trigger_task is None or trigger_task.done():
            trigger_task = asyncio.create_task(trigger_slot.event.wait())
        owned_shutdown = shutdown_task is None
        if owned_shutdown:
            shutdown_task = asyncio.create_task(ctx._shutdown_event.wait())

        assert shutdown_task is not None  # noqa: S101  # set above or just created
        done, _ = await asyncio.wait(
            {sleep_task, trigger_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        fired = trigger_task in done and shutdown_task not in done
        # Reuse trigger_task only when sleep fired (not trigger, not shutdown)
        reuse = not fired and shutdown_task not in done
        await TelemetryRunner._cleanup_sleep_tasks(
            done,
            sleep_task,
            trigger_task,
            owned_shutdown,
            shutdown_task,
            cancel_trigger=not reuse,
        )
        return fired, (trigger_task if reuse else None)

    async def run_telemetry_group(
        self,
        group_name: str,
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        reactors: list[_ReactorRegistration] | None = None,
        trigger_slots: dict[str, _TriggerSlot] | None = None,
    ) -> None:
        """Run a coalescing-group scheduler for grouped telemetry handlers.

        Handlers in the same group are managed by a shared tick-aligned
        scheduler.  A priority queue (min-heap) of ``(fire_time_ms, index)``
        entries determines when each handler fires.  Handlers that share a
        fire time execute sequentially in a single batch — enabling adapter
        session sharing for resources like serial buses.

        Integer-millisecond tick arithmetic avoids floating-point
        accumulation errors (e.g. 300 s × 12 == 3600 s exactly).

        Per-handler semantics are preserved: each handler has its own
        ``DeviceContext``, ``PublishStrategy``, error state, persistence
        policy, and init function.

        **Trigger sources on members (ADR-067).**  A member declaring
        ``triggerable=`` also wakes the scheduler out of cycle.  The wake
        is *per member*: only the armed members run, in one shared batch
        with whatever the tick made due, so a push burst still costs one
        execution window.  A trigger-initiated run never moves the
        member's heap entry — ``interval=`` heartbeats stay anchored to
        the shared group epoch, because losing that anchor would cost the
        tick coincidence the group exists to create.
        """
        logger.debug(
            "Starting coalescing group '%s' with %d handler(s)",
            group_name,
            len(registrations),
        )

        # --- 1. INIT: prepare each handler ---
        init_result = await self._init_group_handlers(
            registrations, contexts, error_publisher, health_reporter, trigger_slots
        )
        if init_result is None:
            return  # all handlers failed init

        gs = init_result
        if gs.wake is not None:
            gs.shutdown_task = asyncio.create_task(gs.sleep_ctx._shutdown_event.wait())

        # --- 2. MAIN LOOP ---
        try:
            while not gs.sleep_ctx.shutdown_requested and gs.heap:
                if not await self._run_group_cycle(
                    gs,
                    registrations,
                    contexts,
                    error_publisher,
                    health_reporter,
                    reactors,
                ):
                    break
                # Yield once per cycle so shutdown helpers can run during catch-up.
                await asyncio.sleep(0)

        finally:
            for task in (gs.wake_task, gs.shutdown_task):
                if task is not None:
                    task.cancel()  # cancel() on a done task is a safe no-op
            for store, name in gs.active_stores:
                save_store_on_shutdown(store, name)

    async def _run_group_cycle(
        self,
        gs: _GroupState,
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        reactors: list[_ReactorRegistration] | None,
    ) -> bool:
        """Run one wait-batch-reschedule cycle. ``False`` asks for shutdown.

        The batch is the union of the members the tick made due and the
        members whose pending arm the trigger gate released (ADR-067).
        Only the tick-due half is rescheduled: an out-of-cycle run must
        leave the member's heap entry on the shared group epoch.
        """
        next_fire_ms = gs.heap[0][0]
        tick_reached = await self._await_group_cycle(gs, next_fire_ms)
        if tick_reached is None:
            return False

        due = self._pop_due_handlers(gs.heap, next_fire_ms) if tick_reached else []
        released = self._release_armed(gs)
        batch = sorted(set(due) | released)
        if batch:
            await self._process_group_handler_result(
                batch,
                registrations,
                contexts,
                gs,
                released,
                error_publisher,
                health_reporter,
                reactors,
            )
        self._reschedule_handlers(gs.heap, due, next_fire_ms, gs.intervals_ms)
        return True

    async def _await_group_cycle(
        self,
        gs: _GroupState,
        next_fire_ms: int,
    ) -> bool | None:
        """Sleep until the group's next tick or an eligible member arm.

        Returns ``True`` when the tick deadline was reached, ``False``
        when a trigger wake came first, and ``None`` on shutdown.

        A group with no triggerable member (the only shape that existed
        before ADR-067) takes :meth:`_sleep_until_fire` verbatim — the
        trigger path adds no work to it.

        When both the tick and an arm are ready the tick wins, so the two
        merge into a single batch rather than landing as two batches at
        the same instant (the ADR-066 tie rule, read the other way round:
        here the loser is not deferred, it is merged).
        """
        ctx = gs.sleep_ctx
        if gs.wake is None:
            reached = await self._sleep_until_fire(ctx, gs.epoch, next_fire_ms)
            return reached or None

        tick_at = gs.epoch + next_fire_ms / _TICK_PRECISION
        while True:
            # Clear before scanning: _TriggerSlot.arm / arm_local set the
            # per-member event *before* _signal_group sets this wake, so this
            # edge can only over-wake, never drop an arm.
            gs.wake.clear()
            now = ctx.clock.now()
            if tick_at - now <= 0:
                return True
            hold = self._armed_hold(gs, now)
            # Tri-state: 0.0 = an arm is eligible now (batch it); None =
            # nothing armed (wait for the tick); >0.0 = throttle window still
            # open, so wake no later than whichever of tick/window comes first.
            if hold == 0.0:
                return False
            delay = tick_at - now if hold is None else min(tick_at - now, hold)
            alive, gs.wake_task = await self._sleep_until_wake(
                ctx, delay, gs.wake, gs.shutdown_task, gs.wake_task
            )
            if not alive:
                return None

    @staticmethod
    def _armed_hold(gs: _GroupState, now: float) -> float | None:
        """Seconds until the earliest armed member may run, or ``None``.

        ``None`` means nothing is armed; ``0.0`` means at least one arm is
        eligible right now.  Anything larger is an ADR-066 throttle window
        that has not reopened yet.
        """
        return min(
            (
                slot.throttle_delay(now)
                for slot in gs.trigger_slots
                if slot is not None and slot.event.is_set()
            ),
            default=None,
        )

    @staticmethod
    def _release_armed(gs: _GroupState) -> set[int]:
        """Return the member indices whose pending arm may run now.

        Records the throttle window for each one it releases.  A member
        still inside its ``min_interval`` window keeps its arm pending —
        a tick may run it as a heartbeat meanwhile, and
        :meth:`_update_trigger_kwargs` will leave the arm (and its
        payload) alone because it is not in the returned set.
        """
        now = gs.sleep_ctx.clock.now()
        released: set[int] = set()
        for idx, slot in enumerate(gs.trigger_slots):
            if slot is None or not slot.event.is_set():
                continue
            if slot.throttle_delay(now) > 0.0:
                continue
            slot.note_trigger_start(now)
            released.add(idx)
        return released

    @staticmethod
    async def _sleep_until_wake(
        ctx: DeviceContext,
        seconds: float,
        wake: asyncio.Event,
        shutdown_task: asyncio.Task[Any] | None,
        wake_task: asyncio.Task[Any] | None,
    ) -> tuple[bool, asyncio.Task[Any] | None]:
        """Sleep up to *seconds*, returning early when *wake* is set.

        Returns ``(alive, wake_task_for_reuse)``; *alive* is ``False``
        only when shutdown won the race.  The caller re-derives what to
        do from the slots, so this deliberately does not report which of
        the sleep and the wake finished first.
        """
        if ctx.shutdown_requested:
            return False, wake_task
        # Created alongside gs.wake in run_telemetry_group; both or neither.
        assert shutdown_task is not None  # noqa: S101
        sleep_task = asyncio.create_task(ctx._clock.sleep(seconds))
        if wake_task is None or wake_task.done():
            wake_task = asyncio.create_task(wake.wait())
        done, _ = await asyncio.wait(
            {sleep_task, wake_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        reuse = wake_task not in done and shutdown_task not in done
        await TelemetryRunner._cleanup_sleep_tasks(
            done,
            sleep_task,
            wake_task,
            False,  # shutdown_task is owned by run_telemetry_group
            shutdown_task,
            cancel_trigger=not reuse,
        )
        return shutdown_task not in done, (wake_task if reuse else None)

    # --- Internal helpers --------------------------------------------------

    def _prepare_telemetry_providers(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
    ) -> tuple[dict[type, object], DeviceStore | None]:
        """Build the DI provider map for a telemetry handler."""
        providers = build_providers(ctx, reg.name, reg.per_device_config)
        device_store: DeviceStore | None = None
        if self._store is not None:
            device_store = create_device_store(self._store, reg.name)
            providers[DeviceStore] = device_store
        return providers, device_store

    async def _init_telemetry_handler(
        self,
        reg: _TelemetryRegistration,
        providers: dict[type, object],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> bool:
        """Run the optional init function for a telemetry handler.

        Returns ``True`` if init succeeded (or was not needed).
        Returns ``False`` if init raised — the caller should abort.
        """
        if reg.init is None:
            return True
        try:
            init_result = _call_init(reg.init, reg.init_injection_plan, providers)
            providers[type(init_result)] = init_result
        except Exception as exc:
            await self._handle_telemetry_error(
                reg,
                exc,
                None,
                error_publisher,
                health_reporter,
            )
            return False
        return True

    async def _handle_telemetry_outcome(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
        result: Any,
        strategy: PublishStrategy | None,
        last_published: dict[str, object] | None,
        last_error_type: type[Exception] | None,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        device_store: DeviceStore | None,
    ) -> tuple[dict[str, object] | None, type[Exception] | None, bool]:
        """Run the publish -> persist -> error-clear pipeline for one result.

        Shared by both the single-telemetry and group-telemetry paths.
        Returns the updated ``(last_published, last_error_type, ok)`` tuple
        where *ok* is ``False`` when return normalisation fails — callers
        must skip reactor dispatch and circuit-breaker success in that case.

        Normalises *result* via the return annotation / ``state_model``
        before publishing, supporting typed handler returns (BaseModel,
        dataclass, TypedDict, primitives) in addition to plain dicts.
        """
        if result is None:
            maybe_persist(device_store, reg.persist_policy, False, reg.name)
            return last_published, last_error_type, True

        # Normalise typed return to dict before publish/strategy comparison
        try:
            normalized = _normalize_telemetry_return(reg, result)
        except Exception as exc:
            last_error_type = await self._handle_telemetry_error(
                reg, exc, last_error_type, error_publisher, health_reporter
            )
            return last_published, last_error_type, False
        if normalized is None:
            maybe_persist(device_store, reg.persist_policy, False, reg.name)
            return last_published, last_error_type, True

        if self._should_publish_telemetry(normalized, last_published, strategy):
            await ctx.publish_state(normalized)
            last_published = normalized
            did_publish = True
            if strategy is not None:
                strategy.on_published()
        else:
            did_publish = False

        maybe_persist(device_store, reg.persist_policy, did_publish, reg.name)

        last_error_type = self._clear_telemetry_error(
            reg.name, last_error_type, health_reporter
        )
        return last_published, last_error_type, True

    async def _init_group_member(
        self,
        reg: _TelemetryRegistration,
        providers: dict[type, object],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> bool:
        """Run one group member's init function.

        Returns ``False`` when init raised, which excludes the member
        from the group for the lifetime of the scheduler.
        """
        if reg.init is None:
            return True
        try:
            init_result = _call_init(reg.init, reg.init_injection_plan, providers)
        except Exception as exc:
            await self._handle_telemetry_error(
                reg, exc, None, error_publisher, health_reporter
            )
            return False
        providers[type(init_result)] = init_result
        return True

    def _group_member_trigger(
        self,
        reg: _TelemetryRegistration,
        trigger_slots: dict[str, _TriggerSlot] | None,
    ) -> tuple[_TriggerSlot | None, tuple[str, Any, type | None] | None]:
        """Return one member's ``(slot, trigger kwarg info)`` pair (ADR-067).

        Both are ``None`` for a member that declares no trigger source;
        the kwarg is looked up only when there is a slot to feed it.
        """
        slot = trigger_slots.get(reg.name) if trigger_slots else None
        if slot is None:
            return None, None
        return slot, self._find_trigger_kwarg(reg.injection_plan)

    async def _init_group_handlers(
        self,
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        trigger_slots: dict[str, _TriggerSlot] | None = None,
    ) -> _GroupState | None:
        """Initialise per-handler state for a coalescing-group scheduler.

        Prepares DI providers, calls init functions, binds publish
        strategies, and builds the priority-queue heap.

        Returns ``None`` when every handler fails its init — the caller
        should exit early.  Otherwise returns a `_GroupState` with:

        - ``kwargs_arr`` — resolved kwargs per handler
        - ``device_stores`` — per-handler persistence stores
        - ``strategies`` — per-handler publish strategies
        - ``last_published`` — per-handler last-published state
        - ``last_error_type`` — per-handler last error type
        - ``intervals_ms`` — per-handler interval in ms
        - ``heap`` — priority queue of ``(fire_time_ms, index)``
        - ``sleep_ctx`` — context for shutdown-aware sleep
        - ``epoch`` — reference timestamp
        - ``active_stores`` — ``(store, name)`` pairs for cleanup
        - ``trigger_slots`` / ``trigger_infos`` / ``wake`` — the ADR-067
          per-member trigger state, ``None`` throughout for a group whose
          members declare no trigger source
        """
        n = len(registrations)

        # Per-handler state arrays
        # providers_arr: O(n) memory but avoids rebuilding injection context per tick.
        providers_arr: list[dict[type, object]] = [{} for _ in range(n)]
        device_stores: list[DeviceStore | None] = [None] * n
        kwargs_arr: list[dict[str, Any]] = [{} for _ in range(n)]
        strategies: list[PublishStrategy | None] = [None] * n
        last_published: list[dict[str, object] | None] = [None] * n
        last_error_type: list[type[Exception] | None] = [None] * n
        intervals_ms: list[int] = [0] * n
        active: list[bool] = [False] * n
        slots: list[_TriggerSlot | None] = [None] * n
        infos: list[tuple[str, Any, type | None] | None] = [None] * n

        for i, reg in enumerate(registrations):
            ctx = contexts[reg.name]
            providers_arr[i], device_stores[i] = self._prepare_telemetry_providers(
                reg, ctx
            )
            if not await self._init_group_member(
                reg, providers_arr[i], error_publisher, health_reporter
            ):
                continue  # exclude this handler

            kwargs_arr[i] = resolve_request_kwargs(reg.injection_plan, providers_arr[i])
            strategy = reg.publish_strategy
            strategies[i] = strategy
            if strategy is not None:
                strategy._bind(ctx.clock)
            intervals_ms[i] = _to_ms(_resolved_interval(reg))
            active[i] = True
            # Only an active member is scanned for arms; a handler excluded
            # by a failing init must not be woken by one either.
            slots[i], infos[i] = self._group_member_trigger(reg, trigger_slots)

        # Build priority queue and active-stores list in a single pass
        heap: list[tuple[int, int]] = []
        active_stores: list[tuple[DeviceStore | None, str]] = []
        for i in range(n):
            if active[i]:
                heapq.heappush(heap, (0, i))
                active_stores.append((device_stores[i], registrations[i].name))

        if not heap:
            return None

        # First active handler's context for shutdown-aware sleep.
        # heap[0][1] is the lowest-index active handler.
        sleep_ctx = contexts[registrations[heap[0][1]].name]
        epoch = sleep_ctx.clock.now()

        return _GroupState(
            kwargs_arr=kwargs_arr,
            providers_arr=providers_arr,
            device_stores=device_stores,
            strategies=strategies,
            last_published=last_published,
            last_error_type=last_error_type,
            intervals_ms=intervals_ms,
            heap=heap,
            sleep_ctx=sleep_ctx,
            epoch=epoch,
            active_stores=active_stores,
            retry_counts=[0] * n,
            trigger_slots=slots,
            trigger_infos=infos,
            wake=self._group_wake(slots),
        )

    @staticmethod
    def _group_wake(slots: list[_TriggerSlot | None]) -> asyncio.Event | None:
        """Return the wake event this group's members share, if any.

        Every triggerable member of one group is handed the same event by
        :meth:`~cosalette._wiring.TriggerConfig.build`, so the first one
        found speaks for the group.  ``None`` means no member declared a
        trigger source — the shape the scheduler had before ADR-067.
        """
        return next(
            (slot.wake for slot in slots if slot is not None and slot.wake), None
        )

    async def _sleep_until_fire(
        self,
        sleep_ctx: DeviceContext,
        epoch: float,
        fire_time_ms: int,
    ) -> bool:
        """Sleep until the next fire time, returning *False* on shutdown.

        Calculates the wall-clock wait from the scheduler epoch, sleeps
        if positive, and checks the shutdown flag afterwards.
        """
        elapsed = sleep_ctx.clock.now() - epoch
        wait_seconds = (fire_time_ms / _TICK_PRECISION) - elapsed
        if wait_seconds > 0:
            await sleep_ctx.sleep(wait_seconds)
        return not sleep_ctx.shutdown_requested

    @staticmethod
    def _pop_due_handlers(
        heap: list[tuple[int, int]],
        fire_time_ms: int,
    ) -> list[int]:
        """Pop all handler indices whose fire time matches *fire_time_ms*."""
        batch: list[int] = []
        while heap and heap[0][0] == fire_time_ms:
            _, idx = heapq.heappop(heap)
            batch.append(idx)
        return batch

    @staticmethod
    def _reschedule_handlers(
        heap: list[tuple[int, int]],
        batch: list[int],
        fire_time_ms: int,
        intervals_ms: list[int],
    ) -> None:
        """Push the next fire time for every handler in *batch*."""
        for idx in batch:
            next_time = fire_time_ms + intervals_ms[idx]
            heapq.heappush(heap, (next_time, idx))

    async def _process_group_handler_result(
        self,
        batch: list[int],
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        gs: _GroupState,
        released: set[int],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        reactors: list[_ReactorRegistration] | None = None,
    ) -> None:
        """Execute all handlers in the current batch and process results.

        Iterates through the batch of handler indices, invoking each
        handler and delegating result processing to
        :meth:`_handle_telemetry_outcome` — the same pipeline used by
        the single-telemetry path.

        *released* holds the members the ADR-067 trigger gate let through
        this cycle; every other member of the batch is here because its
        tick came due and sees ``TriggerPayload.scheduled()``.

        Respects ``sleep_ctx.shutdown_requested`` to skip remaining
        handlers when shutdown is in progress.
        """
        sleep_ctx = gs.sleep_ctx
        for idx in batch:
            if sleep_ctx.shutdown_requested:
                break
            reg = registrations[idx]
            ctx = contexts[reg.name]

            if self._circuit_breaker_skip(reg, health_reporter):
                continue

            self._update_trigger_kwargs(
                gs.trigger_slots[idx],
                gs.trigger_infos[idx],
                gs.kwargs_arr[idx],
                idx in released,
            )
            rr = await self._attempt_with_retry(
                reg, gs.kwargs_arr[idx], gs.retry_counts[idx], sleep_ctx
            )
            gs.retry_counts[idx] = rr.retry_count

            if rr.outcome == "success":
                (
                    gs.last_published[idx],
                    gs.last_error_type[idx],
                    outcome_ok,
                ) = await self._handle_telemetry_outcome(
                    reg,
                    ctx,
                    rr.result,
                    gs.strategies[idx],
                    gs.last_published[idx],
                    gs.last_error_type[idx],
                    error_publisher,
                    health_reporter,
                    gs.device_stores[idx],
                )
                if outcome_ok:
                    # Dispatch reactors only after a fully successful cycle
                    # Use stored providers to preserve init results
                    gs.last_error_type[idx] = await self._dispatch_telemetry_reactors(
                        reactors,
                        gs.providers_arr[idx],
                        reg,
                        gs.last_error_type[idx],
                        error_publisher,
                        health_reporter,
                    )
                    self._circuit_breaker_record(reg, rr)
            elif rr.outcome in ("error", "exhausted"):
                gs.last_error_type[idx] = await self._handle_telemetry_error(
                    reg,
                    cast(Exception, rr.error),
                    gs.last_error_type[idx],
                    error_publisher,
                    health_reporter,
                )
                self._circuit_breaker_record(reg, rr)

    @staticmethod
    def _circuit_breaker_skip(
        reg: _TelemetryRegistration,
        health_reporter: HealthReporter,
    ) -> bool:
        """Return True (and log) if the circuit breaker says to skip."""
        cb = reg.circuit_breaker
        if cb is not None and not cb.should_attempt():
            logger.warning(
                "Telemetry '%s' circuit open, skipping",
                reg.name,
            )
            health_reporter.set_device_status(reg.name, "circuit_open")
            return True
        return False

    @staticmethod
    def _circuit_breaker_record(
        reg: _TelemetryRegistration,
        rr: _RetryResult,
    ) -> None:
        """Notify the circuit breaker of the outcome, if present."""
        cb = reg.circuit_breaker
        if cb is None:
            return
        if rr.outcome == "success":
            cb.record_success()
        elif rr.outcome == "exhausted":
            cb.record_failure()

    async def _attempt_with_retry(
        self,
        reg: _TelemetryRegistration,
        kwargs: dict[str, Any],
        retry_count: int,
        ctx: DeviceContext,
    ) -> _RetryResult:
        """Execute a handler with retry logic, returning the outcome.

        Handles the retry for-loop, ``CancelledError`` propagation,
        non-retryable exception detection, intermediate/exhaustion
        logging, and backoff delays.  The caller is responsible for
        circuit-breaker checks *before* calling, and for acting on
        the returned outcome (publishing results, recording errors).
        """
        max_attempts = reg.retry + 1

        for attempt in range(1, max_attempts + 1):
            if ctx.shutdown_requested:
                return _RetryResult(
                    result=None,
                    error=None,
                    retry_count=retry_count,
                    outcome="shutdown",
                )

            result, exc = await self._try_invoke(reg, kwargs)
            if exc is None:
                return _RetryResult(
                    result=result,
                    error=None,
                    retry_count=0,
                    outcome="success",
                )

            if reg.retry == 0 or not isinstance(exc, reg.retry_on):
                return _RetryResult(
                    result=None,
                    error=exc,
                    retry_count=retry_count,
                    outcome="error",
                )

            retry_count += 1
            if attempt == max_attempts:
                logger.warning(
                    "Telemetry '%s' retries exhausted (%d/%d)",
                    reg.name,
                    retry_count,
                    reg.retry,
                )
                return _RetryResult(
                    result=None,
                    error=exc,
                    retry_count=retry_count,
                    outcome="exhausted",
                )

            delay = reg.backoff.delay(retry_count) if reg.backoff else 0
            logger.warning(
                "Telemetry '%s' retry %d/%d after %s, backoff %.1fs",
                reg.name,
                attempt,
                reg.retry,
                type(exc).__name__,
                delay,
            )
            if delay > 0:
                await ctx.sleep(delay)

        # Unreachable in practice — the loop always returns.
        return _RetryResult(  # pragma: no cover
            result=None,
            error=None,
            retry_count=retry_count,
            outcome="shutdown",
        )

    @staticmethod
    async def _try_invoke(
        reg: _TelemetryRegistration,
        kwargs: dict[str, Any],
    ) -> tuple[dict[str, object] | None, Exception | None]:
        """Invoke the handler once, returning ``(result, None)`` or ``(None, exc)``.

        When ``reg.timeout`` is a concrete positive number the call is wrapped
        in :func:`asyncio.timeout` (ADR-060) so a hung handler raises
        :exc:`TimeoutError` (a subclass of :exc:`OSError` per PEP 3151).
        That exception falls into the existing ``except Exception`` path and
        composes transparently with the retry machinery in
        :meth:`_attempt_with_retry`.
        """
        try:
            coro = reg.func(**kwargs)
            if isinstance(reg.timeout, (int, float)) and not isinstance(
                reg.timeout, bool
            ):
                async with asyncio.timeout(reg.timeout):
                    result = await coro
            else:
                result = await coro
            return result, None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return None, exc

    @staticmethod
    def _should_publish_telemetry(
        result: dict[str, object],
        last_published: dict[str, object] | None,
        strategy: PublishStrategy | None,
    ) -> bool:
        """Decide whether a telemetry reading should be published.

        First reading always goes through. Without a strategy, every
        reading is published. With a strategy, the decision is delegated.
        """
        if last_published is None:
            return True
        if strategy is None:
            return True
        return strategy.should_publish(result, last_published)

    @staticmethod
    def _clear_telemetry_error(
        name: str,
        last_error_type: type[Exception] | None,
        health_reporter: HealthReporter,
    ) -> type[Exception] | None:
        """Clear error state on successful telemetry poll."""
        if last_error_type is not None:
            logger.info("Telemetry '%s' recovered", name)
            health_reporter.set_device_status(name, "ok")
        return None

    @staticmethod
    async def _handle_telemetry_error(
        reg: _TelemetryRegistration,
        exc: Exception,
        last_error_type: type[Exception] | None,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> type[Exception]:
        """Handle a telemetry polling error with deduplication."""
        if type(exc) is not last_error_type:
            logger.error("Telemetry '%s' error: %s", reg.name, exc)
            await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)
        health_reporter.set_device_status(reg.name, "error")
        return type(exc)

    @staticmethod
    async def _dispatch_telemetry_reactors(
        reactors: list[_ReactorRegistration] | None,
        providers: dict[type, Any],
        reg: _TelemetryRegistration,
        last_error_type: type[Exception] | None,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> type[Exception] | None:
        """Dispatch reactors after successful telemetry work.

        Returns the updated last_error_type. If no reactors are
        configured or reactor dispatch succeeds, returns the
        current last_error_type unchanged. On reactor exception,
        routes through telemetry error handling and returns
        the updated error type.
        """
        if not reactors:
            return last_error_type

        try:
            from cosalette._wiring._reactors import dispatch_reactors

            await dispatch_reactors(reactors, providers)
            return last_error_type
        except asyncio.CancelledError:
            raise
        except Exception as reactor_exc:
            # Handle reactor failures through existing telemetry error handling
            return await TelemetryRunner._handle_telemetry_error(
                reg,
                reactor_exc,
                last_error_type,
                error_publisher,
                health_reporter,
            )
