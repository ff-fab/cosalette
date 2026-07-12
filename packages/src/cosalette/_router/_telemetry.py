"""Telemetry mixin for the Router class."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any, cast

from cosalette._app._telemetry_validators import (
    has_interval,
    parse_schedule,
    prepare_schedule_spec,
    resolve_retry_defaults,
    resolve_telemetry_name_spec,
    validate_group_name,
    validate_imperative_schedule,
    validate_interval_schedule,
    validate_retry_args,
    validate_retry_on_elements,
    validate_schedule_spec_combinations,
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
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
    _Unset,
    _validate_init,
    check_device_name,
)
from cosalette._retry import BackoffStrategy, CircuitBreaker
from cosalette._strategies import PublishStrategy
from cosalette._utils import _callable_name, _callable_qualname


def _is_static_schedule(
    schedule: str | CronSchedule | CronSpec | None,
) -> bool:
    """Return True when *schedule* is a non-None, non-callable value."""
    return schedule is not None and not callable(schedule)


class _RouterTelemetryMixin:
    """Mixin for telemetry-related Router methods."""

    _devices: list[_DeviceRegistration]
    _telemetry: list[_TelemetryRegistration]
    _commands: list[_CommandRegistration]

    @abstractmethod
    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]: ...

    def _validate_interval_with_schedule(
        self,
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        group: str | None,
    ) -> None:
        """Validate combined interval + static-schedule constraints."""
        static = _is_static_schedule(schedule)
        if interval is not None and has_interval(interval) and static:
            _sched = cast("str | CronSchedule | None", schedule)
            validate_interval_schedule(interval, _sched, group)

    def _validate_imperative(
        self,
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        group: str | None,
    ) -> None:
        """Validate imperative (cron) schedule when present."""
        if _is_static_schedule(schedule):
            _sched = cast("str | CronSchedule | None", schedule)
            parsed_schedule_obj = parse_schedule(_sched)
            validate_imperative_schedule(
                interval if interval is not None else 0.0,
                parsed_schedule_obj,
                group,
            )

    def _validate_schedule_params(
        self,
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        group: str | None,
    ) -> None:
        """Extract schedule/interval validation logic."""
        self._validate_interval_with_schedule(interval, schedule, group)
        self._validate_imperative(interval, schedule, group)
        if group is not None:
            validate_group_name(group)

    def _validate_telemetry_params(
        self,
        name: str | NameSpec | None,
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        group: str | None,
        retry: int,
        retry_on: tuple[type[BaseException], ...] | None,
        timeout: TimeoutSpec | None | _Unset,
        triggerable: bool,
    ) -> None:
        """Extract early validation logic for telemetry parameters."""
        self._validate_schedule_params(interval, schedule, group)
        validate_retry_args(retry, retry_on)
        if retry_on is not None:
            validate_retry_on_elements(retry_on)
        validate_timeout(timeout)
        effective_name_for_validate = name if isinstance(name, str) else None
        is_root_for_validate = name is None or (
            not isinstance(name, str) and not callable(name)
        )
        validate_triggerable(
            triggerable,
            effective_name_for_validate,
            group,
            is_root=is_root_for_validate,
        )

    def _resolve_telemetry_registration_name(
        self,
        func: Callable[..., Any],
        name: str | NameSpec | None,
    ) -> tuple[str, NameSpec | None, bool]:
        """Resolve effective name, name spec, and root flag from *name* / *func*."""
        effective_name, name_spec = resolve_telemetry_name_spec(
            name if name is not None else _callable_name(func), func
        )
        is_root = effective_name == _callable_qualname(func)
        return effective_name, name_spec, is_root

    def _validate_telemetry_name_collision(
        self,
        name: str | NameSpec | None,
        effective_name: str,
        is_root: bool,
    ) -> None:
        """Check for name collisions when *name* is not callable."""
        if not callable(name):
            check_device_name(
                effective_name,
                registry_type="telemetry",
                is_root=is_root,
                devices=self._devices,
                telemetry=self._telemetry,
                commands=self._commands,
            )

    def _build_telemetry_decorator_body(
        self,
        func: Callable[..., Any],
        name: str | NameSpec | None,
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
        triggerable: bool,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        tags: list[str] | None,
    ) -> Callable[..., Any]:
        """Build telemetry registration and return func unchanged."""
        effective_name, name_spec, is_root = self._resolve_telemetry_registration_name(
            func, name
        )
        self._validate_telemetry_name_collision(name, effective_name, is_root)
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)

        schedule_spec, schedule_obj, _ = prepare_schedule_spec(
            interval, schedule, group
        )
        if callable(enabled):
            schedule_validation_name = (
                name_spec if name_spec is not None else effective_name
            )
            validate_schedule_spec_combinations(
                schedule_spec,
                schedule_validation_name,
                group,
                parsed_schedule=schedule_obj,
            )
        final_retry_on, final_backoff = resolve_retry_defaults(retry, retry_on, backoff)
        merged_tags = self._merge_tags(tags)

        reg = _TelemetryRegistration(
            name=effective_name,
            func=func,
            injection_plan=plan,
            interval=interval if interval is not None else 0.0,
            is_root=is_root,
            enabled_spec=enabled,
            publish_strategy=publish,
            persist_policy=persist,
            init=init,
            init_injection_plan=init_plan,
            group=group,
            name_spec=name_spec,
            retry=retry,
            retry_on=final_retry_on,
            backoff=final_backoff,
            circuit_breaker=circuit_breaker,
            timeout=timeout,
            schedule=schedule_obj,
            schedule_spec=schedule_spec,
            triggerable=triggerable,
            tags=tuple(merged_tags),
            summary=summary,
            state_model=state_model,
            payload_model=payload_model,
            behavior=behavior,
            effects=effects,
        )
        self._telemetry.append(reg)
        return func

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
        triggerable: bool = False,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a telemetry device with periodic polling.

        Extends ``App.telemetry`` with router-specific parameters
        (``tags``, ``dependencies``).

        Args:
            name: Device name for MQTT topics and logging.
            interval: Polling interval in seconds or a settings-derived callable.
            schedule: Cron schedule string or CronSchedule instance.
            publish: Strategy for conditional publishing.
            persist: Persistence policy.
            init: Optional synchronous factory called once before the handler.
            enabled: When ``False``, registration is skipped.
            group: Scheduler group name for coordinated polling.
            retry: Maximum retry attempts on handler failure.
            retry_on: Exception types to retry.
            backoff: Backoff strategy for retries.
            circuit_breaker: Circuit breaker for fault tolerance.
            triggerable: Whether this telemetry can be triggered manually.
            summary: One-line description for documentation.
            state_model: Type model for state payloads.
            payload_model: Type model for MQTT payloads.
            behavior: Phrases describing what the telemetry does.
            effects: Side effects produced by the telemetry.
            tags: Additional tags for this telemetry.
            dependencies: Reserved for cos-ebc.  Must be None or empty.

        Returns:
            The decorated function, unchanged.

        Raises:
            ValueError: If a telemetry with this name is already registered.
            NotImplementedError: If *dependencies* is not None or empty.
        """
        if dependencies is not None and len(dependencies) > 0:
            msg = (
                "dependencies= is reserved for the cos-ebc epic "
                "and is not yet implemented. Pass None or omit the parameter."
            )
            raise NotImplementedError(msg)

        self._validate_telemetry_params(
            name, interval, schedule, group, retry, retry_on, timeout, triggerable
        )

        if callable(enabled):
            return lambda func: self._build_telemetry_decorator_body(
                func,
                name,
                interval,
                schedule,
                publish,
                # Persistence and initialization configuration
                persist,
                init,
                enabled,
                # Group and retry behavior
                group,
                retry,
                retry_on,
                backoff,
                circuit_breaker,
                timeout,
                triggerable,
                # Summary and type models
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                tags,
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func
            return self._build_telemetry_decorator_body(
                func,
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
                timeout,
                triggerable,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                tags,
            )

        return decorator
