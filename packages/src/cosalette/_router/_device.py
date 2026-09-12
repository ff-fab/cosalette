"""Device mixin for the Router class."""

from __future__ import annotations

import inspect
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from cosalette._app._device import _build_device_reg
from cosalette._app._device import _resolve_name_spec as _resolve_device_name_spec
from cosalette._app._device_validators import validate_device_triggerable
from cosalette._injection import build_injection_plan
from cosalette._registration import (
    _UNSET,
    DiscoverableSpec,
    EnabledSpec,
    NameSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    _Unset,
    _validate_init,
    check_device_name,
)
from cosalette._runners._stream_types import BackpressurePolicy
from cosalette._runners._trigger import TriggerableSpec, TriggerSource


class _RouterDeviceMixin:
    """Mixin for device-related Router methods."""

    _devices: list[_DeviceRegistration]
    _telemetry: list[_TelemetryRegistration]
    _commands: list[_CommandRegistration]
    _streams: list[_StreamRegistration]

    @abstractmethod
    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]: ...

    def _resolve_device_registration_name(
        self,
        func: Callable[..., Any],
        name: str | NameSpec | None,
    ) -> tuple[str, NameSpec | None, bool]:
        """Resolve effective name, name spec, and root flag from *name* / *func*."""
        effective_name, name_spec = _resolve_device_name_spec(name, func)
        return effective_name, name_spec, name is None

    def _validate_device_name_collision(
        self,
        name: str | NameSpec | None,
        effective_name: str,
        is_root: bool,
    ) -> None:
        """Check for name collisions when *name* is not callable."""
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

    def _device_reg_kwargs(
        self,
        *,
        is_root: bool,
        name_spec: NameSpec | None,
        trigger_source: TriggerSource | None,
        min_interval: float | None,
        unavailable_on: tuple[type[Exception], ...] | None | _Unset,
        enabled: EnabledSpec,
        tags: list[str] | None,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        discoverable: DiscoverableSpec,
        maxsize: int,
        backpressure: BackpressurePolicy,
    ) -> dict[str, Any]:
        """Return shared registration kwargs for router device records.

        Mirrors :meth:`_RouterCommandMixin._command_reg_kwargs` so the two
        router device paths (decorator and deferred-enabled) declare every
        registration field exactly once.
        """
        return {
            "is_root": is_root,
            "name_spec": name_spec,
            "triggerable": trigger_source,
            "min_interval": min_interval,
            "unavailable_on": unavailable_on,
            "enabled_spec": enabled,
            "tags": tuple(self._merge_tags(tags)),
            "summary": summary,
            "state_model": state_model,
            "payload_model": payload_model,
            "behavior": behavior,
            "effects": effects,
            "discoverable": discoverable,
            "maxsize": maxsize,
            "backpressure": backpressure,
        }

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
        discoverable: DiscoverableSpec,
        tags: list[str] | None,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        triggerable: TriggerableSpec = False,
        min_interval: float | None = None,
        unavailable_on: tuple[type[Exception], ...] | None | _Unset = _UNSET,
    ) -> Callable[..., Any]:
        """Build device registration and return func unchanged."""
        effective_name, name_spec, is_root = self._resolve_device_registration_name(
            func, name
        )
        self._validate_device_name_collision(name, effective_name, is_root)
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        trigger_source = validate_device_triggerable(
            triggerable, effective_name, plan, min_interval
        )
        reg = _build_device_reg(
            effective_name,
            func,
            plan,
            init,
            init_plan,
            **self._device_reg_kwargs(
                is_root=is_root,
                name_spec=name_spec,
                trigger_source=trigger_source,
                min_interval=min_interval,
                unavailable_on=unavailable_on,
                enabled=enabled,
                tags=tags,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
                discoverable=discoverable,
                maxsize=maxsize,
                backpressure=backpressure,
            ),
        )
        self._devices.append(reg)
        return func

    def _register_deferred_device(
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
        discoverable: DiscoverableSpec,
        tags: list[str] | None,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        triggerable: TriggerableSpec = False,
        min_interval: float | None = None,
        unavailable_on: tuple[type[Exception], ...] | None | _Unset = _UNSET,
    ) -> None:
        """Append a deferred-enabled device registration for *func*."""
        effective_name, name_spec, is_root = self._resolve_device_registration_name(
            func, name
        )
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        trigger_source = validate_device_triggerable(
            triggerable, effective_name, plan, min_interval
        )
        self._devices.append(
            _build_device_reg(
                effective_name,
                func,
                plan,
                init,
                init_plan,
                **self._device_reg_kwargs(
                    is_root=is_root,
                    name_spec=name_spec,
                    trigger_source=trigger_source,
                    min_interval=min_interval,
                    unavailable_on=unavailable_on,
                    enabled=enabled,
                    tags=tags,
                    summary=summary,
                    state_model=state_model,
                    payload_model=payload_model,
                    behavior=behavior,
                    effects=effects,
                    discoverable=discoverable,
                    maxsize=maxsize,
                    backpressure=backpressure,
                ),
            )
        )

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
        discoverable: DiscoverableSpec = True,
        tags: list[str] | None = None,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        triggerable: TriggerableSpec = False,
        min_interval: float | None = None,
        unavailable_on: tuple[type[Exception], ...] | None | _Unset = _UNSET,
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
            discoverable: Consumer-visibility control (ADR-073). ``True``
                (default) keeps every channel discoverable; ``False`` excludes
                them all. A device with ``payload_model`` emits paired
                ``/state`` and ``/set`` channels; ``"state"`` keeps only the
                state channel and ``"command"`` only the command channel. See
                ``App.device``.
            tags: Additional tags for this device.
            maxsize: Maximum command queue size. ``0`` (default) means unbounded.
                When ``> 0``, applies *backpressure* policy on queue full.
            backpressure: Policy applied when ``maxsize > 0`` and the queue is full.
                ``"drop_newest"`` (default) discards the incoming command,
                ``"drop_oldest"`` evicts the oldest queued command, ``"raise"``
                propagates :exc:`asyncio.QueueFull`. Ignored when ``maxsize=0``.
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
            unavailable_on: Exception types whose occurrence, once retries
                are exhausted, publishes retained ``"offline"`` to the
                entity's availability topic; ``"online"`` is republished on
                the next successful run (ADR-077).  Omitted, a **named**
                entity triggers on *any* exception — the framework cannot
                name downstream transport types such as ``BleakError``, so a
                narrower default would silently never fire.  Pass a tuple to
                narrow it (so a handler bug does not claim the device is
                unreachable), or ``None`` to disable.  **Root** entities are
                excluded from the automatic default and must pass a tuple to
                participate: they publish to the flat ``{prefix}/availability``
                and would otherwise mark the whole app unavailable.

        Returns:
            The decorated function, unchanged.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            ValueError: If *triggerable* is not ``"local"`` or falsy, if it
                disagrees with the presence of a
                :class:`~cosalette.DeviceTrigger` handler parameter, or if
                *min_interval* is invalid.
            TypeError: If *init* is async or if *init* / handler parameters
                lack a type annotation.
        """
        if callable(name) and inspect.iscoroutinefunction(name):
            raise TypeError(
                "Use @router.device(), not @router.device (parentheses required)"
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(enabled):
                self._register_deferred_device(
                    func,
                    name,
                    init,
                    enabled,
                    summary,
                    state_model,
                    payload_model,
                    behavior,
                    effects,
                    discoverable,
                    tags,
                    maxsize,
                    backpressure,
                    triggerable,
                    min_interval,
                    unavailable_on,
                )
                return func
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
                discoverable,
                tags,
                maxsize,
                backpressure,
                triggerable,
                min_interval,
                unavailable_on,
            )

        return decorator
