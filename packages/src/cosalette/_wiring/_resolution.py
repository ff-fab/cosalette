"""Resolution and validation: Intervals, names, enabled specs."""

from __future__ import annotations

import dataclasses
import inspect
import logging
from typing import Any

from cosalette._persistence._stores import Store
from cosalette._registration import (
    _UNSET,
    EnabledSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._periodic import _PeriodicRegistration
from cosalette._settings import Settings

logger = logging.getLogger("cosalette._wiring")

_DEFAULT_TIMEOUT_FACTOR = 1.0
"""Multiplier applied to the resolved poll interval when auto-defaulting timeout.

``reg.timeout = reg.interval * _DEFAULT_TIMEOUT_FACTOR`` when timeout was omitted
(``_UNSET``) and the registration uses ``interval=`` (not ``schedule=``).
"""


def resolve_intervals(
    telemetry_list: list[_TelemetryRegistration],
    settings: Settings,
) -> None:
    """Resolve any callable intervals to concrete floats.

    Called once after settings are resolved.  Replaces
    ``_TelemetryRegistration`` entries that have callable intervals
    with new frozen instances containing the resolved float value.
    Mutates *telemetry_list* in place.

    Raises:
        ValueError: If a resolved interval is zero or negative.
    """
    for i, reg in enumerate(telemetry_list):
        if callable(reg.interval):
            resolved = reg.interval(settings)  # ty: ignore[call-top-callable]
            if resolved <= 0:
                msg = (
                    f"Telemetry interval for {reg.name!r} must be "
                    f"positive, got {resolved}"
                )
                raise ValueError(msg)
            telemetry_list[i] = dataclasses.replace(reg, interval=resolved)


def resolve_intervals_periodic(
    periodic_list: list[_PeriodicRegistration],
    settings: Settings,
) -> None:
    """Resolve any callable intervals in periodic registrations to concrete floats.

    Called once after settings are resolved, alongside
    :func:`resolve_intervals`.  Mutates *periodic_list* in place.

    Raises:
        ValueError: If a resolved interval is zero or negative.
    """
    for i, reg in enumerate(periodic_list):
        if callable(reg.interval):
            resolved = reg.interval(settings)  # ty: ignore[call-top-callable]
            if resolved <= 0:
                msg = (
                    f"Periodic interval for {reg.name!r} must be "
                    f"positive, got {resolved}"
                )
                raise ValueError(msg)
            periodic_list[i] = dataclasses.replace(reg, interval=resolved)
        elif isinstance(reg.interval, (int, float)) and reg.interval <= 0:
            msg = (
                f"Periodic interval for {reg.name!r} must be "
                f"positive, got {reg.interval}"
            )
            raise ValueError(msg)


def resolve_timeouts(
    telemetry_list: list[_TelemetryRegistration],
    settings: Settings,
) -> None:
    """Resolve callable timeouts and apply auto-defaults for UNSET entries.

    Must be called AFTER :func:`resolve_intervals` so that ``reg.interval``
    is already a concrete float when the auto-default is computed.

    Three-state logic per registration:

    * **callable** → call with *settings* to obtain a float; require > 0.
    * **_UNSET** → auto-default: ``interval × _DEFAULT_TIMEOUT_FACTOR`` for
      interval-based telemetry, ``None`` for cron-scheduled telemetry.
    * **None / concrete float** → unchanged.

    Mutates *telemetry_list* in place.

    Raises:
        ValueError: If a callable timeout resolves to a non-positive value.
    """
    for i, reg in enumerate(telemetry_list):
        timeout = reg.timeout
        if callable(timeout):
            resolved = timeout(settings)  # ty: ignore[call-top-callable]
            if resolved <= 0:
                msg = (
                    f"Telemetry timeout for {reg.name!r} must be "
                    f"positive, got {resolved}"
                )
                raise ValueError(msg)
            telemetry_list[i] = dataclasses.replace(reg, timeout=resolved)
        elif timeout is _UNSET:
            interval_val = reg.interval
            if callable(interval_val):  # resolve_intervals must have run first
                msg = (
                    f"interval for {reg.name!r} not resolved before timeout resolution"
                )
                raise TypeError(msg)
            new_timeout: float | None = (
                interval_val * _DEFAULT_TIMEOUT_FACTOR if reg.schedule is None else None
            )
            telemetry_list[i] = dataclasses.replace(reg, timeout=new_timeout)
        # None or concrete float: no change needed


def _reject_async_enabled(spec: Any) -> None:
    """Raise TypeError if *spec* is an async callable."""
    if inspect.iscoroutinefunction(spec):
        msg = (
            f"enabled= callable {spec!r} is async; "
            f"enabled= callables must be synchronous"
        )
        raise TypeError(msg)


def _enabled_arg(reg: Any, settings: Settings) -> Any:
    """Return the argument to pass to an enabled= callable.

    For dict-name registrations the callable receives the per-device
    config object; for everything else it receives the global settings.
    """
    return (
        reg.per_device_config
        if getattr(reg, "per_device_config", None) is not None
        else settings
    )


def _validate_enabled_telemetry(
    reg: _TelemetryRegistration,
    store: Store | None,
) -> None:
    """Validate deferred telemetry constraints after enabled= resolves truthy.

    Raises:
        ValueError: If ``persist=`` is set but no store backend is configured.
        ValueError: If ``triggerable=True`` is combined with a coalescing group.
        ValueError: If ``triggerable=True`` is set on a root device.
    """
    if reg.persist_policy is not None and store is None:
        msg = (
            f"persist= on telemetry {reg.name!r} requires a "
            f"store= backend on the App.  Pass "
            f"store=MemoryStore() (or another Store) to App()."
        )
        raise ValueError(msg)
    if reg.triggerable and reg.group is not None:
        msg = f"triggerable= and group= cannot be combined on telemetry {reg.name!r}"
        raise ValueError(msg)
    if reg.triggerable and reg.is_root:
        msg = (
            f"triggerable=True requires a named device on "
            f"telemetry {reg.name!r} (name= must be set)"
        )
        raise ValueError(msg)


def _resolve_list_enabled(
    registrations: list[Any],
    settings: Settings,
) -> list[Any]:
    """Return a filtered list with callable enabled_specs resolved.

    Entries whose resolved enabled spec is falsy are dropped.
    Surviving entries with a callable spec are replaced with
    ``enabled_spec=True``.  Entries with a literal spec are passed
    through unchanged.
    """
    result = []
    for reg in registrations:
        spec: EnabledSpec = reg.enabled_spec
        if callable(spec):
            _reject_async_enabled(spec)
            if spec(_enabled_arg(reg, settings)):
                result.append(dataclasses.replace(reg, enabled_spec=True))
            # else: drop — device is disabled by the factory
        else:
            result.append(reg)
    return result


def resolve_enabled(
    telemetry_list: list[_TelemetryRegistration],
    devices_list: list[_DeviceRegistration],
    commands_list: list[_CommandRegistration],
    settings: Settings,
    store: Store | None,
    periodic_list: list[_PeriodicRegistration] | None = None,
    stream_list: list[_StreamRegistration] | None = None,
) -> None:
    """Resolve callable enabled= specs across all registration lists.

    Called once during bootstrap, right after :func:`resolve_intervals`.
    Mutates all three lists in place — entries disabled by their factory
    are removed, and surviving entries have their ``enabled_spec``
    pinned to ``True``.

    For telemetry entries confirmed as enabled by a callable spec,
    deferred constraints (``persist=`` requiring a store backend,
    ``triggerable=True`` with a group) are validated here.

    Args:
        telemetry_list: In-place list of telemetry registrations.
        devices_list: In-place list of device registrations.
        commands_list: In-place list of command registrations.
        settings: Resolved settings instance passed to each callable.
        store: The resolved store backend (or ``None`` if none configured).

    Raises:
        ValueError: If a surviving telemetry entry declares
            ``persist=`` but no store backend is configured.
        ValueError: If a surviving telemetry entry combines
            ``triggerable=True`` with a coalescing group or root device.
    """
    resolved_telemetry: list[_TelemetryRegistration] = []
    for reg in telemetry_list:
        spec = reg.enabled_spec
        if callable(spec):
            _reject_async_enabled(spec)
            if spec(_enabled_arg(reg, settings)):
                _validate_enabled_telemetry(reg, store)
                resolved_telemetry.append(dataclasses.replace(reg, enabled_spec=True))
            # else: drop — disabled by the factory
        else:
            resolved_telemetry.append(reg)

    telemetry_list[:] = resolved_telemetry
    devices_list[:] = _resolve_list_enabled(devices_list, settings)
    commands_list[:] = _resolve_list_enabled(commands_list, settings)
    if periodic_list is not None:
        periodic_list[:] = _resolve_list_enabled(periodic_list, settings)
    if stream_list is not None:
        stream_list[:] = _resolve_list_enabled(stream_list, settings)
