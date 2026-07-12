"""Telemetry and device execution runner.

Encapsulates the telemetry polling loops, coalescing-group scheduler,
and device execution that were previously methods on :class:`App`.  The
runner is constructed with a persistence store reference and exposes
three public async methods:

- :meth:`~TelemetryRunner.run_telemetry` — single-telemetry polling loop
- :meth:`~TelemetryRunner.run_telemetry_group` — coalescing-group scheduler
- :meth:`~TelemetryRunner.run_device` — device execution with error isolation

"""

from __future__ import annotations

import asyncio
import contextlib
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
from cosalette._runners._contracts import normalize_handler_return, parse_payload
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
    ) -> None:
        """Run a single device function with error isolation.

        Supports async generator device handlers only (breaking change).
        For async generators, dispatches reactors after each yielded
        boundary and once at completion.
        """
        device_store: DeviceStore | None = None
        try:
            providers = build_providers(ctx, reg.name, reg.per_device_config)
            # Create per-device store if app has a store backend
            if self._store is not None:
                device_store = create_device_store(self._store, reg.name)
                providers[DeviceStore] = device_store

            if reg.init is not None:
                init_result = _call_init(reg.init, reg.init_injection_plan, providers)
                providers[type(init_result)] = init_result
            kwargs = resolve_request_kwargs(reg.injection_plan, providers)

            result = reg.func(**kwargs)

            # Handle async generator device handlers (P5 breaking change)
            if inspect.isasyncgen(result):
                await self._run_async_generator_device(
                    result, providers, reactors, reg.name
                )
            # Reject coroutine-style device handlers (breaking change)
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
        try:
            while not ctx.shutdown_requested:
                if self._circuit_breaker_skip(reg, health_reporter):
                    await self._sleep_cycle(ctx, reg, trigger_slot, shutdown_task)
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
                )
                if rr is None:
                    await self._sleep_cycle(ctx, reg, trigger_slot, shutdown_task)
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
                await self._sleep_cycle(ctx, reg, trigger_slot, shutdown_task)
        finally:
            if shutdown_task is not None and not shutdown_task.done():
                shutdown_task.cancel()
            save_store_on_shutdown(device_store, reg.name)

    async def _execute_cycle_attempt(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
        kwargs: dict[str, Any],
        trigger_slot: _TriggerSlot | None,
        trigger_info: tuple[str, Any] | None,
        retry_count: int,
        last_error_type: type[Exception] | None,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> tuple[_RetryResult | None, type[Exception] | None, int]:
        """Update trigger kwargs and run the handler attempt.

        Returns ``(None, last_error_type, retry_count)`` when the trigger
        update fails — the caller should skip to the next cycle.
        Returns ``(rr, last_error_type, rr.retry_count)`` on success.
        ``asyncio.CancelledError`` propagates unchanged.
        """
        try:
            self._update_trigger_kwargs(trigger_slot, trigger_info, kwargs)
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
    ) -> tuple[str, Any] | None:
        """Return (kwarg_name, annotation) for the trigger param, or None.

        Detects both legacy ``TriggerPayload`` params and new-style
        ``Annotated[T, Payload()]`` params used for typed trigger binding.
        """
        from cosalette.mqtt import _PayloadMarker

        for pname, ptype in injection_plan:
            if ptype is TriggerPayload:
                return pname, TriggerPayload
            # Detect Annotated[T, Payload()] — typed trigger binding
            if get_origin(ptype) is Annotated:
                args = get_args(ptype)
                if len(args) >= 2 and isinstance(args[1], _PayloadMarker):
                    return pname, ptype
        return None

    @staticmethod
    def _update_trigger_kwargs(
        trigger_slot: _TriggerSlot | None,
        trigger_info: tuple[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> None:
        """Inject the current trigger value into kwargs before each invocation.

        The slot event is always consumed when set, regardless of whether the
        handler declares a trigger parameter.  Failing to consume the event
        leaves it set permanently, causing _sleep_or_trigger to return
        True on every subsequent call — producing a tight loop with no
        event-loop yields that can never be interrupted by other tasks.

        Supports both legacy ``TriggerPayload`` and typed ``Annotated[T, Payload()]``
        trigger parameters.  For typed params, the trigger payload is parsed via
        :func:`~cosalette._contracts.parse_payload`; on scheduled (no-trigger) runs,
        ``None`` is passed to the adapter so that ``T | None`` optional types succeed.
        """
        if trigger_slot is None:
            return

        if trigger_slot.event.is_set():
            trigger_payload = trigger_slot.consume()  # always clear the event
            if trigger_info is not None:
                kwarg_name, annotation = trigger_info
                if annotation is TriggerPayload:
                    kwargs[kwarg_name] = trigger_payload
                else:
                    # Typed Annotated[T, Payload()] — parse raw trigger string
                    inner_type = get_args(annotation)[0]
                    # Blank trigger payload ("" or whitespace) is the "just
                    # re-run" form → treat as {} so typed payloads get an
                    # empty model rather than None (framework-findings F-1).
                    kwargs[kwarg_name] = parse_payload(
                        (trigger_payload.raw or "").strip() or "{}",
                        inner_type,
                        param=kwarg_name,
                    )
        elif trigger_info is not None:
            kwarg_name, annotation = trigger_info
            if annotation is TriggerPayload:
                kwargs[kwarg_name] = TriggerPayload.scheduled()
            else:
                # Scheduled run with typed payload — validate None for optional types
                inner_type = get_args(annotation)[0]
                kwargs[kwarg_name] = parse_payload(None, inner_type, param=kwarg_name)

    async def _sleep_cycle(
        self,
        ctx: DeviceContext,
        reg: _TelemetryRegistration,
        trigger_slot: _TriggerSlot | None,
        shutdown_task: asyncio.Task[Any] | None,
    ) -> None:
        """Sleep until the next cycle, woken early by trigger or shutdown."""
        if trigger_slot is not None:
            triggered = await self._sleep_or_trigger(
                ctx, _sleep_seconds(reg), trigger_slot, shutdown_task
            )
            if triggered:
                logger.debug(
                    "Trigger received for '%s', scheduling immediate run",
                    reg.name,
                )
        else:
            await ctx.sleep(_sleep_seconds(reg))

    @staticmethod
    async def _cleanup_sleep_tasks(
        done: set[asyncio.Task[Any]],
        sleep_task: asyncio.Task[Any],
        trigger_task: asyncio.Task[Any],
        owned_shutdown: bool,
        shutdown_task: asyncio.Task[Any],
    ) -> None:
        """Cancel tasks created by _sleep_or_trigger that did not complete."""
        for task in (sleep_task, trigger_task):
            if task not in done:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if owned_shutdown and shutdown_task not in done:
            shutdown_task.cancel()

    @staticmethod
    async def _sleep_or_trigger(
        ctx: DeviceContext,
        seconds: float,
        trigger_slot: _TriggerSlot,
        shutdown_task: asyncio.Task[Any] | None,
    ) -> bool:
        """Sleep for *seconds*, returning early if a trigger fires.

        Returns ``True`` if woken by a trigger, ``False`` otherwise.
        The *shutdown_task* is hoisted by the caller across cycles to
        avoid spawning a new task on every sleep.
        """
        if trigger_slot.event.is_set():
            return True
        if ctx.shutdown_requested:
            return False

        sleep_task = asyncio.create_task(ctx._clock.sleep(seconds))
        trigger_task = asyncio.create_task(trigger_slot.event.wait())
        owned_shutdown = shutdown_task is None
        if owned_shutdown:
            shutdown_task = asyncio.create_task(ctx._shutdown_event.wait())

        assert shutdown_task is not None  # noqa: S101  # set above or just created
        done, _ = await asyncio.wait(
            {sleep_task, trigger_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        await TelemetryRunner._cleanup_sleep_tasks(
            done, sleep_task, trigger_task, owned_shutdown, shutdown_task
        )
        return trigger_task in done and shutdown_task not in done

    async def run_telemetry_group(
        self,
        group_name: str,
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        reactors: list[_ReactorRegistration] | None = None,
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
        """
        logger.debug(
            "Starting coalescing group '%s' with %d handler(s)",
            group_name,
            len(registrations),
        )

        # --- 1. INIT: prepare each handler ---
        init_result = await self._init_group_handlers(
            registrations, contexts, error_publisher, health_reporter
        )
        if init_result is None:
            return  # all handlers failed init

        gs = init_result

        # --- 2. MAIN LOOP ---
        try:
            while not gs.sleep_ctx.shutdown_requested and gs.heap:
                next_fire_ms = gs.heap[0][0]

                if not await self._sleep_until_fire(
                    gs.sleep_ctx, gs.epoch, next_fire_ms
                ):
                    break

                batch = self._pop_due_handlers(gs.heap, next_fire_ms)

                await self._process_group_handler_result(
                    batch,
                    registrations,
                    contexts,
                    gs.kwargs_arr,
                    gs.providers_arr,
                    gs.device_stores,
                    gs.strategies,
                    gs.last_published,
                    gs.last_error_type,
                    error_publisher,
                    health_reporter,
                    gs.sleep_ctx,
                    gs.retry_counts,
                    reactors,
                )

                self._reschedule_handlers(gs.heap, batch, next_fire_ms, gs.intervals_ms)
                # Yield once per batch so shutdown helpers can run during catch-up.
                await asyncio.sleep(0)

        finally:
            for store, name in gs.active_stores:
                save_store_on_shutdown(store, name)

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

    async def _init_group_handlers(
        self,
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
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

        for i, reg in enumerate(registrations):
            ctx = contexts[reg.name]
            providers_arr[i], device_stores[i] = self._prepare_telemetry_providers(
                reg, ctx
            )
            if reg.init is not None:
                try:
                    init_result = _call_init(
                        reg.init, reg.init_injection_plan, providers_arr[i]
                    )
                    providers_arr[i][type(init_result)] = init_result
                except Exception as exc:
                    await self._handle_telemetry_error(
                        reg, exc, None, error_publisher, health_reporter
                    )
                    continue  # exclude this handler

            kwargs_arr[i] = resolve_request_kwargs(reg.injection_plan, providers_arr[i])
            strategy = reg.publish_strategy
            strategies[i] = strategy
            if strategy is not None:
                strategy._bind(ctx.clock)
            intervals_ms[i] = _to_ms(_resolved_interval(reg))
            active[i] = True

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
        kwargs_arr: list[dict[str, Any]],
        providers_arr: list[dict[type, Any]],
        device_stores: list[DeviceStore | None],
        strategies: list[PublishStrategy | None],
        last_published: list[dict[str, object] | None],
        last_error_type: list[type[Exception] | None],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        sleep_ctx: DeviceContext,
        retry_counts: list[int],
        reactors: list[_ReactorRegistration] | None = None,
    ) -> None:
        """Execute all handlers due at the current tick and process results.

        Iterates through the batch of handler indices, invoking each
        handler and delegating result processing to
        :meth:`_handle_telemetry_outcome` — the same pipeline used by
        the single-telemetry path.

        Respects ``sleep_ctx.shutdown_requested`` to skip remaining
        handlers when shutdown is in progress.
        """
        for idx in batch:
            if sleep_ctx.shutdown_requested:
                break
            reg = registrations[idx]
            ctx = contexts[reg.name]

            if self._circuit_breaker_skip(reg, health_reporter):
                continue

            rr = await self._attempt_with_retry(
                reg, kwargs_arr[idx], retry_counts[idx], sleep_ctx
            )
            retry_counts[idx] = rr.retry_count

            if rr.outcome == "success":
                (
                    last_published[idx],
                    last_error_type[idx],
                    outcome_ok,
                ) = await self._handle_telemetry_outcome(
                    reg,
                    ctx,
                    rr.result,
                    strategies[idx],
                    last_published[idx],
                    last_error_type[idx],
                    error_publisher,
                    health_reporter,
                    device_stores[idx],
                )
                if outcome_ok:
                    # Dispatch reactors only after a fully successful cycle
                    # Use stored providers to preserve init results
                    last_error_type[idx] = await self._dispatch_telemetry_reactors(
                        reactors,
                        providers_arr[idx],
                        reg,
                        last_error_type[idx],
                        error_publisher,
                        health_reporter,
                    )
                    self._circuit_breaker_record(reg, rr)
            elif rr.outcome in ("error", "exhausted"):
                last_error_type[idx] = await self._handle_telemetry_error(
                    reg,
                    cast(Exception, rr.error),
                    last_error_type[idx],
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
        in :func:`asyncio.wait_for` so a hung handler raises
        :exc:`TimeoutError` (a subclass of :exc:`OSError` per PEP 3151).
        That exception falls into the existing ``except Exception`` path and
        composes transparently with the retry machinery in
        :meth:`_attempt_with_retry`.
        """
        try:
            coro = reg.func(**kwargs)
            if isinstance(reg.timeout, (int, float)):
                result = await asyncio.wait_for(coro, reg.timeout)
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
