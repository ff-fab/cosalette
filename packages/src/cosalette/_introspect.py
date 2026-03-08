"""Registry introspection for cosalette applications.

See Also:
    COS-fdq — Introspection module task.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

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
            "name": app._name,
            "version": app._version,
            "description": app._description,
        },
        "devices": [_describe_device(reg) for reg in app._devices],
        "telemetry": [_describe_telemetry(reg) for reg in app._telemetry],
        "commands": [_describe_command(reg) for reg in app._commands],
        "adapters": [
            _describe_adapter(port_type, entry)
            for port_type, entry in app._adapters.items()
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
        "func": reg.func.__qualname__,
        "has_init": reg.init is not None,
        "dependencies": _format_dependencies(reg.injection_plan),
    }


def _describe_telemetry(reg: _TelemetryRegistration) -> dict[str, Any]:
    """Describe a single telemetry registration."""
    return {
        "name": reg.name,
        "type": "telemetry",
        "func": reg.func.__qualname__,
        "interval": _describe_interval(reg.interval),
        "strategy": repr(reg.publish_strategy)
        if reg.publish_strategy is not None
        else None,
        "persist": repr(reg.persist_policy) if reg.persist_policy is not None else None,
        "group": reg.group,
        "has_init": reg.init is not None,
        "dependencies": _format_dependencies(reg.injection_plan),
    }


def _describe_command(reg: _CommandRegistration) -> dict[str, Any]:
    """Describe a single command registration."""
    return {
        "name": reg.name,
        "type": "command",
        "func": reg.func.__qualname__,
        "mqtt_params": sorted(reg.mqtt_params),
        "has_init": reg.init is not None,
        "dependencies": _format_dependencies(reg.injection_plan),
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
    if callable(interval):
        return "<deferred>"
    return interval


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
