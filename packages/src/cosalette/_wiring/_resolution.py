"""Resolution and validation: Intervals, names, enabled specs."""

from __future__ import annotations

import dataclasses
import inspect
import logging
from typing import TYPE_CHECKING, Any

from cosalette._cron import CronSchedule
from cosalette._injection import KNOWN_INJECTABLE_TYPES
from cosalette._periodic import _PeriodicRegistration
from cosalette._persistence._stores import Store
from cosalette._registration import (
    EnabledSpec,
    IntervalSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    validate_mqtt_name,
)
from cosalette._settings import Settings
from cosalette._utils import _callable_qualname

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("cosalette._wiring")


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


# ---------------------------------------------------------------------------
# Name-spec expansion
# ---------------------------------------------------------------------------


def _validate_config_type(config: Any) -> None:
    """Reject per-device config whose type shadows a framework injectable."""
    if config is None:
        return
    config_type = type(config)
    if config_type in KNOWN_INJECTABLE_TYPES:
        msg = (
            f"Dict-name config type {config_type.__name__!r} shadows "
            f"a framework-provided type"
        )
        raise TypeError(msg)


def _evaluate_name_spec(
    name_spec: Callable[..., Any],
    settings: Settings,
    qualname: str,
) -> list[tuple[str, Any]]:
    """Evaluate a name-spec callable, returning (name, config|None) pairs."""
    result = name_spec(settings)
    if isinstance(result, dict):
        if not result:
            logger.warning("Dict-name callable returned empty dict for %s", qualname)
        for config in result.values():
            _validate_config_type(config)
        pairs = list(result.items())
    elif isinstance(result, list):
        if not result:
            logger.warning("List-name callable returned empty list for %s", qualname)
        pairs = [(name, None) for name in result]
    else:
        msg = f"name= callable must return dict or list, got {type(result).__name__}"
        raise TypeError(msg)

    for name, _ in pairs:
        if not isinstance(name, str):
            msg = f"name= callable must return str keys, got {type(name).__name__!r}"
            raise TypeError(msg)
        validate_mqtt_name(name)
    return pairs  # ty: ignore[invalid-return-type]


def _resolve_per_device_interval(
    reg: _TelemetryRegistration,
    dev_name: str,
    config: Any,
) -> IntervalSpec:
    """Resolve a callable interval for a single dict-name entry."""
    interval = reg.interval
    if not callable(interval) or config is None:
        return interval
    if reg.group is not None:
        msg = f"Per-device interval (callable) cannot be used with group={reg.group!r}"
        raise ValueError(msg)
    interval = interval(config)  # ty: ignore[call-top-callable]
    if interval <= 0:
        msg = f"Per-device interval for {dev_name!r} must be positive, got {interval}"
        raise ValueError(msg)
    return interval


def _resolve_per_device_schedule(
    reg: _TelemetryRegistration,
    dev_name: str,
    config: Any,
) -> CronSchedule | None:
    """Resolve a callable schedule spec for a single dict-name entry.

    When ``reg.schedule_spec`` is ``None``, returns ``reg.schedule`` unchanged.
    When set, calls the spec with *config* and parses the result.

    Raises:
        ValueError: If *config* is ``None`` (schedule= callable requires a
            per-device config object, meaning ``name=`` must also be callable).
        TypeError: If the spec callable returns an unexpected type.
    """
    if reg.schedule_spec is None:
        return reg.schedule
    if config is None:
        msg = (
            f"Per-device schedule (callable) requires a config object "
            f"(device={dev_name!r}).  Use name=callable to supply per-device config."
        )
        raise ValueError(msg)
    result = reg.schedule_spec(config)
    if isinstance(result, str):
        return CronSchedule(result)
    if isinstance(result, CronSchedule):
        return result
    msg = (
        f"schedule= callable for {dev_name!r} must return str or CronSchedule, "
        f"got {type(result).__name__!r}"
    )
    raise TypeError(msg)


def _expand_telemetry_names(
    telemetry: list[_TelemetryRegistration],
    settings: Settings,
) -> None:
    """Expand callable name specs in telemetry registrations."""
    expanded: list[_TelemetryRegistration] = []
    for reg in telemetry:
        if reg.name_spec is None:
            expanded.append(reg)
            continue
        for dev_name, config in _evaluate_name_spec(
            reg.name_spec,
            settings,
            _callable_qualname(reg.func),
        ):
            interval = _resolve_per_device_interval(reg, dev_name, config)
            schedule = _resolve_per_device_schedule(reg, dev_name, config)
            new_reg = dataclasses.replace(
                reg,
                name=dev_name,
                interval=interval,
                schedule=schedule,
                per_device_config=config,
                name_spec=None,
                schedule_spec=None,
            )
            if new_reg.triggerable and new_reg.group is not None:
                qualname = _callable_qualname(reg.func)
                msg = (
                    f"triggerable= and group= cannot be combined"
                    f" for device '{dev_name}'"
                    f" (handler: {qualname};"
                    f" coalescing groups use a shared scheduler)"
                )
                raise ValueError(msg)
            expanded.append(new_reg)
    telemetry.clear()
    telemetry.extend(expanded)


def _expand_device_names(
    devices: list[_DeviceRegistration],
    settings: Settings,
) -> None:
    """Expand callable name specs in device registrations."""
    expanded: list[_DeviceRegistration] = []
    for reg in devices:
        if reg.name_spec is None:
            expanded.append(reg)
            continue
        for dev_name, config in _evaluate_name_spec(
            reg.name_spec,
            settings,
            _callable_qualname(reg.func),
        ):
            expanded.append(
                dataclasses.replace(
                    reg,
                    name=dev_name,
                    per_device_config=config,
                    name_spec=None,
                )
            )
    devices.clear()
    devices.extend(expanded)


def _expand_command_names(
    commands: list[_CommandRegistration],
    settings: Settings,
) -> None:
    """Expand callable name specs in command registrations."""
    expanded: list[_CommandRegistration] = []
    for reg in commands:
        if reg.name_spec is None:
            expanded.append(reg)
            continue
        for dev_name, config in _evaluate_name_spec(
            reg.name_spec,
            settings,
            _callable_qualname(reg.func),
        ):
            expanded.append(
                dataclasses.replace(
                    reg,
                    name=dev_name,
                    per_device_config=config,
                    name_spec=None,
                )
            )
    commands.clear()
    commands.extend(expanded)


def _check_is_root_consistency(
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
) -> None:
    """Shared tel↔cmd names must agree on is_root (MQTT namespace check)."""
    for tel_reg in telemetry:
        for cmd_reg in commands:
            if tel_reg.name == cmd_reg.name and tel_reg.is_root != cmd_reg.is_root:
                msg = (
                    f"Cannot share name '{tel_reg.name}' between root and named "
                    f"registrations — MQTT topic namespaces would conflict"
                )
                raise ValueError(msg)


def _check_sub_dispatch_entry(
    name: str,
    cmd_reg: _CommandRegistration,
    cmd_set: set[str],
    cmd_sub_groups: dict[str, tuple[str, set[str]]],
) -> None:
    if name in cmd_set:
        msg = f"Cannot mix sub-dispatch and non-sub-dispatch handlers on topic '{name}'"
        raise ValueError(msg)
    if name not in cmd_sub_groups:
        cmd_sub_groups[name] = (cmd_reg.sub_key, set())
    existing_sub_key, existing_subs = cmd_sub_groups[name]
    if existing_sub_key != cmd_reg.sub_key:
        msg = f"All sub-dispatch handlers on topic '{name}' must use the same sub_key"
        raise ValueError(msg)
    sub_value = cmd_reg.sub
    assert sub_value is not None
    if sub_value in existing_subs:
        msg = f"Sub-command '{sub_value}' already registered on topic '{name}'"
        raise ValueError(msg)
    existing_subs.add(sub_value)


def _check_regular_command_entry(
    name: str,
    cmd_set: set[str],
    cmd_sub_groups: dict[str, tuple[str, set[str]]],
) -> None:
    if name in cmd_sub_groups:
        msg = f"Cannot mix sub-dispatch and non-sub-dispatch handlers on topic '{name}'"
        raise ValueError(msg)
    if name in cmd_set:
        msg = f"Device name '{name}' is already registered"
        raise ValueError(msg)
    cmd_set.add(name)


def _check_command_registrations(
    commands: list[_CommandRegistration],
    device_set: set[str],
) -> None:
    cmd_set: set[str] = set()
    cmd_sub_groups: dict[str, tuple[str, set[str]]] = {}
    for cmd_reg in commands:
        name = cmd_reg.name
        if name in device_set:
            msg = f"Device name '{name}' is already registered"
            raise ValueError(msg)
        if cmd_reg.sub is not None:
            _check_sub_dispatch_entry(name, cmd_reg, cmd_set, cmd_sub_groups)
        else:
            _check_regular_command_entry(name, cmd_set, cmd_sub_groups)


def _check_expanded_duplicates(
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
) -> None:
    """Check for name collisions after dict/list expansion."""
    device_set: set[str] = set()
    for reg in devices:
        if reg.name in device_set:
            msg = f"Device name '{reg.name}' is already registered"
            raise ValueError(msg)
        device_set.add(reg.name)

    telem_set: set[str] = set()
    for tel_reg in telemetry:
        name = tel_reg.name
        if name in device_set or name in telem_set:
            msg = f"Device name '{name}' is already registered"
            raise ValueError(msg)
        telem_set.add(name)

    _check_command_registrations(commands, device_set)
    _check_is_root_consistency(telemetry, commands)


def expand_name_specs(
    telemetry: list[_TelemetryRegistration],
    devices: list[_DeviceRegistration],
    commands: list[_CommandRegistration],
    settings: Settings,
) -> None:
    """Expand callable name= specs into concrete registrations.

    .. note::

       Duplicate-name checking is **not** performed here.  It is
       deferred to after :func:`resolve_enabled` so that registrations
       disabled by a callable ``enabled=`` spec are pruned before the
       check runs, preventing false conflicts.
    """
    _expand_telemetry_names(telemetry, settings)
    _expand_device_names(devices, settings)
    _expand_command_names(commands, settings)
