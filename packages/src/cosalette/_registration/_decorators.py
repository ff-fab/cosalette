"""Decoration-time builders and validators shared by App and Router.

Router mixins use these without importing :mod:`cosalette._app`, so a
Router-only module never loads the App façade (cos-qitr.3).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, get_args, get_origin

from cosalette._registration import (
    _UNSET,
    TimeoutSpec,
    _build_op_reg,
    _CommandRegistration,
    _DeviceRegistration,
    validate_mqtt_name,
    warn_on_state_model_conflict,
)
from cosalette._registration._telemetry_validators import (
    validate_timeout,
    validate_unavailable_on,
)
from cosalette._runners._stream_types import StreamablePort
from cosalette._utils import _callable_name, _callable_qualname


def _resolve_name_spec(
    name: str | Callable[..., Any] | None,
    func: Callable[..., Any],
) -> tuple[str, Callable[..., Any] | None]:
    """Return (resolved_name, name_spec) from a raw name argument."""
    if callable(name):
        return _callable_qualname(func), name
    return name or _callable_name(func), None


def _build_device_reg(
    name: str,
    func: Callable[..., Any],
    plan: list[tuple[str, type]],
    init: Callable[..., Any] | None,
    init_plan: list[tuple[str, type]] | None,
    **kw: Any,
) -> _DeviceRegistration:
    validate_unavailable_on(kw.get("unavailable_on", _UNSET))
    return _build_op_reg(_DeviceRegistration, name, func, plan, init, init_plan, **kw)


def _build_command_reg(
    name: str,
    func: Callable[..., Any],
    plan: list[tuple[str, type]],
    init: Callable[..., Any] | None,
    init_plan: list[tuple[str, type]] | None,
    declared_mqtt: frozenset[str],
    *,
    sub: str | None,
    sub_key: str,
    unavailable_on: tuple[type[Exception], ...] | None = None,
    **kw: Any,
) -> _CommandRegistration:
    # Single choke point for every command entry point — App.command,
    # App.add_command, Router.command, and both deferred-enabled variants.
    warn_on_state_model_conflict(func, kw.get("state_model"), name)
    return _build_op_reg(
        _CommandRegistration,
        name,
        func,
        plan,
        init,
        init_plan,
        mqtt_params=declared_mqtt,
        sub=sub,
        sub_key=sub_key,
        unavailable_on=unavailable_on,
        **kw,
    )


def validate_stream_health(
    name: str,
    *,
    stale_after: TimeoutSpec | None,
    feeds: Sequence[str],
    is_root: bool,
) -> tuple[str, ...]:
    """Validate a stream's ``stale_after=`` and ``feeds=`` at decoration time.

    Checks what is knowable before bootstrap: a concrete ``stale_after``
    must be a finite positive number, ``feeds`` must be a sequence of
    names (a bare ``str`` is rejected — it is itself a sequence), and a
    root stream may not declare ``feeds``.  Whether the fed names exist
    is checked at bootstrap, after every registration is known.

    Args:
        name: Resolved stream name, for error messages.
        stale_after: The ``stale_after=`` value.
        feeds: The ``feeds=`` value.
        is_root: Whether the stream is unnamed (root).

    Returns:
        *feeds* normalised to a tuple.

    Raises:
        ValueError: On an invalid ``stale_after`` or ``feeds`` value.
        TypeError: If *feeds* is a ``str``.
    """
    validate_timeout(stale_after, "stale_after")
    if isinstance(feeds, str):
        msg = (
            f"Stream {name!r}: feeds= must be a sequence of entity names, "
            f"not a str; use feeds=[{feeds!r}]"
        )
        raise TypeError(msg)
    fed = tuple(feeds)
    if fed and is_root:
        msg = (
            f"Root stream {name!r} cannot declare feeds=: a root stream has no "
            "availability topic of its own, so it cannot drive the availability "
            "of other entities. Give the stream a name."
        )
        raise ValueError(msg)
    return fed


def _validate_periodic_early(
    name: str,
    registered_names: frozenset[str] | set[str],
    interval: object,
) -> None:
    """Validate name uniqueness and interval positivity at decoration time."""
    validate_mqtt_name(name)
    if name in registered_names:
        msg = f"Name '{name}' is already registered"
        raise ValueError(msg)
    if isinstance(interval, (int, float)) and interval <= 0:
        msg = f"Periodic interval for '{name}' must be positive, got {interval}"
        raise ValueError(msg)


def _check_no_port_in_signature(
    func: Callable[..., Any], hints: dict[str, Any], item_type: type
) -> None:
    """Raise TypeError if func declares a port parameter for item_type directly."""
    for _, ann in hints.items():
        origin = get_origin(ann)
        if origin is StreamablePort:
            port_ann_args = get_args(ann)
            if port_ann_args and port_ann_args[0] == item_type:
                item_type_name = getattr(item_type, "__name__", repr(item_type))
                msg = (
                    f"Function {_callable_qualname(func)!r} declares both "
                    f"Stream[{item_type_name}] and"
                    f" StreamablePort[{item_type_name}]. "
                    "The framework owns the stream-source lifecycle "
                    "(open, start_scan, stop_scan, close) — "
                    "remove the port parameter. "
                    "To access the adapter for non-lifecycle operations, "
                    "inject its concrete type instead."
                )
                raise TypeError(msg)
