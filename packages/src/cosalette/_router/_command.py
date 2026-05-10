"""Command mixin for the Router class."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from cosalette._app._command import _build_command_reg, _resolve_name_spec
from cosalette._injection import build_injection_plan, detect_raw_mqtt_params
from cosalette._registration import (
    EnabledSpec,
    NameSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
    _validate_init,
    check_device_name,
)
from cosalette._utils import _callable_qualname


class _RouterCommandMixin:
    """Mixin for command-related Router methods."""

    _commands: list[_CommandRegistration]
    _devices: list[_DeviceRegistration]
    _telemetry: list[_TelemetryRegistration]

    @abstractmethod
    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]: ...

    def _build_command_decorator_body(
        self,
        func: Callable[..., Any],
        name: str | NameSpec | None,
        init: Callable[..., Any] | None,
        enabled: EnabledSpec,
        sub: str | None,
        sub_key: str,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        tags: list[str] | None,
    ) -> Callable[..., Any]:
        """Build command registration and return func unchanged."""
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
        _raw_mqtt = detect_raw_mqtt_params(func)
        declared_mqtt = frozenset(_raw_mqtt)
        plan = build_injection_plan(func, mqtt_params=set(declared_mqtt))
        merged_tags = self._merge_tags(tags)
        reg = _build_command_reg(
            effective_name,
            func,
            plan,
            # Init factory and plan
            init,
            init_plan,
            # Declared MQTT params
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

        Extends ``App.command`` with router-specific parameters
        (``tags``, ``dependencies``).

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
            return lambda func: self._build_command_decorator_body(
                func,
                name,
                init,
                enabled,
                sub,
                sub_key,
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
            return self._build_command_decorator_body(
                func,
                name,
                init,
                enabled,
                sub,
                sub_key,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                tags,
            )

        return decorator
