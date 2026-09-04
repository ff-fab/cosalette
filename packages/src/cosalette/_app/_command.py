"""Command mixin for the App class."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from cosalette._injection import build_injection_plan, detect_raw_mqtt_params
from cosalette._registration import (
    _UNSET,
    EnabledSpec,
    NameSpec,
    TimeoutSpec,
    _build_op_reg,
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    _Unset,
    _validate_init,
    check_device_name,
    warn_on_state_model_conflict,
)
from cosalette._runners._stream_types import BackpressurePolicy
from cosalette._utils import _callable_name, _callable_qualname

logger = logging.getLogger(__name__)


def _resolve_name_spec(
    name: str | Callable[..., Any] | None,
    func: Callable[..., Any],
) -> tuple[str, Callable[..., Any] | None]:
    """Return (resolved_name, name_spec) from a raw name argument."""
    if callable(name):
        return _callable_qualname(func), name
    return name or _callable_name(func), None


def _build_command_reg(
    name: str,
    func: Callable[..., Any],
    plan: list[tuple[str, type]],
    init: Callable[..., Any] | None,
    init_plan: list[tuple[str, type]] | None,
    declared_mqtt: frozenset[str],
    *,
    sub: str | None,
    sub_key: str,
    unavailable_on: tuple[type[Exception], ...] | None = None,
    **kw: Any,
) -> _CommandRegistration:
    # Single choke point for every command entry point — App.command,
    # App.add_command, Router.command, and both deferred-enabled variants.
    warn_on_state_model_conflict(func, kw.get("state_model"), name)
    return _build_op_reg(
        _CommandRegistration,
        name,
        func,
        plan,
        init,
        init_plan,
        mqtt_params=declared_mqtt,
        sub=sub,
        sub_key=sub_key,
        unavailable_on=unavailable_on,
        **kw,
    )


class _CommandMixin:
    """Mixin for command-related App methods."""

    _commands: list[_CommandRegistration]
    _devices: list[_DeviceRegistration]
    _telemetry: list[_TelemetryRegistration]
    _streams: list[_StreamRegistration]

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
        unavailable_on: tuple[type[Exception], ...] | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
    ) -> Callable[..., Any]:
        """Register a command handler for an MQTT device.

        The decorated function is called each time a command arrives
        on the ``{prefix}/{name}/set`` topic.  Parameters named
        ``topic`` and ``payload`` receive the MQTT message values;
        all other parameters are injected by type annotation, exactly
        like ``@app.device`` and ``@app.telemetry`` handlers.

        If the handler returns a ``dict``, the framework publishes it
        as device state via ``publish_state()``.  Return ``None`` to
        skip auto-publishing.

        When *name* is ``None``, the function name is used internally
        and the device publishes to root-level topics.

        Args:
            name: Device name used for MQTT topics and logging.  When
                ``None``, the function name is used internally and
                topics omit the device segment.  When a
                :data:`NameSpec` callable is provided, the framework
                calls it with the resolved ``Settings``.  Returning
                ``list[str]`` expands the registration into one command
                per name.  Returning ``dict[str, config]`` expands the
                registration into one command per key, and each dict
                value becomes the per-command config injected into the
                handler.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                the handler by type.
            enabled: When ``False``, registration is silently skipped.
                When a callable ``(Settings) -> bool``, the decision
                is deferred to the bootstrap phase after settings
                resolution.  Defaults to ``True``.
            sub: Optional sub-command value for routing multiple handlers
                on the same topic.  When provided, the JSON payload must
                contain a field (specified by *sub_key*) with this exact
                value for the handler to be invoked.  Multiple handlers
                can share the same topic name if they have different
                *sub* values.
            sub_key: JSON field name used for sub-command routing.
                Defaults to ``"command"``.  Only meaningful when *sub*
                is provided.
            summary: Optional human-readable description of what this
                command does.  Metadata only - does not affect
                runtime behavior.
            state_model: Optional type representing the expected
                device state structure.  Surfaced in introspection and,
                since 0.9.0, runtime load-bearing: the handler return
                value is validated and normalised against it, raising
                :exc:`~cosalette.ReturnValidationError` on a mismatch.
                It outranks the return annotation; a differently typed
                annotation warns at registration (ADR-068).
            payload_model: Optional type representing the expected
                command payload structure.  Metadata only - does not
                enforce runtime validation but is surfaced in
                introspection.
            behavior: Optional list of strings describing the command's
                behavior or operational steps.  Metadata only.
            effects: Optional list of strings describing the side
                effects this command produces.  Metadata only.
            unavailable_on: Optional tuple of exception types. When the handler
                raises any of these exceptions, the framework suppresses it,
                publishes "offline" to the device availability topic, and logs
                to the error topic. The device automatically returns "online"
                after the next successful handler invocation.
            timeout: Per-invocation timeout backstop in seconds. When the
                handler runs longer, it is cancelled and the resulting
                ``TimeoutError`` (a subclass of ``OSError`` per PEP 3151)
                is published to the device's error topic like any other
                handler error. Combine with ``unavailable_on=(TimeoutError,)``
                to mark the device offline on timeout. Three-state
                semantics (ADR-060): omitted — bounded default of
                ``_DEFAULT_COMMAND_TIMEOUT`` (30 s); explicit float —
                used as-is; a ``(Settings) -> float`` callable is
                resolved at bootstrap; ``None`` — explicitly unbounded.
            maxsize: Maximum command queue size. ``0`` (default) means unbounded
                (backward compatible). When ``> 0``, applies *backpressure* policy
                on queue full.
            backpressure: Policy applied when ``maxsize > 0`` and the queue is full.
                ``"drop_newest"`` (default) discards the incoming command,
                ``"drop_oldest"`` evicts the oldest queued command, ``"raise"``
                propagates :exc:`asyncio.QueueFull`. Ignored when ``maxsize=0``.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            TypeError: If any handler parameter lacks a type annotation.
        """
        # See device() for rationale on inspect vs asyncio.iscoroutinefunction.
        if callable(name) and inspect.iscoroutinefunction(name):
            raise TypeError(
                "Use @app.command(), not @app.command (parentheses required)"
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(enabled):
                self._register_deferred_command(
                    func,
                    name,
                    enabled,
                    init,
                    sub,
                    sub_key,
                    summary,
                    state_model,
                    payload_model,
                    behavior,
                    effects,
                    unavailable_on,
                    timeout,
                    maxsize,
                    backpressure,
                )
                return func
            effective_name = name if name is not None else _callable_name(func)
            self.add_command(
                effective_name,
                func,
                init=init,
                enabled=enabled,
                is_root=name is None,
                sub=sub,
                sub_key=sub_key,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
                unavailable_on=unavailable_on,
                timeout=timeout,
                maxsize=maxsize,
                backpressure=backpressure,
            )
            return func

        return decorator

    def _register_deferred_command(
        self,
        func: Callable[..., Any],
        name: str | Callable[..., Any] | None,
        enabled: EnabledSpec,
        init: Callable[..., Any] | None,
        sub: str | None,
        sub_key: str,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        unavailable_on: tuple[type[Exception], ...] | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
    ) -> None:
        """Append a deferred-enabled command registration for *func*."""
        init_plan = build_injection_plan(init) if init is not None else None
        raw_mqtt = detect_raw_mqtt_params(func)
        plan = build_injection_plan(func, mqtt_params=raw_mqtt)
        declared_mqtt = raw_mqtt
        resolved_name, name_spec = _resolve_name_spec(name, func)
        self._commands.append(
            _build_command_reg(
                resolved_name,
                func,
                plan,
                init,
                init_plan,
                declared_mqtt,
                is_root=not callable(name) and name is None,
                sub=sub,
                sub_key=sub_key,
                name_spec=name_spec,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
                enabled_spec=enabled,
                unavailable_on=unavailable_on,
                timeout=timeout,
                maxsize=maxsize,
                backpressure=backpressure,
            )
        )

    def add_command(
        self,
        name: str | Callable[..., Any],
        func: Callable[..., Awaitable[dict[str, object] | None]],
        *,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        is_root: bool = False,
        sub: str | None = None,
        sub_key: str = "command",
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
        unavailable_on: tuple[type[Exception], ...] | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
    ) -> None:
        """Register a command handler imperatively.

        This is the imperative counterpart to :meth:`command`.  It
        always creates a *named* (non-root) registration by default.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async callable invoked on each incoming command.
                Parameters named ``topic`` and ``payload`` receive the
                MQTT message values; others are injected by type.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                *func* by type.
            enabled: When ``False``, registration is silently skipped
                - no entry in the registry and no name slot reserved.
                Defaults to ``True``.
            is_root: When ``True``, the device publishes to root-level
                topics (``{prefix}/state`` instead of
                ``{prefix}/{name}/state``).  Defaults to ``False``.
            sub: Optional sub-command value for routing multiple handlers
                on the same topic.  When provided, the JSON payload must
                contain a field (specified by *sub_key*) with this exact
                value for the handler to be invoked.
            sub_key: JSON field name used for sub-command routing.
                Defaults to ``"command"``.  Only meaningful when *sub*
                is provided.
            timeout: Per-invocation timeout backstop in seconds
                (ADR-060).  Omitted — bounded default of
                ``_DEFAULT_COMMAND_TIMEOUT`` (30 s); explicit float —
                used as-is; callable — resolved against ``Settings`` at
                bootstrap; ``None`` — explicitly unbounded.
            maxsize: Maximum command queue size. ``0`` (default) means unbounded.
                When ``> 0``, applies *backpressure* policy on queue full.
            backpressure: Policy applied when ``maxsize > 0`` and the queue is full.
                ``"drop_newest"`` (default) discards the incoming command,
                ``"drop_oldest"`` evicts the oldest queued command, ``"raise"``
                propagates :exc:`asyncio.QueueFull`. Ignored when ``maxsize=0``.

        Raises:
            ValueError: If a device with this name is already registered.
            TypeError: If *init* is async or has un-annotated parameters.
            TypeError: If *func* has un-annotated parameters.

        See Also:
            :meth:`command` - decorator equivalent.
        """
        if not enabled:
            return
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        if not callable(name):
            check_device_name(
                name,
                registry_type="command",
                is_root=is_root,
                devices=self._devices,
                telemetry=self._telemetry,
                commands=self._commands,
                streams=self._streams,
                sub=sub,
                sub_key=sub_key,
            )
        raw_mqtt = detect_raw_mqtt_params(func)
        plan = build_injection_plan(func, mqtt_params=raw_mqtt)
        declared_mqtt = frozenset(raw_mqtt)
        resolved_name, name_spec = _resolve_name_spec(name, func)
        self._commands.append(
            _build_command_reg(
                resolved_name,
                func,
                plan,
                # Factory and injection
                init,
                init_plan,
                # MQTT topic/payload detection
                declared_mqtt,
                is_root=is_root,
                sub=sub,
                sub_key=sub_key,
                name_spec=name_spec,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
                unavailable_on=unavailable_on,
                timeout=timeout,
                maxsize=maxsize,
                backpressure=backpressure,
            ),
        )
