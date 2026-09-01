"""Command mixin for the Router class."""

from __future__ import annotations

import inspect
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from cosalette._app._command import _build_command_reg, _resolve_name_spec
from cosalette._injection import build_injection_plan, detect_raw_mqtt_params
from cosalette._registration import (
    _UNSET,
    EnabledSpec,
    NameSpec,
    TimeoutSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    _Unset,
    _validate_init,
    check_device_name,
)
from cosalette._runners._stream_types import BackpressurePolicy


class _RouterCommandMixin:
    """Mixin for command-related Router methods."""

    _commands: list[_CommandRegistration]
    _devices: list[_DeviceRegistration]
    _telemetry: list[_TelemetryRegistration]
    _streams: list[_StreamRegistration]

    @abstractmethod
    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]: ...

    def _resolve_command_registration_name(
        self,
        func: Callable[..., Any],
        name: str | NameSpec | None,
    ) -> tuple[str, NameSpec | None, bool]:
        """Resolve effective name, name spec, and root flag from *name* / *func*."""
        effective_name, name_spec = _resolve_name_spec(name, func)
        return effective_name, name_spec, name is None

    def _validate_command_name_collision(
        self,
        name: str | NameSpec | None,
        effective_name: str,
        is_root: bool,
        sub: str | None,
        sub_key: str,
    ) -> None:
        """Check for name collisions when *name* is not callable."""
        if not callable(name):
            check_device_name(
                effective_name,
                registry_type="command",
                is_root=is_root,
                devices=self._devices,
                telemetry=self._telemetry,
                commands=self._commands,
                streams=self._streams,
                sub=sub,
                sub_key=sub_key,
            )

    def _command_reg_kwargs(
        self,
        *,
        is_root: bool,
        name_spec: NameSpec | None,
        tags: list[str] | None,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        enabled: EnabledSpec,
        unavailable_on: tuple[type[Exception], ...] | None,
        timeout: TimeoutSpec | None | _Unset,
        maxsize: int,
        backpressure: BackpressurePolicy,
        sub: str | None,
        sub_key: str,
    ) -> dict[str, Any]:
        """Return shared registration kwargs for router command records."""
        router_tags = tuple(self._merge_tags(tags))
        return {
            "is_root": is_root,
            "sub": sub,
            "sub_key": sub_key,
            "name_spec": name_spec,
            "tags": router_tags,
            "summary": summary,
            "state_model": state_model,
            "payload_model": payload_model,
            "behavior": behavior,
            "effects": effects,
            "enabled_spec": enabled,
            "unavailable_on": unavailable_on,
            "timeout": timeout,
            "maxsize": maxsize,
            "backpressure": backpressure,
        }

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
        unavailable_on: tuple[type[Exception], ...] | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
    ) -> Callable[..., Any]:
        """Build command registration and return func unchanged."""
        effective_name, name_spec, is_root = self._resolve_command_registration_name(
            func, name
        )
        self._validate_command_name_collision(
            name, effective_name, is_root, sub, sub_key
        )
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        _raw_mqtt = detect_raw_mqtt_params(func)
        declared_mqtt = frozenset(_raw_mqtt)
        plan = build_injection_plan(func, mqtt_params=set(declared_mqtt))
        reg = _build_command_reg(
            effective_name,
            func,
            plan,
            init,
            init_plan,
            declared_mqtt,
            **self._command_reg_kwargs(
                is_root=is_root,
                name_spec=name_spec,
                tags=tags,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
                enabled=enabled,
                unavailable_on=unavailable_on,
                timeout=timeout,
                maxsize=maxsize,
                backpressure=backpressure,
                sub=sub,
                sub_key=sub_key,
            ),
        )
        self._commands.append(reg)
        return func

    def _register_deferred_command(
        self,
        func: Callable[..., Any],
        name: str | NameSpec | None,
        enabled: EnabledSpec,
        init: Callable[..., Any] | None,
        sub: str | None,
        sub_key: str,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        tags: list[str] | None,
        unavailable_on: tuple[type[Exception], ...] | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
    ) -> None:
        """Append a deferred-enabled command registration for *func*."""
        init_plan = build_injection_plan(init) if init is not None else None
        raw_mqtt = detect_raw_mqtt_params(func)
        plan = build_injection_plan(func, mqtt_params=raw_mqtt)
        effective_name, name_spec, is_root = self._resolve_command_registration_name(
            func, name
        )
        self._commands.append(
            _build_command_reg(
                effective_name,
                func,
                plan,
                init,
                init_plan,
                raw_mqtt,
                **self._command_reg_kwargs(
                    is_root=is_root,
                    name_spec=name_spec,
                    tags=tags,
                    summary=summary,
                    state_model=state_model,
                    payload_model=payload_model,
                    behavior=behavior,
                    effects=effects,
                    enabled=enabled,
                    unavailable_on=unavailable_on,
                    timeout=timeout,
                    maxsize=maxsize,
                    backpressure=backpressure,
                    sub=sub,
                    sub_key=sub_key,
                ),
            )
        )

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
        unavailable_on: tuple[type[Exception], ...] | None = None,
        timeout: TimeoutSpec | None | _Unset = _UNSET,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
    ) -> Callable[..., Any]:
        """Router variant of ``App.command`` with an extra ``tags=`` keyword.

        All naming, sub-dispatch, timeout, injection, and error semantics match
        ``App.command``. The only router-specific behavior is tag accumulation:
        router-constructor tags are merged with ``tags=`` on the command and any
        ``include_router(tags=...)`` values applied later.
        """
        if callable(name) and inspect.iscoroutinefunction(name):
            raise TypeError(
                "Use @router.command(), not @router.command (parentheses required)"
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(enabled):
                deferred_kwargs = {
                    "summary": summary,
                    "state_model": state_model,
                    "payload_model": payload_model,
                    "behavior": behavior,
                    "effects": effects,
                    "tags": tags,
                    "unavailable_on": unavailable_on,
                    "timeout": timeout,
                    "maxsize": maxsize,
                    "backpressure": backpressure,
                }
                self._register_deferred_command(
                    func,
                    name,
                    enabled,
                    init,
                    sub,
                    sub_key,
                    **deferred_kwargs,
                )
                return func
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
                unavailable_on,
                timeout,
                maxsize,
                backpressure,
            )

        return decorator
