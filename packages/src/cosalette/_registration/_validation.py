"""Name-validation helpers for the cosalette App.

Helper functions for validating device names and checking for collisions
during registration.
"""

from __future__ import annotations

import logging
from typing import Literal

from cosalette._registration._model import (
    _CommandRegistration,
    _DeviceRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
)

RegistryType = Literal["device", "telemetry", "command"]

logger = logging.getLogger("cosalette._registration")

# ---------------------------------------------------------------------------
# Name-validation helpers (extracted from App)
# ---------------------------------------------------------------------------

_INVALID_MQTT_CHARS: frozenset[str] = frozenset("/+#\0")


def _find_control_chars(name: str) -> list[str]:
    """Return the ASCII control characters present in *name*, in order.

    Control characters are the C0 range (``0x00``–``0x1F``) plus DEL
    (``0x7F``).  They must never appear in a name because names flow into
    log records and filesystem paths, where CR/LF and other control bytes
    enable log forging and record garbling (CWE-117).
    """
    return [c for c in name if c <= "\x1f" or c == "\x7f"]


def validate_mqtt_name(name: str) -> None:
    """Raise if *name* contains characters invalid in MQTT topic segments.

    MQTT topic levels are separated by ``/``, and ``+`` / ``#`` are
    wildcard characters.  A NUL byte (``\\0``) is forbidden by the MQTT
    specification.  ASCII control characters (``0x00``–``0x1F`` and
    ``0x7F``) are also rejected: names are embedded in log records and
    file paths, so CR/LF and similar control bytes would allow log
    forging (CWE-117).  Names are interpolated directly into topic
    addresses, so none of these characters may appear.
    """
    invalid = [c for c in name if c in _INVALID_MQTT_CHARS]
    if invalid:
        chars = ", ".join(repr(c) for c in dict.fromkeys(invalid))
        msg = f"Name '{name}' contains invalid MQTT characters: {chars}"
        raise ValueError(msg)
    control = _find_control_chars(name)
    if control:
        # repr the whole name so the offending control bytes cannot leak
        # verbatim into the caller's error handling / logs.
        chars = ", ".join(repr(c) for c in dict.fromkeys(control))
        msg = f"Name {name!r} contains control characters: {chars}"
        raise ValueError(msg)


# Mapping: registry_type → extra pools (by key) that must not collide
_COLLIDE_EXTRA: dict[str, tuple[str, ...]] = {
    "device": ("tel", "cmd"),
    "telemetry": ("tel",),
    "command": ("cmd",),
}


def colliding_names(
    registry_type: RegistryType,
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
    streams: list[_StreamRegistration],
) -> set[str]:
    """Return names that would collide with *registry_type*.

    Rules:
    - ``'device'`` collides with ALL other registrations
    - ``'telemetry'`` collides with devices + streams + other telemetry (NOT commands)
    - ``'command'`` collides with devices + streams + other commands (NOT telemetry)

    Streams share the device topic namespace (ADR-019), so they collide with
    every registration kind regardless of registration order.
    """
    pool = {"tel": telemetry, "cmd": commands}
    names: set[str] = {r.name for r in devices} | {r.name for r in streams}
    for key in _COLLIDE_EXTRA[registry_type]:
        names |= {r.name for r in pool[key]}
    return names


def validate_name_unique(name: str, existing: set[str]) -> None:
    """Raise if *name* already appears in *existing*."""
    if name in existing:
        msg = f"Name '{name}' is already registered"
        raise ValueError(msg)


def validate_single_root(has_root: bool) -> None:
    """Raise if a root device already exists."""
    if has_root:
        msg = "Only one root device (unnamed) is allowed per app"
        raise ValueError(msg)


def warn_if_mixing(is_root: bool, *, has_root: bool, has_named: bool) -> None:
    """Log a warning when root and named devices coexist."""
    will_mix = (is_root and has_named) or (not is_root and has_root)
    if will_mix:
        logger.warning(
            "Mixing root (unnamed) and named devices may cause MQTT "
            "wildcard subscription issues — <prefix>/+/state won't "
            "match <prefix>/state"
        )


def _validate_regular_command(
    name: str,
    existing: set[str],
    commands: list[_CommandRegistration],
) -> None:
    validate_name_unique(name, existing)
    for cmd in commands:
        if cmd.name == name and cmd.sub is not None:
            msg = (
                f"Cannot mix sub-dispatch and non-sub-dispatch "
                f"handlers on topic '{name}'"
            )
            raise ValueError(msg)


def _validate_sub_dispatch_peer(
    cmd: _CommandRegistration,
    name: str,
    sub: str,
    sub_key: str,
) -> None:
    """Raise if an existing sub-dispatch command conflicts with the new one."""
    if cmd.sub is None:
        msg = f"Cannot mix sub-dispatch and non-sub-dispatch handlers on topic '{name}'"
        raise ValueError(msg)
    if cmd.sub_key != sub_key:
        msg = f"All sub-dispatch handlers on topic '{name}' must use the same sub_key"
        raise ValueError(msg)
    if cmd.sub == sub:
        msg = f"Sub-command '{sub}' already registered on topic '{name}'"
        raise ValueError(msg)


def _validate_sub_dispatch_command(
    name: str,
    sub: str,
    sub_key: str,
    devices: list[_DeviceRegistration],
    commands: list[_CommandRegistration],
) -> None:
    validate_name_unique(name, {r.name for r in devices})
    for cmd in commands:
        if cmd.name != name:
            continue
        _validate_sub_dispatch_peer(cmd, name, sub, sub_key)


def _check_root_conflict(
    name: str,
    is_root: bool,
    registry_type: RegistryType,
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
) -> None:
    """Raise if name is shared between root and non-root registrations."""
    complement = commands if registry_type == "telemetry" else telemetry
    for peer in complement:
        if peer.name == name and peer.is_root != is_root:
            msg = (
                f"Cannot share name '{name}' between root and named "
                f"registrations — MQTT topic namespaces would conflict"
            )
            raise ValueError(msg)


def _check_root_and_mixing(
    is_root: bool,
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
) -> None:
    all_regs: list[_DeviceRegistration | _TelemetryRegistration | _CommandRegistration]
    all_regs = [*devices, *telemetry, *commands]
    all_names: set[str] = set()
    has_root = False
    for reg in all_regs:
        all_names.add(reg.name)
        if reg.is_root:
            has_root = True
    if is_root:
        validate_single_root(has_root)
    warn_if_mixing(is_root, has_root=has_root, has_named=bool(all_names))


def check_device_name(
    name: str,
    *,
    registry_type: RegistryType,
    is_root: bool = False,
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
    streams: list[_StreamRegistration],
    sub: str | None = None,
    sub_key: str = "command",
) -> None:
    """Raise if name collides with an incompatible registration.

    Name sharing rules:
    - telemetry + command: ALLOWED (different MQTT suffixes)
    - All other cross-type combinations: REJECTED
    - Same-type duplicates: REJECTED
    - Sub-dispatch commands may share names if they have different sub values

    When *is_root* is True, also enforces that at most one root
    (unnamed) device exists and logs a warning when root and named
    devices are mixed.

    Root and mixing checks are always global (all registrations)
    because they concern MQTT topic layout, not name scoping.

    Args:
        sub: Sub-value this handler owns for sub-dispatch routing.
        sub_key: JSON field used for routing between sub-handlers.
    """
    validate_mqtt_name(name)
    existing = colliding_names(registry_type, devices, telemetry, commands, streams)

    if registry_type == "command":
        if sub is None:
            _validate_regular_command(name, existing, commands)
        else:
            _validate_sub_dispatch_command(name, sub, sub_key, devices, commands)
    else:
        validate_name_unique(name, existing)

    if registry_type in ("telemetry", "command"):
        _check_root_conflict(name, is_root, registry_type, telemetry, commands)

    _check_root_and_mixing(is_root, devices, telemetry, commands)
