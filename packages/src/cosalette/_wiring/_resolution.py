"""Resolution and validation: Intervals, names, enabled specs."""

from __future__ import annotations

import dataclasses
import inspect
import itertools
import logging
import math
from datetime import UTC, datetime
from typing import Any, cast

from cosalette._cron import CronSchedule
from cosalette._persistence._stores import Store
from cosalette._registration import (
    _UNSET,
    EnabledSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _InboundRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
)
from cosalette._runners._periodic import _PeriodicRegistration
from cosalette._runners._trigger import arms_via_mqtt
from cosalette._settings import Settings
from cosalette._utils import _DEFAULT_COMMAND_TIMEOUT

logger = logging.getLogger("cosalette._wiring")

_DEFAULT_TIMEOUT_FACTOR = 1.0
"""Multiplier applied to the resolved poll interval when auto-defaulting timeout.

``reg.timeout = reg.interval * _DEFAULT_TIMEOUT_FACTOR`` when timeout was omitted
(``_UNSET``) and the registration uses ``interval=`` (not ``schedule=``).

Set to 1.0 so a hung handler is always caught within one poll cycle. Users
who need headroom (e.g. a legitimately slow adapter) should pass an explicit
``timeout=`` float or disable the backstop entirely with ``timeout=None``.
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
            # ``callable()`` narrows to the top callable type, whose return is
            # ``object``; the declared IntervalSpec callable returns ``float``.
            resolved = cast("float", reg.interval(settings))
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
            # See resolve_intervals: top-callable narrowing loses the return type.
            resolved = cast("float", reg.interval(settings))
            if resolved <= 0:
                msg = (
                    f"Periodic interval for {reg.name!r} must be "
                    f"positive, got {resolved}"
                )
                raise ValueError(msg)
            periodic_list[i] = dataclasses.replace(reg, interval=resolved)
        elif reg.interval <= 0:
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
            resolved = timeout(settings)
            _validate_resolved_timeout(resolved, reg.name)
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


_STALE_BACKOFF_ALLOWANCE = 60.0
"""Minimum per-retry backoff allowance (seconds) in the derived ``stale_after``.

Equal to the default ``max_delay`` cap of the built-in backoff strategies.
Jitter-free so the derived bound is deterministic (ADR-080).
"""


def _backoff_allowance(backoff: object) -> float:
    """Return the per-retry allowance: the backoff's ``max_delay``, at least 60 s.

    A strategy without a finite numeric ``max_delay`` gets the 60 s floor.
    """
    cap = getattr(backoff, "max_delay", None)
    usable = isinstance(cap, int | float) and not isinstance(cap, bool)
    if not usable or not math.isfinite(cap):
        return _STALE_BACKOFF_ALLOWANCE
    return max(_STALE_BACKOFF_ALLOWANCE, float(cap))


_CRON_GAP_SAMPLES = 16
"""Upcoming fire times sampled to find a cron schedule's longest gap."""


def _longest_cron_gap(schedule: CronSchedule) -> float:
    """Return the longest gap in seconds among the schedule's next fire times."""
    fires = [datetime.now(UTC)]
    for _ in range(_CRON_GAP_SAMPLES):
        try:
            fires.append(schedule.next_fire_after(fires[-1]))
        except ValueError:
            break
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(fires)]
    if not gaps:
        msg = (
            "Cannot derive stale_after from a cron schedule with no future fire "
            "times; set stale_after explicitly"
        )
        raise ValueError(msg)
    return max(gaps)


def derive_stale_after(reg: _TelemetryRegistration) -> float:
    """Return the default ``stale_after`` for a resolved registration (ADR-080).

    ``2 × period + timeout × (retry + 1) + allowance × retry``: two missed
    polls, plus the worst-case duration of one cycle's attempts and its
    backoff sleeps.  *period* is the interval, or a cron schedule's longest
    gap; a disabled timeout counts as ``0``; *allowance* is the backoff's
    ``max_delay``, never below 60 s.
    """
    period = (
        _longest_cron_gap(reg.schedule)
        if reg.schedule is not None
        else cast("float", reg.interval)
    )
    timeout = cast("float | None", reg.timeout) or 0.0
    allowance = _backoff_allowance(reg.backoff)
    return 2 * period + timeout * (reg.retry + 1) + allowance * reg.retry


def resolve_stale_after(
    telemetry_list: list[_TelemetryRegistration],
    settings: Settings,
) -> None:
    """Resolve callable ``stale_after`` values and derive defaults (ADR-080).

    Must run after :func:`resolve_timeouts`, whose resolved interval and
    timeout the derived default reads.  ``_UNSET`` derives a bound for named
    entities and ``None`` for root ones, which publish to the app-wide
    availability topic (the ADR-077 root exclusion).  Mutates in place.

    Raises:
        ValueError: If a callable resolves to a non-positive value.
    """
    for i, reg in enumerate(telemetry_list):
        spec = reg.stale_after
        if callable(spec):
            resolved = spec(settings)
            _validate_resolved_timeout(resolved, reg.name, param="stale_after")
            telemetry_list[i] = dataclasses.replace(reg, stale_after=resolved)
        elif spec is _UNSET:
            derived = None if reg.is_root else derive_stale_after(reg)
            telemetry_list[i] = dataclasses.replace(reg, stale_after=derived)


def _validate_resolved_timeout(
    resolved: object, name: str, label: str = "Telemetry", param: str = "timeout"
) -> None:
    """Raise ValueError if *resolved* is not a finite positive number.

    Called after invoking a timeout callable (settings-level or per-device)
    to enforce the same rules as registration-time ``validate_timeout``.

    Raises:
        ValueError: If *resolved* is not a finite positive number.
    """
    if isinstance(resolved, bool) or not isinstance(resolved, (int, float)):
        msg = (
            f"{label} {param} for {name!r} must return a float, "
            f"got {type(resolved).__name__!r}: {resolved!r}"
        )
        raise ValueError(msg)
    if not math.isfinite(resolved) or resolved <= 0:
        msg = (
            f"{label} {param} for {name!r} must be a finite positive number, "
            f"got {resolved!r}"
        )
        raise ValueError(msg)


def resolve_timeouts_commands(
    commands_list: list[_CommandRegistration],
    settings: Settings,
) -> None:
    """Resolve command timeouts and apply the bounded default (ADR-060).

    Three-state logic per registration:

    * **callable** → call with *settings* to obtain a float; require > 0.
    * **_UNSET** → auto-default: ``_DEFAULT_COMMAND_TIMEOUT``.
    * **None / concrete float** → unchanged.

    Mutates *commands_list* in place.

    Raises:
        ValueError: If a callable timeout resolves to a non-positive value.
    """
    for i, reg in enumerate(commands_list):
        timeout = reg.timeout
        if callable(timeout):
            resolved = timeout(settings)
            _validate_resolved_timeout(resolved, reg.name, "Command")
            commands_list[i] = dataclasses.replace(reg, timeout=resolved)
        elif timeout is _UNSET:
            commands_list[i] = dataclasses.replace(
                reg, timeout=_DEFAULT_COMMAND_TIMEOUT
            )
        # None or concrete float: no change needed


def resolve_timeouts_periodic(
    periodic_list: list[_PeriodicRegistration],
    settings: Settings,
) -> None:
    """Resolve periodic timeouts and apply the interval-derived default (ADR-060).

    Mirrors :func:`resolve_timeouts` for telemetry: an omitted (``_UNSET``)
    timeout auto-defaults to ``interval × _DEFAULT_TIMEOUT_FACTOR``, so a
    hung cycle cannot outlive its own poll period. Must be called AFTER
    :func:`resolve_intervals_periodic`.

    Mutates *periodic_list* in place.

    Raises:
        ValueError: If a callable timeout resolves to a non-positive value.
    """
    for i, reg in enumerate(periodic_list):
        timeout = reg.timeout
        if callable(timeout):
            resolved = timeout(settings)
            _validate_resolved_timeout(resolved, reg.name, "Periodic")
            periodic_list[i] = dataclasses.replace(reg, timeout=resolved)
        elif timeout is _UNSET:
            interval_val = reg.interval
            if isinstance(interval_val, bool) or not isinstance(
                interval_val, (int, float)
            ):
                msg = (
                    f"interval for {reg.name!r} not resolved before timeout resolution"
                )
                raise TypeError(msg)
            periodic_list[i] = dataclasses.replace(
                reg, timeout=float(interval_val) * _DEFAULT_TIMEOUT_FACTOR
            )
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
        ValueError: If an MQTT trigger source is set on a root device.
            ``triggerable="local"`` is allowed there — a local wake needs
            no topic segment (ADR-064).
    """
    if reg.persist_policy is not None and store is None:
        msg = (
            f"persist= on telemetry {reg.name!r} requires a "
            f"store= backend on the App.  Pass "
            f"store=MemoryStore() (or another Store) to App()."
        )
        raise ValueError(msg)
    if arms_via_mqtt(reg.triggerable) and reg.is_root:
        msg = (
            f"triggerable={reg.triggerable!r} requires a named device on "
            f"telemetry {reg.name!r} (name= must be set); use "
            f"triggerable='local' on a root device"
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
    inbound_list: list[_InboundRegistration] | None = None,
) -> None:
    """Resolve callable enabled= specs across all registration lists.

    Called once during bootstrap, right after :func:`resolve_intervals`.
    Mutates all three lists in place — entries disabled by their factory
    are removed, and surviving entries have their ``enabled_spec``
    pinned to ``True``.

    For telemetry entries confirmed as enabled by a callable spec,
    deferred constraints (``persist=`` requiring a store backend, an
    MQTT trigger source on a root device) are validated here.

    Args:
        telemetry_list: In-place list of telemetry registrations.
        devices_list: In-place list of device registrations.
        commands_list: In-place list of command registrations.
        settings: Resolved settings instance passed to each callable.
        store: The resolved store backend (or ``None`` if none configured).

    Raises:
        ValueError: If a surviving telemetry entry declares
            ``persist=`` but no store backend is configured.
        ValueError: If a surviving telemetry entry puts an MQTT trigger
            source on a root device.
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
    if inbound_list is not None:
        inbound_list[:] = _resolve_list_enabled(inbound_list, settings)
