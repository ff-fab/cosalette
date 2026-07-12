"""Name-spec expansion and registration validation helpers."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from cosalette._cron import CronSchedule
from cosalette._injection import KNOWN_INJECTABLE_TYPES
from cosalette._registration import (
    IntervalSpec,
    TimeoutSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _TelemetryRegistration,
    _Unset,
    validate_mqtt_name,
)
from cosalette._settings import Settings
from cosalette._utils import _callable_qualname

if TYPE_CHECKING:
    from collections.abc import Callable


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
    import logging

    logger = logging.getLogger("cosalette._wiring")
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


def _resolve_per_device_timeout(
    reg: _TelemetryRegistration,
    dev_name: str,
    config: Any,
) -> TimeoutSpec | None | _Unset:
    """Resolve a callable timeout for a single dict-name entry.

    When ``reg.timeout`` is not callable or ``config`` is ``None``, the
    value is returned unchanged (leaving ``_UNSET`` or ``None`` to be
    resolved later by :func:`~cosalette._wiring._resolution.resolve_timeouts`).

    Raises:
        ValueError: If the resolved timeout is non-positive.
    """
    timeout = reg.timeout
    if not callable(timeout) or config is None:
        return timeout
    resolved = timeout(config)  # ty: ignore[call-top-callable]
    if resolved <= 0:
        msg = f"Per-device timeout for {dev_name!r} must be positive, got {resolved}"
        raise ValueError(msg)
    return resolved


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
            timeout = _resolve_per_device_timeout(reg, dev_name, config)
            new_reg = dataclasses.replace(
                reg,
                name=dev_name,
                interval=interval,
                schedule=schedule,
                timeout=timeout,
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
    assert sub_value is not None  # noqa: S101
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
