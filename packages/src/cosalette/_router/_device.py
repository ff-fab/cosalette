"""Device mixin for the Router class."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from cosalette._app._device import _build_device_reg
from cosalette._app._device import _resolve_name_spec as _resolve_device_name_spec
from cosalette._app._device_validators import validate_device_triggerable
from cosalette._injection import build_injection_plan
from cosalette._registration import (
    EnabledSpec,
    NameSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    _validate_init,
    check_device_name,
)
from cosalette._runners._stream_types import BackpressurePolicy
from cosalette._runners._trigger import TriggerableSpec
from cosalette._utils import _callable_qualname


class _RouterDeviceMixin:
    """Mixin for device-related Router methods."""

    _devices: list[_DeviceRegistration]
    _telemetry: list[_TelemetryRegistration]
    _commands: list[_CommandRegistration]
    _streams: list[_StreamRegistration]

    @abstractmethod
    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]: ...

    def _build_device_decorator_body(
        self,
        func: Callable[..., Any],
        name: str | NameSpec | None,
        init: Callable[..., Any] | None,
        enabled: EnabledSpec,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        tags: list[str] | None,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        triggerable: TriggerableSpec = False,
        min_interval: float | None = None,
    ) -> Callable[..., Any]:
        """Build device registration and return func unchanged."""
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
                streams=self._streams,
            )
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        trigger_source = validate_device_triggerable(
            triggerable, effective_name, plan, min_interval
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
            triggerable=trigger_source,
            min_interval=min_interval,
            enabled_spec=enabled,
            tags=tuple(merged_tags),
            summary=summary,
            state_model=state_model,
            payload_model=payload_model,
            behavior=behavior,
            effects=effects,
            maxsize=maxsize,
            backpressure=backpressure,
        )
        self._devices.append(reg)
        return func

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
        tags: list[str] | None = None,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        triggerable: TriggerableSpec = False,
        min_interval: float | None = None,
    ) -> Callable[..., Any]:
        """Register a command & control device.

        Extends ``App.device`` with the router-specific ``tags``
        parameter.

        Args:
            name: Device name for MQTT topics and logging.
            init: Optional synchronous factory called once before the handler.
            enabled: When ``False``, registration is skipped.
            summary: One-line description for documentation.
            state_model: Model class describing the device state payload.
                Runtime load-bearing since 0.6.0 — validates every
                ``ctx.publish_state()`` payload (see ``App.device``).
            payload_model: Model class describing the inbound command payload.
                Metadata only for validation, but since 0.6.0 it emits a
                ``receive`` channel on ``{prefix}/{device}/set`` in the AsyncAPI
                schema output, documenting the subscribed command surface.
            behavior: Phrases describing what the device does.
            effects: Side effects produced by the device.
            tags: Additional tags for this device.
            triggerable: When ``"local"``, the device joins the in-process
                trigger mechanism and the handler must declare a
                :class:`~cosalette.DeviceTrigger` parameter.  Devices accept
                ``"local"`` only — ``{prefix}/{device}/set`` is already the
                command topic.  See ``App.device`` for full semantics.
            min_interval: Optional storm throttle (ADR-066) bounding the
                minimum spacing in seconds between wake-driven
                :meth:`~cosalette.DeviceTrigger.wait` returns.  ``None``
                (the default) is off.  Requires ``triggerable=``.  See
                ``App.device`` for full semantics.

        Returns:
            The decorated function, unchanged.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If *triggerable* is not ``"local"`` or falsy, if it
                disagrees with the presence of a
                :class:`~cosalette.DeviceTrigger` handler parameter, or if
                *min_interval* is invalid.
        """
        if callable(enabled):
            return lambda func: self._build_device_decorator_body(
                func,
                name,
                init,
                enabled,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                tags,
                maxsize,
                backpressure,
                triggerable,
                min_interval,
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func
            return self._build_device_decorator_body(
                func,
                name,
                init,
                enabled,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                tags,
                maxsize,
                backpressure,
                triggerable,
                min_interval,
            )

        return decorator
