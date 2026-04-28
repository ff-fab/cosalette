"""Telemetry mixin for the App class."""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from cosalette._cron import CronSchedule
from cosalette._injection import build_injection_plan
from cosalette._persist import PersistPolicy
from cosalette._registration import (
    CronSpec,
    EnabledSpec,
    IntervalSpec,
    NameSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
    _validate_init,
    check_device_name,
)
from cosalette._retry import (
    _DEFAULT_BACKOFF,
    _DEFAULT_RETRY_ON,
    BackoffStrategy,
    CircuitBreaker,
)
from cosalette._strategies import PublishStrategy
from cosalette._utils import _callable_name, _callable_qualname

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)


class _TelemetryMixin:
    """Mixin for telemetry-related App methods."""

    _telemetry: list[_TelemetryRegistration]
    _devices: list[_DeviceRegistration]
    _commands: list[_CommandRegistration]

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
        triggerable: bool = False,
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
                ``interval`` or ``schedule`` is required.
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
            triggerable: When ``True``, the framework subscribes to
                ``{prefix}/{device}/set`` and triggers an immediate
                out-of-cycle execution when a message arrives.  The
                handler runs through the same pipeline as scheduled
                runs.  Requires a named device (not root).  Defaults
                to ``False``.
            summary: Optional human-readable description of what this
                telemetry device measures or reports.  Metadata only —
                does not affect runtime behavior.
            state_model: Optional type representing the expected
                payload structure.  Metadata only — does not enforce
                runtime validation but is surfaced in introspection.
            behavior: Optional list of strings describing the device's
                behavior or operational steps.  Metadata only.
            payload_model: Optional type representing the expected
                command payload structure.  Metadata only — does not
                enforce runtime validation but is surfaced in
                introspection.
            effects: Optional list of strings describing the side
                effects this telemetry might trigger.  Metadata only.

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
                persist,
                init,
                enabled,
                group,
                retry,
                retry_on,
                backoff,
                circuit_breaker,
                triggerable,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
            )

        # Skip all validation when disabled — a disabled device shouldn't raise.
        if enabled:
            if group is not None and group == "":
                msg = "group must be non-empty"
                raise ValueError(msg)
            schedule_spec, parsed_schedule, effective_interval = (
                self._prepare_schedule_spec(interval, schedule, group)
            )
            if schedule_spec is None:
                # _prepare_schedule_spec returns (None, ...) only when schedule
                # is not callable, so CronSpec is excluded at this point.
                # cast() here is zero-cost: it tells the type-checker that the
                # CronSpec branch is already ruled out by the invariant above.
                _sched = cast("str | CronSchedule | None", schedule)
                self._validate_interval_schedule(interval, _sched, group)
                parsed_schedule = self._parse_schedule(_sched)
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
                triggerable=triggerable,
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
        triggerable: bool,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
    ) -> Callable[..., Any]:
        """Validate and build a decorator for callable-enabled telemetry."""
        # Defer settings-dependent validation to resolve_enabled().
        # Still validate interval/schedule structure — independent of settings.
        if group is not None and group == "":
            msg = "group must be non-empty"
            raise ValueError(msg)
        self._validate_retry_args(retry, retry_on)
        deferred_schedule_spec, parsed_schedule, effective_interval = (
            self._prepare_schedule_spec(interval, schedule, group)
        )
        if deferred_schedule_spec is None:
            # _prepare_schedule_spec returns (None, ...) only when schedule
            # is not callable, so CronSpec is excluded at this point.
            # cast() here is zero-cost: it tells the type-checker that the
            # CronSpec branch is already ruled out by the invariant above.
            _sched = cast("str | CronSchedule | None", schedule)
            self._validate_interval_schedule(interval, _sched, group)
            parsed_schedule = self._parse_schedule(_sched)
            effective_interval = interval if interval is not None else 0.0
        resolved_retry_on, resolved_backoff = self._resolve_retry_defaults(
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
                triggerable,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                deferred_schedule_spec,
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
        triggerable: bool,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        schedule_spec: CronSpec | None = None,
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
                schedule=parsed_schedule,
                schedule_spec=schedule_spec,
                triggerable=triggerable,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            ),
        )

    @staticmethod
    def _validate_triggerable(
        triggerable: bool,
        name: str | None,
        group: str | None,
        is_root: bool = False,
    ) -> None:
        """Raise ValueError for invalid triggerable combinations."""
        if not triggerable:
            return
        if name is None or is_root:
            msg = "triggerable=True requires a named device (name= must be set)"
            raise ValueError(msg)
        if group is not None:
            msg = (
                "triggerable= and group= cannot be combined"
                " (coalescing groups use a shared scheduler)"
            )
            raise ValueError(msg)

    @staticmethod
    def _parse_schedule(
        schedule: str | CronSchedule | None,
    ) -> CronSchedule | None:
        """Parse a schedule string or pass through a CronSchedule."""
        if isinstance(schedule, str):
            return CronSchedule(schedule)
        if isinstance(schedule, CronSchedule):
            return schedule
        return None

    @staticmethod
    def _prepare_schedule_spec(
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        group: str | None,
    ) -> tuple[CronSpec | None, CronSchedule | None, IntervalSpec]:
        """Normalise schedule arguments for callable-name telemetry.

        Returns ``(schedule_spec, parsed_schedule, effective_interval)``.
        When *schedule* is a callable (per-device spec), schedule_spec is set and
        parsed_schedule is ``None``; mutual-exclusivity with interval/group is
        validated eagerly.
        """
        if not callable(schedule):
            return None, None, interval if interval is not None else 0.0
        if interval is not None:
            msg = "interval= and schedule= are mutually exclusive"
            raise ValueError(msg)
        if group is not None:
            msg = (
                "schedule= and group= cannot be combined"
                " (coalescing groups require interval=)"
            )
            raise ValueError(msg)
        return schedule, None, 0.0  # ty: ignore[invalid-return-type]

    @staticmethod
    def _validate_interval_schedule(
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | None,
        group: str | None = None,
    ) -> None:
        """Validate interval/schedule mutual exclusivity and group compat."""
        if interval is not None and schedule is not None:
            msg = "interval= and schedule= are mutually exclusive"
            raise ValueError(msg)
        if interval is None and schedule is None:
            msg = "Either interval= or schedule= is required"
            raise ValueError(msg)
        if schedule is not None and group is not None:
            msg = (
                "schedule= and group= cannot be combined"
                " (coalescing groups require interval=)"
            )
            raise ValueError(msg)

    @staticmethod
    def _validate_imperative_schedule(
        interval: IntervalSpec,
        parsed_schedule: CronSchedule | None,
        group: str | None = None,
    ) -> None:
        """Validate interval/schedule mutual exclusivity (imperative path)."""
        has_interval = interval != 0.0 or callable(interval)
        if has_interval and parsed_schedule is not None:
            msg = "interval= and schedule= are mutually exclusive"
            raise ValueError(msg)
        if not has_interval and parsed_schedule is None:
            msg = "Either interval= or schedule= is required"
            raise ValueError(msg)
        if parsed_schedule is not None and group is not None:
            msg = (
                "schedule= and group= cannot be combined"
                " (coalescing groups require interval=)"
            )
            raise ValueError(msg)

    @staticmethod
    def _resolve_retry_defaults(
        retry: int,
        retry_on: tuple[type[BaseException], ...] | None,
        backoff: BackoffStrategy | None,
    ) -> tuple[tuple[type[BaseException], ...], BackoffStrategy | None]:
        """Apply default retry_on and backoff when retry > 0."""
        if retry > 0:
            if retry_on is None:
                retry_on = _DEFAULT_RETRY_ON
            if backoff is None:
                backoff = _DEFAULT_BACKOFF
        return retry_on if retry_on is not None else (), backoff

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
    ) -> None:
        if group is not None and group == "":
            msg = "group must be non-empty"
            raise ValueError(msg)
        if persist is not None and not self._store_configured:
            msg = (
                "persist= requires a store= backend on the App. "
                "Pass store=MemoryStore() (or another Store) to App()."
            )
            raise ValueError(msg)
        if init is not None:
            _validate_init(init)
        self._validate_schedule_spec_combinations(schedule_spec, name, group)
        # Skip interval validation when schedule is set (interval is sentinel 0.0)
        if self._interval_is_invalid(schedule, schedule_spec, name, interval):
            msg = f"Telemetry interval must be positive, got {interval}"
            raise ValueError(msg)
        self._validate_retry_args(retry, retry_on)

    @staticmethod
    def _interval_is_invalid(
        schedule: CronSchedule | None,
        schedule_spec: CronSpec | None,
        name: str | Callable[..., Any],
        interval: IntervalSpec,
    ) -> bool:
        has_schedule = schedule is not None or schedule_spec is not None
        is_static_name = not callable(name)
        is_static_interval = not callable(interval)
        return (
            not has_schedule and is_static_name and is_static_interval and interval <= 0  # ty: ignore[unsupported-operator]
        )

    @staticmethod
    def _validate_schedule_spec_combinations(
        schedule_spec: CronSpec | None,
        name: str | Callable[..., Any],
        group: str | None,
        parsed_schedule: CronSchedule | None = None,
    ) -> None:
        if schedule_spec is None:
            return
        if not callable(name):
            msg = (
                "schedule= callable requires name= to be a callable "
                "(per-device dict/list spec).  "
                "Static names have no per-device config to pass to the callable."
            )
            raise ValueError(msg)
        if group is not None:
            msg = "schedule= callable cannot be combined with group="
            raise ValueError(msg)
        if parsed_schedule is not None:
            msg = "schedule_spec= cannot be combined with schedule="
            raise ValueError(msg)

    @staticmethod
    def _validate_retry_args(
        retry: int,
        retry_on: tuple[type[BaseException], ...] | None,
    ) -> None:
        if not isinstance(retry, int) or retry < 0:
            msg = f"retry must be a non-negative integer, got {retry!r}"
            raise ValueError(msg)
        if retry > 0 and retry_on is not None and retry_on == ():
            msg = "retry > 0 with retry_on=() is invalid (nothing would be retried)"
            raise ValueError(msg)
        if retry_on is not None:
            for exc_type in retry_on:
                if not isinstance(exc_type, type) or not issubclass(
                    exc_type, BaseException
                ):
                    msg = f"retry_on elements must be exception types, got {exc_type!r}"
                    raise TypeError(msg)

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
        triggerable: bool = False,
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
            triggerable: When ``True``, the framework subscribes to
                ``{prefix}/{device}/set`` and triggers an immediate
                out-of-cycle execution when a message arrives.  The
                handler runs through the same pipeline as scheduled
                runs.  Requires a named device (not root).  Defaults
                to ``False``.

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

        if triggerable and is_root:
            msg = (
                "triggerable= and is_root=True cannot be combined"
                " (no named topic to subscribe to)"
            )
            raise ValueError(msg)
        if not callable(name):
            self._validate_triggerable(triggerable, str(name), group)
        # else: deferred — validated per resolved device in expand_name_specs

        parsed_schedule = self._parse_schedule(schedule)
        if schedule_spec is not None:
            self._validate_schedule_spec_combinations(
                schedule_spec, name, group, parsed_schedule
            )
            # Per-device callable schedule: skip imperative validation;
            # the schedule is resolved during name expansion.
            parsed_schedule = None
        else:
            self._validate_imperative_schedule(interval, parsed_schedule, group)

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
            )
        plan = build_injection_plan(func)
        resolved_name = _callable_qualname(func) if callable(name) else name
        name_spec = name if callable(name) else None

        resolved_retry_on, resolved_backoff = self._resolve_retry_defaults(
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
                name_spec=name_spec,  # ty: ignore[invalid-argument-type]
                retry=retry,
                retry_on=resolved_retry_on,
                backoff=resolved_backoff,
                circuit_breaker=circuit_breaker,
                schedule=parsed_schedule,
                schedule_spec=schedule_spec,
                triggerable=triggerable,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            ),
        )
