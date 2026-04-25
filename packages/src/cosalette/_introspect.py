"""Registry introspection for cosalette applications.

See Also:
    COS-fdq — Introspection module task.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import orjson

from cosalette._settings_ref import SettingRef
from cosalette._utils import _callable_qualname

if TYPE_CHECKING:
    from cosalette._adapter_lifecycle import _AdapterEntry
    from cosalette._app import App
    from cosalette._registration import (
        _CommandRegistration,
        _DeviceRegistration,
        _TelemetryRegistration,
    )


def build_registry_snapshot(app: App) -> dict[str, Any]:
    """Build a JSON-serializable snapshot of all app registrations.

    Produces a dict describing the app metadata, devices, telemetry,
    commands, and adapters — suitable for ``json.dumps()`` without
    custom encoders.

    Args:
        app: The cosalette :class:`App` instance to introspect.

    Returns:
        A plain dict with string keys and JSON-serializable values.
    """
    return {
        "app": {
            "name": app.name,
            "version": app.version,
            "description": app.description,
        },
        "devices": [_describe_device(reg) for reg in app.devices],
        "telemetry": [_describe_telemetry(reg) for reg in app.telemetry_registrations],
        "commands": [_describe_command(reg) for reg in app.commands],
        "adapters": [
            _describe_adapter(port_type, entry)
            for port_type, entry in app.adapters.items()
        ],
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _describe_device(reg: _DeviceRegistration) -> dict[str, Any]:
    """Describe a single device registration."""
    return {
        "name": reg.name,
        "type": "device",
        "func": _callable_qualname(reg.func),
        "enabled": _describe_enabled(reg.enabled_spec),
        "is_root": reg.is_root,
        "has_init": reg.init is not None,
        "dependencies": _format_dependencies(reg.injection_plan),
        "summary": reg.summary,
        "behavior": reg.behavior,
        "effects": reg.effects,
    }


def _describe_telemetry(reg: _TelemetryRegistration) -> dict[str, Any]:
    """Describe a single telemetry registration."""
    return {
        "name": reg.name,
        "type": "telemetry",
        "func": _callable_qualname(reg.func),
        "interval": _describe_interval(reg.interval),
        "enabled": _describe_enabled(reg.enabled_spec),
        "is_root": reg.is_root,
        "strategy": repr(reg.publish_strategy)
        if reg.publish_strategy is not None
        else None,
        "persist": repr(reg.persist_policy) if reg.persist_policy is not None else None,
        "group": reg.group,
        "has_init": reg.init is not None,
        "dependencies": _format_dependencies(reg.injection_plan),
        "retry": reg.retry,
        "retry_on": ([exc.__name__ for exc in reg.retry_on] if reg.retry_on else None),
        "backoff": (repr(reg.backoff) if reg.backoff is not None else None),
        "circuit_breaker": (
            repr(reg.circuit_breaker) if reg.circuit_breaker is not None else None
        ),
        "triggerable": reg.triggerable,
        "summary": reg.summary,
        "state_model": (
            reg.state_model.__name__ if reg.state_model is not None else None
        ),
        "payload_model": (
            reg.payload_model.__name__ if reg.payload_model is not None else None
        ),
        "behavior": reg.behavior,
        "effects": reg.effects,
    }


def _describe_command(reg: _CommandRegistration) -> dict[str, Any]:
    """Describe a single command registration."""
    return {
        "name": reg.name,
        "type": "command",
        "func": _callable_qualname(reg.func),
        "mqtt_params": sorted(reg.mqtt_params),
        "enabled": _describe_enabled(reg.enabled_spec),
        "is_root": reg.is_root,
        "has_init": reg.init is not None,
        "dependencies": _format_dependencies(reg.injection_plan),
        "summary": reg.summary,
        "state_model": (
            reg.state_model.__name__ if reg.state_model is not None else None
        ),
        "payload_model": (
            reg.payload_model.__name__ if reg.payload_model is not None else None
        ),
        "behavior": reg.behavior,
        "effects": reg.effects,
        "sub": reg.sub,
        "sub_key": reg.sub_key if reg.sub is not None else None,
    }


def _describe_adapter(port_type: type, entry: _AdapterEntry) -> dict[str, Any]:
    """Describe a single adapter entry."""
    return {
        "port": port_type.__name__,
        "impl": _describe_impl(entry.impl),
        "dry_run": _describe_impl(entry.dry_run) if entry.dry_run is not None else None,
    }


def _describe_interval(interval: float | Callable[..., float]) -> float | str:
    """Describe a telemetry interval value."""
    if isinstance(interval, SettingRef):
        return interval.field_name
    if callable(interval):
        return "<deferred>"
    return interval


def _describe_enabled(enabled: bool | Callable[..., bool]) -> bool | str:
    """Describe an enabled value."""
    if isinstance(enabled, SettingRef):
        return enabled.field_name
    if callable(enabled):
        return "<deferred>"
    return enabled


def _describe_impl(impl: type | str | Callable[..., object]) -> str:
    """Describe an adapter implementation."""
    if isinstance(impl, str):
        return impl
    if isinstance(impl, type):
        return impl.__name__
    return getattr(impl, "__qualname__", type(impl).__name__)


def _format_dependencies(plan: list[tuple[str, type]]) -> list[list[str]]:
    """Convert an injection plan to a JSON-serializable list of pairs."""
    return [[param_name, typ.__name__] for param_name, typ in plan]


# ---------------------------------------------------------------------------
# Public formatting helpers
# ---------------------------------------------------------------------------


def format_registry_json(snapshot: dict[str, Any]) -> str:
    """Return the registry *snapshot* as indented JSON.

    Args:
        snapshot: Dict returned by :func:`build_registry_snapshot`.

    Returns:
        A pretty-printed JSON string.
    """
    result: str = orjson.dumps(snapshot, option=orjson.OPT_INDENT_2).decode()
    return result


def format_registry_table(snapshot: dict[str, Any]) -> str:
    """Return the registry *snapshot* as a human-readable plain-text table.

    Args:
        snapshot: Dict returned by :func:`build_registry_snapshot`.

    Returns:
        A multi-line string with aligned columns per section.
    """
    lines: list[str] = []
    app_info = snapshot["app"]
    desc = app_info.get("description") or ""
    header = f"{app_info['name']} v{app_info['version']}"
    if desc:
        header += f" — {desc}"
    lines.append(header)

    _append_devices_section(lines, snapshot.get("devices", []))
    _append_telemetry_section(lines, snapshot.get("telemetry", []))
    _append_commands_section(lines, snapshot.get("commands", []))
    _append_adapters_section(lines, snapshot.get("adapters", []))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section renderers (one per registration type, keeps format_registry_table lean)
# ---------------------------------------------------------------------------


def _append_devices_section(lines: list[str], devices: list[dict[str, Any]]) -> None:
    if not devices:
        return
    lines.append("")
    lines.append("Devices")
    lines.append(
        _table(
            ["Name", "Enabled", "Root", "Init", "Dependencies"],
            [
                [
                    d["name"],
                    _none(d["enabled"]),
                    _bool(d["is_root"]),
                    _bool(d["has_init"]),
                    _deps(d["dependencies"]),
                ]
                for d in devices
            ],
        )
    )


def _append_telemetry_section(
    lines: list[str], telemetry: list[dict[str, Any]]
) -> None:
    if not telemetry:
        return
    lines.append("")
    lines.append("Telemetry")
    lines.append(
        _table(
            [
                "Name",
                "Interval",
                "Enabled",
                "Strategy",
                "Persist",
                "Group",
                "Root",
                "Init",
                "Dependencies",
            ],
            [
                [
                    t["name"],
                    _none(t["interval"]),
                    _none(t["enabled"]),
                    _none(t.get("strategy")),
                    _none(t.get("persist")),
                    _none(t.get("group")),
                    _bool(t["is_root"]),
                    _bool(t["has_init"]),
                    _deps(t["dependencies"]),
                ]
                for t in telemetry
            ],
        )
    )


def _append_commands_section(lines: list[str], commands: list[dict[str, Any]]) -> None:
    if not commands:
        return
    lines.append("")
    lines.append("Commands")
    lines.append(
        _table(
            ["Name", "MQTT Params", "Enabled", "Root", "Init", "Dependencies"],
            [
                [
                    c["name"],
                    ", ".join(c["mqtt_params"]) if c["mqtt_params"] else "\u2014",
                    _none(c["enabled"]),
                    _bool(c["is_root"]),
                    _bool(c["has_init"]),
                    _deps(c["dependencies"]),
                ]
                for c in commands
            ],
        )
    )


def _append_adapters_section(lines: list[str], adapters: list[dict[str, Any]]) -> None:
    if not adapters:
        return
    lines.append("")
    lines.append("Adapters")
    lines.append(
        _table(
            ["Port", "Implementation", "Dry-Run"],
            [[a["port"], a["impl"], _none(a.get("dry_run"))] for a in adapters],
        )
    )


# ---------------------------------------------------------------------------
# Table-building helpers
# ---------------------------------------------------------------------------


def _bool(value: bool) -> str:  # noqa: FBT001
    return "\u2713" if value else "\u2014"


def _none(value: object) -> str:
    return "\u2014" if value is None else str(value)


def _deps(pairs: list[list[str]]) -> str:
    if not pairs:
        return "\u2014"
    return ", ".join(f"{p}: {t}" for p, t in pairs)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Build a fixed-width aligned table string."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    parts = [fmt.format(*headers), fmt.format(*("\u2500" * w for w in widths))]
    for row in rows:
        parts.append(fmt.format(*row))
    return "\n".join(parts)
