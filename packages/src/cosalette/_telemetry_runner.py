"""Telemetry and device execution runner.

Encapsulates the telemetry polling loops, coalescing-group scheduler,
and device execution that were previously methods on :class:`App`.  The
runner is constructed with a persistence store reference and exposes
three public async methods:

- :meth:`~TelemetryRunner.run_telemetry` — single-telemetry polling loop
- :meth:`~TelemetryRunner.run_telemetry_group` — coalescing-group scheduler
- :meth:`~TelemetryRunner.run_device` — device execution with error isolation

Phase 3 of COS-0fv (decompose ``_app.py``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import heapq
import logging
from typing import Any, cast

from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._injection import build_providers, resolve_kwargs
from cosalette._registration import (
    _call_init,
    _DeviceRegistration,
    _TelemetryRegistration,
)
from cosalette._runner_utils import (
    create_device_store,
    maybe_persist,
    save_store_on_shutdown,
)
from cosalette._stores import DeviceStore, Store
from cosalette._strategies import PublishStrategy

logger = logging.getLogger(__name__)

_TICK_PRECISION = 1000  # milliseconds


def _to_ms(seconds: float) -> int:
    """Convert seconds to integer milliseconds for tick arithmetic.

    Positive intervals are clamped to a minimum of 1 ms so that
    scheduler ticks always advance in time.
    """
    if seconds <= 0:
        return 0
    ms = round(seconds * _TICK_PRECISION)
    return ms or 1


@dataclasses.dataclass(slots=True)
class _GroupState:
    """Per-handler state produced by :meth:`TelemetryRunner._init_group_handlers`.

    Replaces a 10-element tuple so that call-sites use named
    attribute access instead of positional destructuring.
    """

    kwargs_arr: list[dict[str, Any]]
    device_stores: list[DeviceStore | None]
    strategies: list[PublishStrategy | None]
    last_published: list[dict[str, object] | None]
    last_error_type: list[type[Exception] | None]
    intervals_ms: list[int]
    heap: list[tuple[int, int]]
    sleep_ctx: DeviceContext
    epoch: float
    active_stores: list[tuple[DeviceStore | None, str]]


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
    ) -> None:
        """Run a single device function with error isolation."""
        device_store: DeviceStore | None = None
        try:
            providers = build_providers(ctx, reg.name)

            # Create per-device store if app has a store backend
            if self._store is not None:
                device_store = create_device_store(self._store, reg.name)
                providers[DeviceStore] = device_store

            if reg.init is not None:
                init_result = _call_init(reg.init, reg.init_injection_plan, providers)
                providers[type(init_result)] = init_result
            kwargs = resolve_kwargs(reg.injection_plan, providers)
            await reg.func(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Device '%s' crashed: %s", reg.name, exc)
            await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)
        finally:
            save_store_on_shutdown(device_store, reg.name)

    async def run_telemetry(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
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
        kwargs = resolve_kwargs(reg.injection_plan, providers)
        strategy = reg.publish_strategy
        if strategy is not None:
            strategy._bind(ctx.clock)
        last_published: dict[str, object] | None = None
        last_error_type: type[Exception] | None = None
        try:
            while not ctx.shutdown_requested:
                try:
                    result = await reg.func(**kwargs)

                    (
                        last_published,
                        last_error_type,
                    ) = await self._handle_telemetry_outcome(
                        reg,
                        ctx,
                        result,
                        strategy,
                        last_published,
                        last_error_type,
                        health_reporter,
                        device_store,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error_type = await self._handle_telemetry_error(
                        reg,
                        exc,
                        last_error_type,
                        error_publisher,
                        health_reporter,
                    )
                await ctx.sleep(cast(float, reg.interval))
        finally:
            save_store_on_shutdown(device_store, reg.name)

    async def run_telemetry_group(
        self,
        group_name: str,
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
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
                    gs.device_stores,
                    gs.strategies,
                    gs.last_published,
                    gs.last_error_type,
                    error_publisher,
                    health_reporter,
                    gs.sleep_ctx,
                )

                self._reschedule_handlers(gs.heap, batch, next_fire_ms, gs.intervals_ms)

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
        providers = build_providers(ctx, reg.name)
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
        result: dict[str, object] | None,
        strategy: PublishStrategy | None,
        last_published: dict[str, object] | None,
        last_error_type: type[Exception] | None,
        health_reporter: HealthReporter,
        device_store: DeviceStore | None,
    ) -> tuple[dict[str, object] | None, type[Exception] | None]:
        """Run the publish -> persist -> error-clear pipeline for one result.

        Shared by both the single-telemetry and group-telemetry paths.
        Returns the updated ``(last_published, last_error_type)`` tuple.
        """
        if result is None:
            maybe_persist(device_store, reg.persist_policy, False, reg.name)
            return last_published, last_error_type

        if self._should_publish_telemetry(result, last_published, strategy):
            await ctx.publish_state(result)
            last_published = result
            did_publish = True
            if strategy is not None:
                strategy.on_published()
        else:
            did_publish = False

        maybe_persist(device_store, reg.persist_policy, did_publish, reg.name)

        last_error_type = self._clear_telemetry_error(
            reg.name, last_error_type, health_reporter
        )
        return last_published, last_error_type

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

            kwargs_arr[i] = resolve_kwargs(reg.injection_plan, providers_arr[i])
            strategy = reg.publish_strategy
            strategies[i] = strategy
            if strategy is not None:
                strategy._bind(ctx.clock)
            intervals_ms[i] = _to_ms(cast(float, reg.interval))
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
            device_stores=device_stores,
            strategies=strategies,
            last_published=last_published,
            last_error_type=last_error_type,
            intervals_ms=intervals_ms,
            heap=heap,
            sleep_ctx=sleep_ctx,
            epoch=epoch,
            active_stores=active_stores,
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
        device_stores: list[DeviceStore | None],
        strategies: list[PublishStrategy | None],
        last_published: list[dict[str, object] | None],
        last_error_type: list[type[Exception] | None],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        sleep_ctx: DeviceContext,
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
            try:
                result = await reg.func(**kwargs_arr[idx])
                (
                    last_published[idx],
                    last_error_type[idx],
                ) = await self._handle_telemetry_outcome(
                    reg,
                    ctx,
                    result,
                    strategies[idx],
                    last_published[idx],
                    last_error_type[idx],
                    health_reporter,
                    device_stores[idx],
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error_type[idx] = await self._handle_telemetry_error(
                    reg,
                    exc,
                    last_error_type[idx],
                    error_publisher,
                    health_reporter,
                )

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
