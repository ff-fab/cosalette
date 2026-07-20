"""Device mixin for the App class."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from cosalette._injection import build_injection_plan
from cosalette._registration import (
    EnabledSpec,
    NameSpec,
    _build_op_reg,
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
    _validate_init,
    check_device_name,
)
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


def _build_device_reg(
    name: str,
    func: Callable[..., Any],
    plan: list[tuple[str, type]],
    init: Callable[..., Any] | None,
    init_plan: list[tuple[str, type]] | None,
    **kw: Any,
) -> _DeviceRegistration:
    return _build_op_reg(_DeviceRegistration, name, func, plan, init, init_plan, **kw)


class _DeviceMixin:
    """Mixin for device-related App methods."""

    _devices: list[_DeviceRegistration]
    _commands: list[_CommandRegistration]
    _telemetry: list[_TelemetryRegistration]

    def device(
        self,
        name: str | NameSpec | None = None,
        *,
        init: Callable[..., Any] | None = None,
        enabled: EnabledSpec = True,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> Callable[..., Any]:
        """Register a command & control device.

        The decorated function runs as a concurrent asyncio task.
        Parameters are injected based on type annotations — declare
        only what you need (e.g. ``ctx: DeviceContext``,
        ``settings: Settings``, ``logger: logging.Logger``).
        Zero-parameter handlers are valid.
        Device handlers must be async generators; each ``yield`` marks
        a reactor dispatch boundary.

        The framework subscribes to ``{name}/set`` and routes commands
        to the handler registered via ``ctx.on_command``.

        When *name* is ``None``, the function name is used internally
        and the device publishes to root-level topics (``{prefix}/state``
        instead of ``{prefix}/{device}/state``).

        Args:
            name: Device name for MQTT topics and logging.  When
                ``None``, the function name is used internally and
                topics omit the device segment.  When a
                :data:`NameSpec` callable is provided, the framework
                calls it with the resolved ``Settings``.  Returning
                ``list[str]`` expands the registration into one device
                per name.  Returning ``dict[str, config]`` expands the
                registration into one device per key, and each dict
                value becomes the per-device config injected into the
                handler.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                the handler by type.
            enabled: When ``False``, registration is silently skipped.
                When a callable ``(Settings) -> bool``, the decision
                is deferred to the bootstrap phase after settings
                resolution.  Defaults to ``True``.
            summary: One-line description of the device for documentation
                and manifest output.  Informational only.  Defaults to
                ``None``.
            state_model: Model class describing the device state payload
                (e.g. a dataclass or Pydantic model).  Used by
                ``cosalette schema init`` to emit a typed AsyncAPI schema.
                Informational only — no runtime validation.  Defaults to
                ``None``.
            payload_model: Model class describing the inbound command payload.
                Stored in the manifest for API symmetry; device ``/set`` channels are
                not schema-emitted, so this is introspection-only and does not affect
                ``cosalette schema init`` output.  Defaults to ``None``.
            behavior: List of phrases describing what the device does
                (e.g. ``["polls I2C bus", "publishes state on change"]``).
                Informational only.  Defaults to ``None``.
            effects: List of side effects the device produces
                (e.g. ``["publishes {name}/state"]``).  Informational
                only.  Defaults to ``None``.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            TypeError: If any handler parameter lacks a type annotation.
        """
        # Note: inspect.iscoroutinefunction is used here rather than
        # asyncio.iscoroutinefunction (deprecated in 3.12, removed in 3.16).
        # The asyncio variant additionally checked the legacy _is_coroutine
        # marker used by older frameworks (aiohttp <3.x); that marker is not
        # supported by cosalette so the narrower inspect check is correct.
        if callable(name) and inspect.iscoroutinefunction(name):
            raise TypeError("Use @app.device(), not @app.device (parentheses required)")

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(enabled):
                self._register_deferred_device(
                    func,
                    name,
                    enabled,
                    init,
                    summary=summary,
                    state_model=state_model,
                    payload_model=payload_model,
                    behavior=behavior,
                    effects=effects,
                )
                return func
            effective_name = name if name is not None else _callable_name(func)
            self.add_device(
                effective_name,
                func,
                init=init,
                enabled=enabled,
                is_root=name is None,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            )
            return func

        return decorator

    def _register_deferred_device(
        self,
        func: Callable[..., Any],
        name: str | Callable[..., Any] | None,
        enabled: EnabledSpec,
        init: Callable[..., Any] | None,
        *,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> None:
        """Append a deferred-enabled device registration for *func*."""
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        resolved_name, name_spec = _resolve_name_spec(name, func)
        self._devices.append(
            _build_device_reg(
                resolved_name,
                func,
                plan,
                init,
                init_plan,
                is_root=not callable(name) and name is None,
                name_spec=name_spec,
                enabled_spec=enabled,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            ),
        )

    def add_device(
        self,
        name: str | Callable[..., Any],
        func: Callable[..., Any],
        *,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        is_root: bool = False,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> None:
        """Register a command & control device imperatively.

        This is the imperative counterpart to :meth:`device`.  It
        always creates a *named* (non-root) registration by default.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async generator implementing the device lifecycle.
                Each ``yield`` marks a reactor dispatch boundary.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                *func* by type.
            enabled: When ``False``, registration is silently skipped
                — no entry in the registry and no name slot reserved.
                Defaults to ``True``.
            is_root: When ``True``, the device publishes to root-level
                topics (``{prefix}/state`` instead of
                ``{prefix}/{name}/state``).  Defaults to ``False``.
            summary: One-line description of the device for documentation
                and manifest output.  Informational only.  Defaults to
                ``None``.
            state_model: Model class describing the device state payload.
                Used by ``cosalette schema init`` for typed AsyncAPI schemas.
                Informational only — no runtime validation.  Defaults to
                ``None``.
            payload_model: Model class describing the inbound command payload.
                Stored in the manifest for API symmetry; device ``/set`` channels are
                not schema-emitted, so this is introspection-only and does not affect
                ``cosalette schema init`` output.  Defaults to ``None``.
            behavior: List of phrases describing what the device does.
                Informational only.  Defaults to ``None``.
            effects: List of side effects the device produces.
                Informational only.  Defaults to ``None``.

        Raises:
            ValueError: If a device with this name is already registered.
            TypeError: If *init* is async or has un-annotated parameters.
            TypeError: If *func* has un-annotated parameters.

        See Also:
            :meth:`device` — decorator equivalent.
        """
        if not enabled:
            return
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        if not callable(name):
            check_device_name(
                name,
                registry_type="device",
                is_root=is_root,
                devices=self._devices,
                telemetry=self._telemetry,
                commands=self._commands,
            )
        plan = build_injection_plan(func)
        resolved_name, name_spec = _resolve_name_spec(name, func)
        self._devices.append(
            _build_device_reg(
                resolved_name,
                func,
                plan,
                init,
                init_plan,
                is_root=is_root,
                name_spec=name_spec,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            ),
        )
