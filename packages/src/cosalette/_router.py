"""Public Router class for composition-based cosalette applications.

The Router provides the same MQTT-native decorator surface as App but defers
registration until include_router() call time. This enables multi-module
composition without circular import dependencies.

See Also:
    ADR-044 — Public Router and composition API.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from cosalette._adapter_lifecycle import _AdapterEntry
from cosalette._app._command import _build_command_reg, _resolve_name_spec
from cosalette._app._device import (
    _build_device_reg,
)
from cosalette._app._device import (
    _resolve_name_spec as _resolve_device_name_spec,
)
from cosalette._app._helpers import (
    _check_no_port_in_signature,
    _collect_stream_params,
    _validate_periodic_early,
)
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
    validate_triggerable,
)
from cosalette._cron import CronSchedule
from cosalette._injection import build_injection_plan
from cosalette._periodic import _PeriodicRegistration
from cosalette._persistence._persist import PersistPolicy
from cosalette._registration import (
    CronSpec,
    EnabledSpec,
    IntervalSpec,
    NameSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _ReactorRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    _validate_init,
    check_device_name,
    validate_mqtt_name,
)
from cosalette._retry import BackoffStrategy, CircuitBreaker
from cosalette._strategies import PublishStrategy
from cosalette._stream import BackpressurePolicy
from cosalette._utils import _callable_name, _callable_qualname

if TYPE_CHECKING:
    import datetime


logger = logging.getLogger(__name__)


class Router:
    """Composition primitive for multi-module cosalette applications.

    Router provides the same MQTT-native decorator surface as App
    (telemetry, command, device, stream, periodic, react) and defers
    registration until ``App.include_router()`` call time.

    See Also:
        ADR-044 — Public Router and composition API.

    Example::

        # sensors.py
        router = cosalette.Router(prefix="sensors", tags=["environment"])

        @router.telemetry("temperature", interval=30)
        async def read_temperature() -> dict:
            return {"celsius": await sensor.read()}

        # main.py
        app = cosalette.App("bridge")
        app.include_router(router)
        # → publishes to: bridge/sensors/temperature/state
    """

    def __init__(
        self,
        *,
        prefix: str | None = None,
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
        adapters: dict[
            type,
            type
            | str
            | Callable[..., object]
            | tuple[
                type | str | Callable[..., object],
                type | str | Callable[..., object],
            ],
        ]
        | None = None,
    ) -> None:
        """Initialize a Router with optional shared metadata.

        Args:
            prefix: Single MQTT topic segment prepended to all operation
                names at include time.  Must not contain ``/``, ``+``,
                ``#``, or NUL.  When ``None``, no prefix is applied.
            tags: List of tags applied to all operations registered on
                this router.  Accumulates with include_router tags and
                operation-level tags.
            dependencies: Reserved for cos-ebc.  Must be ``None`` or empty.
            adapters: Adapter declarations in the same shape as
                ``App(adapters=...)``.  Merged into the app at include time.

        Raises:
            ValueError: If *prefix* contains MQTT special characters.
            NotImplementedError: If *dependencies* is not None or empty.
        """
        if prefix is not None:
            validate_mqtt_name(prefix)
        self._prefix = prefix
        self._tags = list(tags) if tags is not None else []

        if dependencies is not None and len(dependencies) > 0:
            msg = (
                "dependencies= is reserved for the cos-ebc epic "
                "and is not yet implemented. Pass None or omit the parameter."
            )
            raise NotImplementedError(msg)
        self._dependencies = dependencies

        self._devices: list[_DeviceRegistration] = []
        self._telemetry: list[_TelemetryRegistration] = []
        self._commands: list[_CommandRegistration] = []
        self._streams: list[_StreamRegistration] = []
        self._periodic: list[_PeriodicRegistration] = []
        self._reactors: list[_ReactorRegistration] = []

        self._adapters: dict[type, _AdapterEntry] = {}
        if adapters is not None:
            for port_type, value in adapters.items():
                if isinstance(value, tuple):
                    if len(value) != 2:  # noqa: PLR2004
                        msg = (
                            f"adapters value for {port_type!r} must be an impl "
                            f"or (impl, dry_run) 2-tuple, got {len(value)}-tuple"
                        )
                        raise ValueError(msg)
                    impl = cast(
                        type | str | Callable[..., object],
                        value[0],
                    )
                    dry_run_impl = cast(
                        type | str | Callable[..., object],
                        value[1],
                    )
                    self._register_adapter(port_type, impl, dry_run=dry_run_impl)
                else:
                    self._register_adapter(port_type, value)

    def _register_adapter(
        self,
        port_type: type,
        impl: type | str | Callable[..., object],
        *,
        dry_run: type | str | Callable[..., object] | None = None,
    ) -> None:
        """Register an adapter for a port type (internal).

        Args:
            port_type: The Protocol type to register.
            impl: The adapter class, lazy import string, or factory callable.
            dry_run: Optional dry-run variant.

        Raises:
            ValueError: If an adapter is already registered for this port type.
        """
        if port_type in self._adapters:
            msg = f"Adapter already registered for {port_type!r}"
            raise ValueError(msg)

        # Validate callable signatures at registration time
        for candidate in (impl, dry_run):
            if (
                candidate is not None
                and callable(candidate)
                and not isinstance(candidate, str)
            ):
                build_injection_plan(candidate)

        self._adapters[port_type] = _AdapterEntry(impl=impl, dry_run=dry_run)

    @property
    def registered_names(self) -> frozenset[str]:
        """All operation names registered on this router."""
        names: set[str] = set()
        for reg in self._devices:
            names.add(reg.name)
        for reg in self._telemetry:
            names.add(reg.name)
        for reg in self._commands:
            names.add(reg.name)
        for reg in self._streams:
            names.add(reg.name)
        for reg in self._periodic:
            names.add(reg.name)
        return frozenset(names)

    # -----------------------------------------------------------------------
    # Device decorator
    # -----------------------------------------------------------------------

    def device(
        self,
        name: str | NameSpec | None = None,
        *,
        init: Callable[..., Any] | None = None,
        enabled: EnabledSpec = True,
        summary: str | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a command & control device.

        Semantics match ``App.device``; see App documentation for details.

        Args:
            name: Device name for MQTT topics and logging.
            init: Optional synchronous factory called once before the handler.
            enabled: When ``False``, registration is skipped.
            summary: One-line description for documentation.
            behavior: Phrases describing what the device does.
            effects: Side effects produced by the device.
            tags: Additional tags for this device.
            dependencies: Reserved for cos-ebc.  Must be None or empty.

        Returns:
            The decorated function, unchanged.

        Raises:
            ValueError: If a device with this name is already registered.
            NotImplementedError: If *dependencies* is not None or empty.
        """
        if dependencies is not None and len(dependencies) > 0:
            msg = (
                "dependencies= is reserved for the cos-ebc epic "
                "and is not yet implemented. Pass None or omit the parameter."
            )
            raise NotImplementedError(msg)

        if callable(enabled):
            # Deferred: store spec, resolve at bootstrap
            def _deferred_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                effective_name, name_spec = _resolve_device_name_spec(name, func)
                is_root = effective_name == _callable_qualname(func)
                if not callable(name):
                    check_device_name(
                        effective_name,
                        registry_type="device",
                        is_root=is_root,
                        devices=self._devices,
                        telemetry=self._telemetry,
                        commands=self._commands,
                    )
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                plan = build_injection_plan(func)
                merged_tags = self._merge_tags(tags)
                reg = _build_device_reg(
                    effective_name,
                    func,
                    plan,
                    init,
                    init_plan,
                    is_root=is_root,
                    name_spec=name_spec,
                    enabled_spec=enabled,
                    tags=tuple(merged_tags),
                    summary=summary,
                    behavior=behavior,
                    effects=effects,
                )
                self._devices.append(reg)
                return func

            return _deferred_decorator

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func

            effective_name, name_spec = _resolve_device_name_spec(name, func)

            if init is not None:
                _validate_init(init)
            init_plan = build_injection_plan(init) if init is not None else None
            plan = build_injection_plan(func)

            is_root = effective_name == _callable_qualname(func)
            if not callable(name):
                check_device_name(
                    effective_name,
                    registry_type="device",
                    is_root=is_root,
                    devices=self._devices,
                    telemetry=self._telemetry,
                    commands=self._commands,
                )
            merged_tags = self._merge_tags(tags)

            reg = _build_device_reg(
                effective_name,
                func,
                plan,
                init,
                init_plan,
                is_root=is_root,
                name_spec=name_spec,
                enabled_spec=enabled,
                tags=tuple(merged_tags),
                summary=summary,
                behavior=behavior,
                effects=effects,
            )
            self._devices.append(reg)
            return func

        return decorator

    # -----------------------------------------------------------------------
    # Telemetry decorator
    # -----------------------------------------------------------------------

    def _validate_schedule_params(
        self,
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        group: str | None,
    ) -> None:
        """Extract schedule/interval validation logic."""
        if (
            interval is not None
            and has_interval(interval)
            and schedule is not None
            and not callable(schedule)
        ):
            validate_interval_schedule(interval, schedule, group)
        if schedule is not None and not callable(schedule):
            parsed_schedule_obj = parse_schedule(schedule)
            validate_imperative_schedule(
                interval if interval is not None else 0.0,
                parsed_schedule_obj,
                group,
            )
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
        triggerable: bool,
    ) -> None:
        """Extract early validation logic for telemetry parameters."""
        self._validate_schedule_params(interval, schedule, group)
        validate_retry_args(retry, retry_on)
        if retry_on is not None:
            validate_retry_on_elements(retry_on)
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
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a telemetry device with periodic polling.

        Semantics match ``App.telemetry``; see App documentation for details.

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

        # Consolidated early validation
        self._validate_telemetry_params(
            name, interval, schedule, group, retry, retry_on, triggerable
        )

        if callable(enabled):
            # Deferred: store spec, resolve at bootstrap
            def _deferred_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                effective_name, name_spec = resolve_telemetry_name_spec(
                    name if name is not None else _callable_name(func), func
                )
                is_root = effective_name == _callable_qualname(func)
                if not callable(name):
                    check_device_name(
                        effective_name,
                        registry_type="telemetry",
                        is_root=is_root,
                        devices=self._devices,
                        telemetry=self._telemetry,
                        commands=self._commands,
                    )
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                plan = build_injection_plan(func)
                is_root = effective_name == _callable_qualname(func)

                schedule_spec, schedule_obj, _ = prepare_schedule_spec(
                    interval, schedule, group
                )
                validate_schedule_spec_combinations(
                    schedule_spec,
                    name if name is not None else _callable_name(func),
                    group,
                    parsed_schedule=schedule_obj,
                )
                final_retry_on, final_backoff = resolve_retry_defaults(
                    retry, retry_on, backoff
                )
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
                    backoff=backoff,
                    circuit_breaker=circuit_breaker,
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

            return _deferred_decorator

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func

            effective_name, name_spec = resolve_telemetry_name_spec(
                name if name is not None else _callable_name(func), func
            )

            if init is not None:
                _validate_init(init)
            init_plan = build_injection_plan(init) if init is not None else None
            plan = build_injection_plan(func)

            is_root = effective_name == _callable_qualname(func)
            if not callable(name):
                check_device_name(
                    effective_name,
                    registry_type="telemetry",
                    is_root=is_root,
                    devices=self._devices,
                    telemetry=self._telemetry,
                    commands=self._commands,
                )

            schedule_spec, schedule_obj, _ = prepare_schedule_spec(
                interval, schedule, group
            )
            # Schedule spec validation done by prepare_schedule_spec

            final_retry_on, final_backoff = resolve_retry_defaults(
                retry, retry_on, backoff
            )
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
                backoff=backoff,
                circuit_breaker=circuit_breaker,
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

        return decorator

    # -----------------------------------------------------------------------
    # Command decorator
    # -----------------------------------------------------------------------

    def command(
        self,
        name: str | NameSpec | None = None,
        *,
        init: Callable[..., Any] | None = None,
        enabled: EnabledSpec = True,
        sub: str | None = None,
        sub_key: str = "command",
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a command handler for an MQTT device.

        Semantics match ``App.command``; see App documentation for details.

        Args:
            name: Device name for MQTT topics and logging.
            init: Optional synchronous factory called once before the handler.
            enabled: When ``False``, registration is skipped.
            sub: Sub-command value this handler owns.
            sub_key: JSON field used for routing (default: "command").
            summary: One-line description for documentation.
            state_model: Type model for state payloads.
            payload_model: Type model for MQTT payloads.
            behavior: Phrases describing what the command does.
            effects: Side effects produced by the command.
            tags: Additional tags for this command.
            dependencies: Reserved for cos-ebc.  Must be None or empty.

        Returns:
            The decorated function, unchanged.

        Raises:
            ValueError: If a command with this name is already registered.
            NotImplementedError: If *dependencies* is not None or empty.
        """
        if dependencies is not None and len(dependencies) > 0:
            msg = (
                "dependencies= is reserved for the cos-ebc epic "
                "and is not yet implemented. Pass None or omit the parameter."
            )
            raise NotImplementedError(msg)

        if callable(enabled):
            # Deferred: store spec, resolve at bootstrap
            def _deferred_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                effective_name, name_spec = _resolve_name_spec(name, func)
                is_root = effective_name == _callable_qualname(func)
                if not callable(name):
                    check_device_name(
                        effective_name,
                        registry_type="command",
                        is_root=is_root,
                        devices=self._devices,
                        telemetry=self._telemetry,
                        commands=self._commands,
                        sub=sub,
                        sub_key=sub_key,
                    )
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                sig = inspect.signature(func)
                declared_mqtt = frozenset(sig.parameters.keys()) & {
                    "topic",
                    "payload",
                }
                plan = build_injection_plan(func, mqtt_params=set(declared_mqtt))
                is_root = effective_name == _callable_qualname(func)
                merged_tags = self._merge_tags(tags)
                reg = _build_command_reg(
                    effective_name,
                    func,
                    plan,
                    init,
                    init_plan,
                    declared_mqtt,
                    is_root=is_root,
                    sub=sub,
                    sub_key=sub_key,
                    name_spec=name_spec,
                    tags=tuple(merged_tags),
                    summary=summary,
                    state_model=state_model,
                    payload_model=payload_model,
                    behavior=behavior,
                    effects=effects,
                    enabled_spec=enabled,
                )
                self._commands.append(reg)
                return func

            return _deferred_decorator

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func

            effective_name, name_spec = _resolve_name_spec(name, func)

            if init is not None:
                _validate_init(init)
            init_plan = build_injection_plan(init) if init is not None else None
            sig = inspect.signature(func)
            declared_mqtt = frozenset(sig.parameters.keys()) & {"topic", "payload"}
            plan = build_injection_plan(func, mqtt_params=set(declared_mqtt))

            is_root = effective_name == _callable_qualname(func)
            if not callable(name):
                check_device_name(
                    effective_name,
                    registry_type="command",
                    is_root=is_root,
                    devices=self._devices,
                    telemetry=self._telemetry,
                    commands=self._commands,
                    sub=sub,
                    sub_key=sub_key,
                )
            merged_tags = self._merge_tags(tags)

            reg = _build_command_reg(
                effective_name,
                func,
                plan,
                init,
                init_plan,
                declared_mqtt,
                is_root=is_root,
                sub=sub,
                sub_key=sub_key,
                name_spec=name_spec,
                tags=tuple(merged_tags),
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
                enabled_spec=enabled,
            )
            self._commands.append(reg)
            return func

        return decorator

    # -----------------------------------------------------------------------
    # Stream decorator
    # -----------------------------------------------------------------------

    def stream(
        self,
        name: str | None = None,
        *,
        enabled: EnabledSpec = True,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        summary: str | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a streaming handler for push-to-pull data bridging.

        Semantics match ``App.stream``; see App documentation for details.

        Args:
            name: Device name for MQTT topics and logging.
            enabled: When ``False``, registration is skipped.
            maxsize: Maximum number of items buffered in the internal Stream queue.
            backpressure: Policy applied when maxsize > 0 and the queue is full.
            summary: One-line description for documentation.
            behavior: Phrases describing what the handler does.
            effects: Side effects produced by the handler.
            tags: Additional tags for this stream.
            dependencies: Reserved for cos-ebc.  Must be None or empty.

        Returns:
            The decorated function, unchanged.

        Raises:
            TypeError: If the function lacks a Stream[T] parameter.
            NotImplementedError: If *dependencies* is not None or empty.
        """
        if dependencies is not None and len(dependencies) > 0:
            msg = (
                "dependencies= is reserved for the cos-ebc epic "
                "and is not yet implemented. Pass None or omit the parameter."
            )
            raise NotImplementedError(msg)

        if callable(enabled):
            return self._make_deferred_stream_decorator(
                name,
                enabled,
                maxsize,
                backpressure,
                summary,
                behavior,
                effects,
                tags,
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func

            effective_name = name if name is not None else _callable_name(func)
            if effective_name in self.registered_names:
                msg = (
                    f"Stream handler name {effective_name!r} already registered "
                    f"as {self._name_to_kind(effective_name)}"
                )
                raise ValueError(msg)

            from typing import get_type_hints

            try:
                hints = get_type_hints(func)
            except (NameError, AttributeError) as e:
                msg = f"Cannot resolve type hints for {_callable_qualname(func)}: {e}"
                raise TypeError(msg) from e

            stream_params = _collect_stream_params(func, hints)
            if not stream_params:
                msg = (
                    f"Function {_callable_qualname(func)}"
                    " must declare a Stream[T] parameter"
                )
                raise TypeError(msg)
            _, item_type = stream_params[0]
            _check_no_port_in_signature(func, hints, item_type)

            # Stream adapter validation is deferred to App startup (cos-s2q.4)
            # Router only records the registration; no adapter check here.

            plan = build_injection_plan(func)
            is_root = effective_name == _callable_qualname(func)
            merged_tags = self._merge_tags(tags)

            reg = _StreamRegistration(
                name=effective_name,
                func=func,
                injection_plan=plan,
                enabled_spec=enabled,
                is_root=is_root,
                maxsize=maxsize,
                backpressure=backpressure,
                tags=tuple(merged_tags),
                summary=summary,
                behavior=behavior,
                effects=effects,
            )
            self._streams.append(reg)
            return func

        return decorator

    def _make_deferred_stream_decorator(
        self,
        name: str | None,
        enabled: EnabledSpec,
        maxsize: int,
        backpressure: BackpressurePolicy,
        summary: str | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        tags: list[str] | None,
    ) -> Callable[..., Any]:
        """Build a deferred stream decorator (enabled= is a callable)."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            effective_name = name if name is not None else _callable_name(func)
            if effective_name in self.registered_names:
                msg = (
                    f"Stream handler name {effective_name!r} already registered "
                    f"as {self._name_to_kind(effective_name)}"
                )
                raise ValueError(msg)

            from typing import get_type_hints

            try:
                hints = get_type_hints(func)
            except (NameError, AttributeError) as e:
                msg = f"Cannot resolve type hints for {_callable_qualname(func)}: {e}"
                raise TypeError(msg) from e

            stream_params = _collect_stream_params(func, hints)
            if not stream_params:
                msg = (
                    f"Function {_callable_qualname(func)}"
                    " must declare a Stream[T] parameter"
                )
                raise TypeError(msg)
            _, item_type = stream_params[0]
            _check_no_port_in_signature(func, hints, item_type)

            plan = build_injection_plan(func)
            is_root = effective_name == _callable_qualname(func)
            merged_tags = self._merge_tags(tags)

            reg = _StreamRegistration(
                name=effective_name,
                func=func,
                injection_plan=plan,
                enabled_spec=enabled,
                is_root=is_root,
                maxsize=maxsize,
                backpressure=backpressure,
                tags=tuple(merged_tags),
                summary=summary,
                behavior=behavior,
                effects=effects,
            )
            self._streams.append(reg)
            return func

        return decorator

    # -----------------------------------------------------------------------
    # Periodic decorator
    # -----------------------------------------------------------------------

    def periodic(
        self,
        name: str | None = None,
        *,
        interval: IntervalSpec | datetime.timedelta,
        enabled: EnabledSpec = True,
        init: Callable[..., Any] | None = None,
        summary: str | None = None,
        behavior: list[str] | None = None,
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a background periodic task.

        Semantics match ``App.periodic``; see App documentation for details.

        Args:
            name: Task name for logging.
            interval: Polling interval in seconds, a timedelta, or a
                callable ``(Settings) -> float``.
            enabled: When ``False``, registration is skipped.
            init: Optional synchronous factory called once before the handler.
            summary: One-line description for documentation.
            behavior: Phrases describing what the task does.
            tags: Additional tags for this periodic task.
            dependencies: Reserved for cos-ebc.  Must be None or empty.

        Returns:
            The decorated function, unchanged.

        Raises:
            ValueError: If a task with this name is already registered.
            NotImplementedError: If *dependencies* is not None or empty.
        """
        import datetime

        if dependencies is not None and len(dependencies) > 0:
            msg = (
                "dependencies= is reserved for the cos-ebc epic "
                "and is not yet implemented. Pass None or omit the parameter."
            )
            raise NotImplementedError(msg)

        # Normalize timedelta to float immediately
        if isinstance(interval, datetime.timedelta):
            interval = interval.total_seconds()

        if callable(enabled):
            # Deferred: store spec, resolve at bootstrap
            def _deferred_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                effective_name = name if name is not None else _callable_name(func)
                _validate_periodic_early(
                    effective_name, self.registered_names, interval
                )
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                plan = build_injection_plan(func)
                merged_tags = self._merge_tags(tags)
                self._periodic.append(
                    _PeriodicRegistration(
                        name=effective_name,
                        func=func,
                        injection_plan=plan,
                        interval=interval,
                        enabled_spec=enabled,
                        init=init,
                        init_injection_plan=init_plan,
                        tags=tuple(merged_tags),
                        summary=summary,
                        behavior=behavior,
                    )
                )
                return func

            return _deferred_decorator

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func

            effective_name = name if name is not None else _callable_name(func)
            _validate_periodic_early(effective_name, self.registered_names, interval)

            if init is not None:
                _validate_init(init)
            init_plan = build_injection_plan(init) if init is not None else None
            plan = build_injection_plan(func)
            merged_tags = self._merge_tags(tags)

            reg = _PeriodicRegistration(
                name=effective_name,
                func=func,
                injection_plan=plan,
                interval=interval,
                enabled_spec=enabled,
                init=init,
                init_injection_plan=init_plan,
                tags=tuple(merged_tags),
                summary=summary,
                behavior=behavior,
            )
            self._periodic.append(reg)
            return func

        return decorator

    # -----------------------------------------------------------------------
    # React decorator
    # -----------------------------------------------------------------------

    def react(
        self,
        state_type: type,
        *,
        drain: Callable[[Any], Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a reactor for domain events from a state object.

        Semantics match ``App.react``; see App documentation for details.
        Validation that ``state_type`` is registered via ``@app.state``
        is deferred until ``include_router()`` call time.

        Args:
            state_type: The state type registered via ``@app.state`` to
                watch for events.  Must be registered on the app when
                ``include_router`` is called.
            drain: Optional drain callable to invoke on the state instance.
                When ``None``, the framework looks for a ``drain_events()``
                method on the state instance.

        Returns:
            The decorated function, registered as a reactor.

        Raises:
            TypeError: If the decorated function is not async.

        Note:
            Unlike ``App.react``, this method defers validation of
            ``state_type`` registration until ``include_router`` time,
            as the router has no access to app state factories.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not inspect.iscoroutinefunction(func):
                msg = f"Reactor function {_callable_qualname(func)!r} must be async"
                raise TypeError(msg)

            # Detect if function declares 'events' parameter
            sig = inspect.signature(func)
            events_param = "events" if "events" in sig.parameters else None

            # Build injection plan, skipping 'events' if present
            reserved_params = {"events"} if events_param else set()
            injection_plan = build_injection_plan(func, mqtt_params=reserved_params)

            registration = _ReactorRegistration(
                state_type=state_type,
                func=func,
                injection_plan=injection_plan,
                drain=drain,
                events_param=events_param,
            )

            self._reactors.append(registration)
            return func

        return decorator

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]:
        """Merge router constructor tags with operation-level tags.

        Order: Router constructor → operation decorator.
        Deduplicate while preserving first occurrence.
        """
        tags = list(self._tags)  # Router constructor tags
        if operation_tags is not None:
            for tag in operation_tags:
                if tag not in tags:
                    tags.append(tag)
        return tags

    def _name_to_kind(self, name: str) -> str:
        """Return the kind of registration for a given name."""
        # Table-driven lookup reduces cyclomatic complexity
        registries = [
            (self._devices, "device"),
            (self._telemetry, "telemetry"),
            (self._commands, "command"),
            (self._streams, "stream"),
            (self._periodic, "periodic"),
        ]
        for registry, kind in registries:
            if any(reg.name == name for reg in registry):
                return kind
        return "unknown"
