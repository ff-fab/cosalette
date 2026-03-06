"""Registration types and helper functions for the cosalette App.

This private module contains the internal dataclasses, type aliases,
and free functions used by :class:`cosalette._app.App` for device,
telemetry, command, and adapter registration.

Separated from ``_app.py`` for maintainability — the ``App`` class
imports everything it needs from here.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from cosalette._context import AppContext
from cosalette._injection import build_injection_plan, resolve_kwargs
from cosalette._persist import PersistPolicy
from cosalette._settings import Settings
from cosalette._strategies import PublishStrategy

type IntervalSpec = float | Callable[[Settings], float]
"""Interval for telemetry: a concrete float or a settings-derived callable."""

RegistryType = Literal["device", "telemetry", "command"]
"""The kind of registration being added."""

type _AnyRegistration = (
    _DeviceRegistration | _TelemetryRegistration | _CommandRegistration
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DeviceRegistration:
    """Internal record of a registered @app.device function."""

    name: str
    func: Callable[..., Awaitable[None]]
    injection_plan: list[tuple[str, type]]
    is_root: bool = False
    init: Callable[..., Any] | None = None
    init_injection_plan: list[tuple[str, type]] | None = None


@dataclass(frozen=True, slots=True)
class _TelemetryRegistration:
    """Internal record of a registered @app.telemetry function."""

    name: str
    func: Callable[..., Awaitable[dict[str, object] | None]]
    injection_plan: list[tuple[str, type]]
    interval: IntervalSpec
    is_root: bool = False
    publish_strategy: PublishStrategy | None = None
    persist_policy: PersistPolicy | None = None
    init: Callable[..., Any] | None = None
    init_injection_plan: list[tuple[str, type]] | None = None
    group: str | None = None


@dataclass(frozen=True, slots=True)
class _CommandRegistration:
    """Internal record of a registered @app.command handler."""

    name: str
    func: Callable[..., Awaitable[dict[str, object] | None]]
    injection_plan: list[tuple[str, type]]
    mqtt_params: frozenset[str]  # subset of {"topic", "payload"} declared by handler
    is_root: bool = False
    init: Callable[..., Any] | None = None
    init_injection_plan: list[tuple[str, type]] | None = None


@dataclass(frozen=True, slots=True)
class _AdapterEntry:
    """Internal record of a registered adapter.

    Both impl and dry_run can be either a class, a factory callable,
    or a ``module:ClassName`` string for lazy import.
    """

    impl: type | str | Callable[..., object]
    dry_run: type | str | Callable[..., object] | None = None


def _is_async_context_manager(obj: object) -> bool:
    """Check if an object implements the async context manager protocol.

    Uses ``hasattr`` checks rather than
    ``isinstance(obj, AbstractAsyncContextManager)`` because the ABC
    requires explicit registration — duck-typing is more inclusive.
    """
    return hasattr(obj, "__aenter__") and hasattr(obj, "__aexit__")


# ---------------------------------------------------------------------------
# Lifespan type + no-op default
# ---------------------------------------------------------------------------

type LifespanFunc = Callable[[AppContext], AbstractAsyncContextManager[None]]
"""Type alias for the lifespan parameter."""


@asynccontextmanager
async def _noop_lifespan(_ctx: AppContext) -> AsyncIterator[None]:
    """No-op lifespan used when no user lifespan is provided."""
    yield


# ---------------------------------------------------------------------------
# Adapter resolution helpers
# ---------------------------------------------------------------------------


def _build_adapter_providers(settings: Settings) -> dict[type, Any]:
    """Build the provider map available during adapter resolution.

    At adapter-resolution time only the parsed :class:`Settings`
    instance is available (MQTT, clock, and device contexts are
    created later in the bootstrap sequence).

    Returns a mapping keyed by both :class:`Settings` and the
    concrete settings subclass, so factories can annotate with
    either.
    """
    # Extend this dict when new types become injectable at
    # adapter-resolution time (e.g. app metadata, logging config).
    providers: dict[type, Any] = {Settings: settings}
    settings_type = type(settings)
    if settings_type is not Settings:
        providers[settings_type] = settings
    return providers


def _validate_init(init: Callable[..., Any]) -> None:
    """Reject async callables passed as ``init=``.

    The init callback is invoked synchronously before the handler
    loop.  An ``async def`` would silently return an unawaited
    coroutine instead of the desired result.

    Raises:
        TypeError: If *init* is a coroutine function.
    """
    if asyncio.iscoroutinefunction(init):
        msg = (
            "init= must be a synchronous callable, not async. "
            "Use a regular function or a class with __call__."
        )
        raise TypeError(msg)
    # Catch callable instances whose __call__ is async (iscoroutinefunction
    # only inspects the object itself, not its __call__ dunder).
    if inspect.iscoroutinefunction(type(init).__call__):
        msg = (
            "init= must be a synchronous callable, not async. "
            "The __call__ method is a coroutine function."
        )
        raise TypeError(msg)


def _call_init(
    init: Callable[..., Any],
    init_plan: list[tuple[str, type]] | None,
    providers: dict[type, Any],
) -> Any:
    """Invoke an init callback with signature-based injection.

    Validates the return type does not shadow framework-provided
    types, then returns the result.

    Raises:
        TypeError: If the init result type shadows a known injectable.
    """
    from cosalette._injection import KNOWN_INJECTABLE_TYPES

    _validate_init(init)  # defense-in-depth

    kwargs = resolve_kwargs(init_plan or [], providers)
    result = init(**kwargs)

    result_type = type(result)
    if result_type in KNOWN_INJECTABLE_TYPES:
        msg = (
            f"init= callback returned {result_type.__name__!r}, which "
            f"shadows a framework-provided type. Use a wrapper class "
            f"or a different type."
        )
        raise TypeError(msg)

    return result


def _call_factory(
    factory: Callable[..., object],
    providers: dict[type, Any],
) -> object:
    """Invoke an adapter factory with signature-based injection.

    Introspects *factory*'s parameters and resolves each from
    *providers*.  Zero-arg factories are called directly (backward
    compatible).  This reuses the same :func:`build_injection_plan`
    / :func:`resolve_kwargs` machinery that device handlers use.

    Args:
        factory: A callable returning an adapter instance.
        providers: Type → instance map (currently just Settings).

    Returns:
        The adapter instance created by *factory*.
    """
    plan = build_injection_plan(factory)
    if not plan:
        return factory()
    kwargs = resolve_kwargs(plan, providers)
    return factory(**kwargs)


# ---------------------------------------------------------------------------
# Name-validation helpers (extracted from App)
# ---------------------------------------------------------------------------


def colliding_names(
    registry_type: RegistryType,
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
) -> set[str]:
    """Return names that would collide with *registry_type*.

    Rules:
    - ``'device'`` collides with ALL other registrations
    - ``'telemetry'`` collides with devices + other telemetry (NOT commands)
    - ``'command'`` collides with devices + other commands (NOT telemetry)
    """
    # Device names always collide with everything
    names: set[str] = {r.name for r in devices}

    if registry_type == "device":
        names |= {r.name for r in telemetry}
        names |= {r.name for r in commands}
    elif registry_type == "telemetry":
        names |= {r.name for r in telemetry}
    elif registry_type == "command":
        names |= {r.name for r in commands}

    return names


def validate_name_unique(name: str, existing: set[str]) -> None:
    """Raise if *name* already appears in *existing*."""
    if name in existing:
        msg = f"Device name '{name}' is already registered"
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
            "wildcard subscription issues — {prefix}/+/state won't "
            "match {prefix}/state"
        )


def check_device_name(
    name: str,
    *,
    registry_type: RegistryType,
    is_root: bool = False,
    devices: list[_DeviceRegistration],
    telemetry: list[_TelemetryRegistration],
    commands: list[_CommandRegistration],
) -> None:
    """Raise if name collides with an incompatible registration.

    Name sharing rules:
    - telemetry + command: ALLOWED (different MQTT suffixes)
    - All other cross-type combinations: REJECTED
    - Same-type duplicates: REJECTED

    When *is_root* is True, also enforces that at most one root
    (unnamed) device exists and logs a warning when root and named
    devices are mixed.

    Root and mixing checks are always global (all registrations)
    because they concern MQTT topic layout, not name scoping.
    """
    existing = colliding_names(registry_type, devices, telemetry, commands)
    validate_name_unique(name, existing)

    # Shared tel↔cmd names must agree on is_root to avoid MQTT
    # namespace confusion ({prefix}/state vs {prefix}/{name}/state).
    if registry_type in ("telemetry", "command"):
        complement = commands if registry_type == "telemetry" else telemetry
        for peer in complement:
            if peer.name == name and peer.is_root != is_root:
                msg = (
                    f"Cannot share name '{name}' between root and named "
                    f"registrations — MQTT topic namespaces would conflict"
                )
                raise ValueError(msg)

    # Root / mixing checks use ALL registrations (MQTT layout concern)
    all_regs: list[_AnyRegistration] = [
        *devices,
        *telemetry,
        *commands,
    ]
    all_names: set[str] = set()
    has_root = False
    for reg in all_regs:
        all_names.add(reg.name)
        if reg.is_root:
            has_root = True

    if is_root:
        validate_single_root(has_root)
    warn_if_mixing(is_root, has_root=has_root, has_named=bool(all_names))
