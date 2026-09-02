"""Telemetry mixin for the App class."""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from cosalette._app._telemetry_validators import (
    has_interval,
    parse_schedule,
    prepare_schedule_spec,
    resolve_retry_defaults,
    resolve_telemetry_name_spec,
    validate_group_name,
    validate_group_schedule_compat,
    validate_imperative_schedule,
    validate_interval_schedule,
    validate_min_interval,
    validate_retry_args,
    validate_retry_on_elements,
    validate_schedule_spec_combinations,
    validate_telemetry_args,
    validate_timeout,
    validate_triggerable,
)
from cosalette._cron import CronSchedule
from cosalette._injection import build_injection_plan
from cosalette._persistence._persist import PersistPolicy
from cosalette._registration import (
    _UNSET,
    CronSpec,
    EnabledSpec,
    IntervalSpec,
    NameSpec,
    TimeoutSpec,
    TriggerableSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    _Unset,
    check_device_name,
)
from cosalette._retry import (
    BackoffStrategy,
    CircuitBreaker,
)
from cosalette._runners._trigger import normalize_trigger_source
from cosalette._strategies import PublishStrategy
from cosalette._utils import _callable_name, _callable_qualname

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level shims for backward compat (tests may import these directly)
# ---------------------------------------------------------------------------

_validate_group_name = validate_group_name
_has_interval = has_interval
_validate_group_schedule_compat = validate_group_schedule_compat
_validate_retry_on_elements = validate_retry_on_elements
_resolve_telemetry_name_spec = resolve_telemetry_name_spec


class _TelemetryMixin:
    """Mixin for telemetry-related App methods."""

    _telemetry: list[_TelemetryRegistration]
    _devices: list[_DeviceRegistration]
    _commands: list[_CommandRegistration]
    _streams: list[_StreamRegistration]

    @property
    @abstractmethod
    def _store_configured(self) -> bool: ...

    def telemetry(
        self,
        name: str | NameSpec | None = None,
        *,
        interval: IntervalSpec | None = None,
        schedule: str | CronSchedule | CronSpec | None = None,
        publish: PublishStrategy | None = None,
        persist: PersistPolicy | None = None,
        init: Callable[..., Any] | None = None,
        enabled: EnabledSpec = True,
        group: str | None = None,
        retry: int = 0,
        retry_on: tuple[type[BaseException], ...] | None = None,
        backoff: BackoffStrategy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        triggerable: TriggerableSpec = False,
        min_interval: float | None = None,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> Callable[..., Any]:
        """Register a telemetry device with periodic polling.

        The decorated function returns a ``dict`` published as JSON
        state, or ``None`` to suppress publishing for that cycle.
        Parameters are injected based on type annotations — declare
        only what you need.  Zero-parameter handlers are valid.

        The framework calls the handler at the specified interval
        and publishes the returned dict (unless suppressed by a
        ``None`` return or a publish strategy).

        When *name* is ``None``, the function name is used internally
        and the device publishes to root-level topics.

        Args:
            name: Device name for MQTT topics and logging.  When
                ``None``, the function name is used internally and
                topics omit the device segment.  When a
                :data:`NameSpec` callable is provided, the framework
                calls it with the resolved ``Settings``.  Returning
                ``list[str]`` expands the registration into one
                telemetry device per name.  Returning
                ``dict[str, config]`` expands into one device per key,
                and each dict value becomes the per-device config
                injected into the handler and used for other deferred
                per-device resolution (e.g. callable ``interval=`` and
                ``schedule=``).
            interval: Polling interval in seconds, or a callable
                ``(Settings) -> float`` for deferred resolution.
                Mutually exclusive with ``schedule``.  One of
                ``interval`` or ``schedule`` is required — including
                for triggered entities, where it acts as a **heartbeat
                / fallback**: it refreshes the retained state topic
                even if the device never pushes again, and a missed
                push subscription still surfaces at the next tick.
            schedule: Cron expression (Quartz format, 6 or 7 fields),
                a :class:`CronSchedule` instance, or a per-device
                callable ``(config) -> str | CronSchedule`` for deferred
                per-device resolution.  The callable form requires
                ``name=callable`` (dict-based multi-device registration)
                and is mutually exclusive with ``interval=`` and
                ``group=``.  The plain expression/instance form is
                mutually exclusive with ``interval=``.  Example:
                ``"0 0/5 * * * ?"`` (every 5 minutes).
            publish: Optional publish strategy controlling when
                readings are actually published (e.g. ``OnChange()``,
                ``Every(seconds=60)``).  When ``None``, every reading
                is published unconditionally.
            persist: Optional save policy controlling when the
                :class:`DeviceStore` is persisted (e.g.
                ``SaveOnPublish()``, ``SaveOnChange()``).  Requires
                ``store=`` on the :class:`App`.  When ``None``, the
                store is saved only on shutdown (the safety net).
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                the handler by type.
            enabled: When ``False``, registration is silently skipped.
                When a callable ``(Settings) -> bool``, the decision
                is deferred to the bootstrap phase after settings
                resolution — constraints such as ``persist=`` requiring
                a store are validated only if the device ends up active.
                Defaults to ``True``.
            group: Optional coalescing group name.  Telemetry devices
                in the same group share a single scheduler tick so
                their readings are published together.  When ``None``
                (the default), the device runs on its own independent
                timer.
            retry: Maximum number of retry attempts after a failure.
                Defaults to ``0`` (no retry).  The retry counter
                persists across poll cycles and resets on success.
            retry_on: Exception types to retry on.  Defaults to
                ``(OSError,)`` when ``retry > 0`` and not explicitly
                set.  Exceptions not matching this tuple propagate
                immediately to the error handler.
            backoff: Backoff strategy controlling delay between retries
                (e.g. ``ExponentialBackoff()``, ``LinearBackoff()``,
                ``FixedBackoff()``).  Defaults to
                ``ExponentialBackoff(base=2.0, max_delay=60.0)`` when
                ``retry > 0`` and not explicitly set.
            circuit_breaker: Optional circuit breaker that stops
                retrying after consecutive failed cycles.  Works
                independently of ``retry`` — even with ``retry=0``,
                it tracks per-cycle failures.
            timeout: Per-invocation backstop for the handler await.
                When omitted, auto-defaults to the resolved poll
                ``interval`` (so every interval-based handler is
                protected).  Pass ``timeout=None`` to explicitly
                disable the backstop (for handlers that legitimately
                run longer than their interval).  A positive ``float``
                or callable ``(Settings) -> float`` sets an explicit
                limit; the callable is deferred-resolved at bootstrap,
                exactly like ``interval=``.  Cron-scheduled handlers
                get no auto-default.  A timed-out handler raises
                :exc:`TimeoutError` (a subclass of :exc:`OSError`)
                which composes automatically with ``retry`` when
                ``retry_on`` includes :exc:`OSError`.
            triggerable: Trigger source declaration (ADR-064).
                ``False`` (default) disables out-of-cycle runs.
                ``"mqtt"`` — or ``True``, its historical alias —
                subscribes ``{prefix}/{device}/set`` and runs the
                handler when a message arrives.  ``"local"`` arms the
                entity from inside the process through the injectable
                :class:`~cosalette.EntityNotifier`, with no MQTT topic
                involved.  ``"both"`` accepts either.  A triggered run
                uses the identical pipeline as a scheduled one
                (``publish=``, ``state_model=``, availability,
                persistence, error publication).  MQTT sources require
                a named device; ``"local"`` also works on a root
                device.  Composes with ``group=``: the arm wakes that
                member alone inside the group scheduler (ADR-067).
            min_interval: Optional storm throttle (ADR-066) bounding the
                minimum spacing in seconds between the *starts* of two
                trigger-initiated runs.  ``None`` (the default) is off.
                The first arm after a quiet window runs immediately
                (leading edge); arms landing inside a closed window
                coalesce into exactly one run carrying the last payload,
                fired when the window reopens (trailing edge) — nothing
                pushed is dropped.  The ``interval=`` heartbeat is never
                throttled and never consumes a pending arm.  Requires
                ``triggerable=``; must be a positive number.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            ValueError: If *interval* is a float and <= 0.  For
                callable intervals, validation is deferred to
                :meth:`_run_async`.
            ValueError: If both ``interval`` and ``schedule`` are
                provided, or neither is provided.
            ValueError: If ``persist`` is set but no ``store=`` backend
                was configured on the App (when ``enabled`` is a literal
                ``True``; deferred when ``enabled`` is callable).
            ValueError: If *group* is an empty string.
            ValueError: If ``retry > 0`` and ``retry_on`` is
                explicitly empty.
            ValueError: If *timeout* is a concrete non-finite or
                non-positive number.
            ValueError: If *timeout* is a concrete non-finite or
                non-positive number.
            ValueError: If *schedule* is a callable but *name* is a
                static string (no per-device config available).
            ValueError: If *schedule* is a callable and *group* is set.
            TypeError: If any handler parameter lacks a type annotation.
        """
        if callable(enabled):
            return self._make_deferred_telemetry_decorator(
                name,
                interval,
                schedule,
                publish,
                # State persistence and initialization
                persist,
                init,
                enabled,
                # Retry and error handling configuration
                group,
                retry,
                retry_on,
                backoff,
                circuit_breaker,
                timeout,
                triggerable,
                # Documentation and typing
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                min_interval=min_interval,
            )

        # Skip all validation when disabled — a disabled device shouldn't raise.
        if enabled:
            if group is not None and group == "":
                msg = "group must be non-empty"
                raise ValueError(msg)
            schedule_spec, parsed_schedule, effective_interval = prepare_schedule_spec(
                interval, schedule, group
            )
            if schedule_spec is None:
                # _prepare_schedule_spec returns (None, ...) only when schedule
                # is not callable, so CronSpec is excluded at this point.
                # cast() here is zero-cost: it tells the type-checker that the
                # CronSpec branch is already ruled out by the invariant above.
                _sched = cast("str | CronSchedule | None", schedule)
                validate_interval_schedule(interval, _sched, group)
                parsed_schedule = parse_schedule(_sched)
            # (add_telemetry re-checks for the imperative path).
            if persist is not None and not self._store_configured:
                msg = (
                    "persist= requires a store= backend on the App. "
                    "Pass store=MemoryStore() (or another Store) to App()."
                )
                raise ValueError(msg)
        else:
            schedule_spec = None
            parsed_schedule = None
            effective_interval = 0.0

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            effective_name = name if name is not None else _callable_name(func)
            self.add_telemetry(
                effective_name,
                func,
                interval=effective_interval,
                schedule=parsed_schedule,
                schedule_spec=schedule_spec,
                publish=publish,
                persist=persist,
                init=init,
                enabled=enabled,
                group=group,
                is_root=name is None,
                retry=retry,
                retry_on=retry_on,
                backoff=backoff,
                circuit_breaker=circuit_breaker,
                timeout=timeout,
                triggerable=triggerable,
                min_interval=min_interval,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            )
            return func

        return decorator

    def _make_deferred_telemetry_decorator(
        self,
        name: str | Callable[..., Any] | None,
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        publish: PublishStrategy | None,
        persist: PersistPolicy | None,
        init: Callable[..., Any] | None,
        enabled: EnabledSpec,
        group: str | None,
        retry: int,
        retry_on: tuple[type[BaseException], ...] | None,
        backoff: BackoffStrategy | None,
        circuit_breaker: CircuitBreaker | None,
        timeout: TimeoutSpec | None | _Unset,
        triggerable: TriggerableSpec,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        *,
        min_interval: float | None = None,
    ) -> Callable[..., Any]:
        """Validate and build a decorator for callable-enabled telemetry."""
        # Defer settings-dependent validation to resolve_enabled().
        # Still validate interval/schedule structure — independent of settings.
        if group is not None and group == "":
            msg = "group must be non-empty"
            raise ValueError(msg)
        validate_retry_args(retry, retry_on)
        validate_timeout(timeout)
        deferred_schedule_spec, parsed_schedule, effective_interval = (
            prepare_schedule_spec(interval, schedule, group)
        )
        if deferred_schedule_spec is None:
            # _prepare_schedule_spec returns (None, ...) only when schedule
            # is not callable, so CronSpec is excluded at this point.
            # cast() here is zero-cost: it tells the type-checker that the
            # CronSpec branch is already ruled out by the invariant above.
            _sched = cast("str | CronSchedule | None", schedule)
            validate_interval_schedule(interval, _sched, group)
            parsed_schedule = parse_schedule(_sched)
            effective_interval = interval if interval is not None else 0.0
        resolved_retry_on, resolved_backoff = resolve_retry_defaults(
            retry, retry_on, backoff
        )

        def decorator_deferred(func: Callable[..., Any]) -> Callable[..., Any]:
            self._build_deferred_telemetry_registration(
                func,
                name,
                effective_interval,
                parsed_schedule,
                publish,
                persist,
                init,
                enabled,
                group,
                retry,
                resolved_retry_on,
                resolved_backoff,
                circuit_breaker,
                timeout,
                triggerable,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                deferred_schedule_spec,
                min_interval=min_interval,
            )
            return func

        return decorator_deferred

    def _build_deferred_telemetry_registration(
        self,
        func: Callable[..., Any],
        name: str | Callable[..., Any] | None,
        effective_interval: IntervalSpec,
        parsed_schedule: CronSchedule | None,
        publish: PublishStrategy | None,
        persist: PersistPolicy | None,
        init: Callable[..., Any] | None,
        enabled: EnabledSpec,
        group: str | None,
        retry: int,
        resolved_retry_on: tuple[type[BaseException], ...],
        resolved_backoff: BackoffStrategy | None,
        circuit_breaker: CircuitBreaker | None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        triggerable: TriggerableSpec = False,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
        schedule_spec: CronSpec | None = None,
        *,
        min_interval: float | None = None,
    ) -> None:
        """Append a deferred-enabled telemetry registration for *func*."""
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        resolved_name = (
            _callable_qualname(func)
            if callable(name)
            else (name or _callable_name(func))
        )
        name_spec = name if callable(name) else None
        trigger_source = normalize_trigger_source(triggerable)
        validate_min_interval(min_interval, trigger_source, resolved_name)
        self._telemetry.append(
            _TelemetryRegistration(
                name=resolved_name,
                func=func,
                injection_plan=plan,
                interval=effective_interval,
                is_root=not callable(name) and name is None,
                enabled_spec=enabled,
                publish_strategy=publish,
                persist_policy=persist,
                init=init,
                init_injection_plan=init_plan,
                group=group,
                name_spec=name_spec,  # ty: ignore[invalid-argument-type]
                retry=retry,
                retry_on=resolved_retry_on,
                backoff=resolved_backoff,
                circuit_breaker=circuit_breaker,
                timeout=timeout,
                schedule=parsed_schedule,
                schedule_spec=schedule_spec,
                triggerable=trigger_source,
                min_interval=min_interval,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            ),
        )

    def _validate_telemetry_args(
        self,
        name: str | Callable[..., Any],
        interval: IntervalSpec,
        persist: PersistPolicy | None,
        init: Callable[..., Any] | None,
        group: str | None,
        retry: int = 0,
        retry_on: tuple[type[BaseException], ...] | None = None,
        schedule: CronSchedule | None = None,
        schedule_spec: CronSpec | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
    ) -> None:
        validate_telemetry_args(
            name,
            interval,
            persist,
            init,
            group,
            self._store_configured,
            retry=retry,
            retry_on=retry_on,
            schedule=schedule,
            schedule_spec=schedule_spec,
            timeout=timeout,
        )

    def add_telemetry(
        self,
        name: str | Callable[..., Any],
        func: Callable[..., Awaitable[dict[str, object] | None]],
        *,
        interval: IntervalSpec = 0.0,
        schedule: str | CronSchedule | None = None,
        schedule_spec: CronSpec | None = None,
        publish: PublishStrategy | None = None,
        persist: PersistPolicy | None = None,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        group: str | None = None,
        is_root: bool = False,
        retry: int = 0,
        retry_on: tuple[type[BaseException], ...] | None = None,
        backoff: BackoffStrategy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        triggerable: TriggerableSpec = False,
        min_interval: float | None = None,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> None:
        """Register a telemetry device imperatively.

        This is the imperative counterpart to :meth:`telemetry`.  It
        always creates a *named* (non-root) registration by default.

        Either ``interval`` or ``schedule`` must be provided.  They
        are mutually exclusive.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async callable returning a ``dict`` (published as
                state) or ``None`` (suppresses that cycle).
            interval: Polling interval in seconds, or a callable
                ``(Settings) -> float`` for deferred resolution.
                Mutually exclusive with ``schedule``.
            schedule: Cron expression (Quartz format, 6 or 7 fields)
                or a :class:`CronSchedule` instance.  The handler
                fires at times matching the expression.  Mutually
                exclusive with ``interval``.
            publish: Optional publish strategy (e.g. ``OnChange()``)
                controlling when readings are actually published.
            persist: Optional save policy.  Requires ``store=`` on the
                :class:`App`.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                *func* by type.
            enabled: When ``False``, registration is silently skipped
                — no entry in the registry and no name slot reserved.
                Defaults to ``True``.
            group: Optional coalescing group name.  Telemetry devices
                in the same group share a single scheduler tick so
                their readings are published together.  When ``None``
                (the default), the device runs on its own independent
                timer.
            is_root: When ``True``, the device publishes to root-level
                topics (``{prefix}/state`` instead of
                ``{prefix}/{name}/state``).  Defaults to ``False``.
            schedule_spec: Per-device callable ``(config) -> str | CronSchedule``
                for deferred schedule resolution.  **Internal** — set by the
                :meth:`telemetry` decorator when ``schedule=callable`` is used
                with ``name=callable``; not intended for direct use.  Requires
                ``name=callable``, incompatible with ``schedule=`` and
                ``group=``.
            triggerable: Trigger source declaration — ``False``,
                ``True``/``"mqtt"`` (inbound ``{prefix}/{device}/set``),
                ``"local"`` (in-process
                :class:`~cosalette.EntityNotifier`), or ``"both"``.
                MQTT sources require a named device; ``"local"`` also
                works with ``is_root=True``.  See :meth:`telemetry`
                for full semantics.
            min_interval: Optional storm throttle (ADR-066) bounding the
                minimum spacing in seconds between trigger-initiated
                run starts.  ``None`` (the default) is off.  Requires
                ``triggerable=``.  See :meth:`telemetry` for full
                semantics.
            timeout: Per-invocation backstop for the handler await.
                When omitted, auto-defaults to the resolved poll
                ``interval``.  Pass ``timeout=None`` to disable.  A
                positive ``float`` or settings-callable sets an
                explicit limit.  See :meth:`telemetry` for full
                semantics.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If *interval* is a float and <= 0 (when
                ``schedule`` is not set).
            ValueError: If both ``interval`` and ``schedule`` are
                provided (and interval is not the sentinel 0.0), or
                neither is provided.
            ValueError: If *persist* is set but no ``store=`` backend
                was configured on the App.
            ValueError: If *group* is an empty string.
            ValueError: If *timeout* is a concrete non-finite or
                non-positive number.
            ValueError: If ``schedule_spec`` is set but ``name`` is a
                static string, or combined with ``group=`` or
                ``schedule=``.
            TypeError: If *init* is async or has un-annotated parameters.
            TypeError: If *func* has un-annotated parameters.

        See Also:
            :meth:`telemetry` — decorator equivalent.
        """
        if not enabled:
            return

        trigger_source = normalize_trigger_source(triggerable)
        if not callable(name):
            validate_triggerable(triggerable, name, is_root)
        # else: deferred — validated per resolved device in expand_name_specs
        validate_min_interval(
            min_interval, trigger_source, name if isinstance(name, str) else None
        )

        parsed_schedule = parse_schedule(schedule)
        if schedule_spec is not None:
            validate_schedule_spec_combinations(
                schedule_spec, name, group, parsed_schedule
            )
            # Per-device callable schedule: skip imperative validation;
            # the schedule is resolved during name expansion.
            parsed_schedule = None
        else:
            validate_imperative_schedule(interval, parsed_schedule, group)

        self._validate_telemetry_args(
            name,
            interval,
            persist,
            init,
            group,
            retry=retry,
            retry_on=retry_on,
            schedule=parsed_schedule,
            schedule_spec=schedule_spec,
            timeout=timeout,
        )
        init_plan = build_injection_plan(init) if init is not None else None
        if not callable(name):
            check_device_name(
                name,
                registry_type="telemetry",
                is_root=is_root,
                devices=self._devices,
                telemetry=self._telemetry,
                commands=self._commands,
                streams=self._streams,
            )
        plan = build_injection_plan(func)
        resolved_name, name_spec = _resolve_telemetry_name_spec(name, func)

        resolved_retry_on, resolved_backoff = resolve_retry_defaults(
            retry,
            retry_on,
            backoff,
        )

        self._telemetry.append(
            _TelemetryRegistration(
                name=resolved_name,
                func=func,
                injection_plan=plan,
                interval=interval,
                is_root=is_root,
                publish_strategy=publish,
                persist_policy=persist,
                init=init,
                init_injection_plan=init_plan,
                group=group,
                name_spec=name_spec,
                retry=retry,
                retry_on=resolved_retry_on,
                backoff=resolved_backoff,
                circuit_breaker=circuit_breaker,
                timeout=timeout,
                schedule=parsed_schedule,
                schedule_spec=schedule_spec,
                triggerable=trigger_source,
                min_interval=min_interval,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            ),
        )
