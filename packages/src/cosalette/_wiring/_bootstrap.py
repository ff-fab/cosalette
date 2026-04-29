"""Bootstrap wiring: Settings and store bootstrap functions."""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any

from cosalette._clock import ClockPort
from cosalette._injection import build_injection_plan, resolve_kwargs
from cosalette._persistence._stores import Store
from cosalette._settings import Settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("cosalette._wiring")


def resolve_settings(
    settings: Settings | None,
    eager_settings: Settings | None,
    settings_class: type[Settings],
) -> Settings:
    """Return the effective settings instance.

    Priority: explicit override > eagerly-created > fresh from class.
    """
    if settings is not None:
        return settings
    if eager_settings is not None:
        return eager_settings
    return settings_class()


def resolve_store_factory(
    factory: Callable[..., Store],
    settings: Settings,
    adapters: dict[type, object],
) -> Store:
    """Invoke a store factory with signature-based DI.

    Called during bootstrap after settings and adapters are resolved
    but before configure hooks run.  The factory receives whichever
    DI providers its signature requests (Settings subclass, adapter
    ports, etc.).

    Raises:
        TypeError: If the factory is async or returns a non-Store object.
    """
    if inspect.iscoroutinefunction(factory):
        msg = (
            f"store factory {factory!r} is async; store factories must be "
            f"synchronous (bootstrap runs before the async event loop starts)"
        )
        raise TypeError(msg)

    providers: dict[type, Any] = {Settings: settings}
    settings_type = type(settings)
    if settings_type is not Settings:
        providers[settings_type] = settings
    for port_type, instance in adapters.items():
        providers[port_type] = instance

    plan = build_injection_plan(factory)
    kwargs = resolve_kwargs(plan, providers) if plan else {}
    result = factory(**kwargs)

    if not isinstance(result, Store):
        msg = (
            f"store factory {factory!r} returned {type(result).__name__!r}, "
            f"expected a Store instance"
        )
        raise TypeError(msg)
    return result


def _build_configure_providers(
    settings: Settings,
    adapters: dict[type, object],
    clock: ClockPort,
) -> dict[type, Any]:
    """Build the DI providers map for on_configure hooks."""
    providers: dict[type, Any] = {
        Settings: settings,
        logging.Logger: logging.getLogger("cosalette.configure"),
        ClockPort: clock,
    }
    settings_type = type(settings)
    if settings_type is not Settings:
        providers[settings_type] = settings
    for port_type, instance in adapters.items():
        providers[port_type] = instance
    return providers


async def run_configure_hooks(
    hooks: list[Callable[..., Any]],
    settings: Settings,
    adapters: dict[type, object],
    clock: ClockPort,
) -> None:
    """Execute on_configure hooks with dependency injection."""
    if not hooks:
        return
    providers = _build_configure_providers(settings, adapters, clock)
    for hook in hooks:
        plan = build_injection_plan(hook)
        kwargs = resolve_kwargs(plan, providers)
        if inspect.iscoroutinefunction(hook):
            await hook(**kwargs)
        else:
            hook(**kwargs)
