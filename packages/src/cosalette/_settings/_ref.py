"""Settings reference abstraction for inspectable configuration bindings.

This module provides ``SettingRef`` — a callable that wraps access to a
specific settings field but preserves the field name for introspection,
tooling, and humans.

See Also:
    COS-ndz.1 — Add inspectable settings references.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, override


class SettingRef:
    """A callable reference to a specific settings field.

    This wrapper provides the same behavior as ``lambda s: s.field_name``
    but preserves the field path for introspection. The callable behavior
    is fully compatible with existing ``IntervalSpec`` and ``EnabledSpec``
    usage.

    Args:
        field_name: Dot-separated path to the settings field (e.g., "mqtt.host").

    Example:
        >>> ref = SettingRef("mqtt.reconnect_interval")
        >>> ref.field_name
        'mqtt.reconnect_interval'
        >>> # Callable behavior (same as lambda s: s.mqtt.reconnect_interval)
        >>> from cosalette.testing import make_settings
        >>> settings = make_settings()
        >>> ref(settings)  # doctest: +ELLIPSIS
        5.0
    """

    __slots__ = ("field_name", "_accessor")

    def __init__(self, field_name: str) -> None:
        """Initialize with a dot-separated field path."""
        self.field_name = field_name
        # Pre-compile the field access for efficiency
        self._accessor = _compile_field_accessor(field_name)

    def __call__(self, settings: Any) -> Any:
        """Resolve the field value from the given settings object."""
        return self._accessor(settings)

    @override
    def __repr__(self) -> str:
        """Return a human-readable representation."""
        return f"SettingRef({self.field_name!r})"


def setting_ref(field_name: str) -> SettingRef:
    """Create a callable reference to a specific settings field.

    This helper function creates a ``SettingRef`` that can be used anywhere
    a callable is expected (like ``IntervalSpec`` or ``EnabledSpec``) while
    preserving the field name for introspection and tooling.

    Args:
        field_name: Dot-separated path to the settings field.

    Returns:
        A callable that resolves the field value, with a ``field_name`` attribute.

    Example:
        >>> ref = setting_ref("poll_interval")
        >>> ref.field_name
        'poll_interval'
        >>> # Use in telemetry registration:
        >>> # @app.telemetry("temp", interval=setting_ref("sensors.poll_interval"))
    """
    return SettingRef(field_name)


def _compile_field_accessor(field_name: str) -> Callable[[Any], Any]:
    """Compile a field path into an efficient accessor function.

    Args:
        field_name: Dot-separated field path (e.g., "mqtt.host").

    Returns:
        A function that takes a settings object and returns the field value.

    Raises:
        ValueError: If the field_name is empty or invalid.
    """
    if not field_name.strip():
        msg = "field_name cannot be empty"
        raise ValueError(msg)

    # Split on dots and validate parts
    parts = field_name.split(".")
    for part in parts:
        if not part.strip():
            msg = f"Invalid field_name: {field_name!r} (empty segment)"
            raise ValueError(msg)

    def accessor(settings: Any) -> Any:
        """Access the nested field value."""
        current = settings
        for part in parts:
            try:
                current = getattr(current, part)
            except AttributeError as e:
                msg = f"Field {field_name!r} not found in settings"
                raise AttributeError(msg) from e
        return current

    return accessor
