"""Signature-based dependency injection for handler functions.

Inspects handler function signatures at registration time and builds an
*injection plan* — a list of ``(parameter_name, resolved_type)`` tuples.
At call time, :func:`resolve_kwargs` maps those types to live provider
objects (DeviceContext, Settings, Logger, etc.) and returns a kwargs dict
ready for ``handler(**kwargs)``.

**Resolution rules:**

1. Match by *type annotation*, not by parameter name.
2. Uses :func:`typing.get_type_hints` for robust annotation resolution
   (handles ``from __future__ import annotations`` / PEP 563).
3. Zero-parameter functions are valid (empty plan).
4. Missing annotation → ``TypeError`` at registration time (fail-fast).
5. Unknown types are recorded in the plan; resolution failure is deferred
   to call time so that adapters can be registered in any order.

See Also:
    ADR-006 — Hexagonal architecture (adapter resolution).
    ADR-010 — Device archetypes.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from typing import Any, get_type_hints

from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._settings import Settings

# The set of types the framework knows how to provide.
# Mapping: annotation type → human-readable source description (for errors).
KNOWN_INJECTABLE_TYPES: dict[type, str] = {
    DeviceContext: "DeviceContext (full context)",
    Settings: "ctx.settings",
    logging.Logger: "logging.getLogger('cosalette.<device>')",
    ClockPort: "ctx.clock",
    asyncio.Event: "shutdown event",
}


def build_injection_plan(
    func: Any,
) -> list[tuple[str, type]]:
    """Inspect *func*'s signature and build an injection plan.

    At registration time this validates that every parameter carries a
    type annotation.  The plan records ``(param_name, annotation_type)``
    pairs.  Types that are not in :data:`KNOWN_INJECTABLE_TYPES` are
    still accepted (they may be adapter port types resolved at call
    time).

    Annotation resolution uses :func:`typing.get_type_hints` first
    (handles PEP 563 deferred annotations).  When that fails for a
    particular parameter (e.g. locally-defined types in tests), it
    falls back to ``eval()`` in the function's globals, then stores
    the raw annotation.

    Args:
        func: The handler function to inspect.

    Returns:
        A list of ``(param_name, type)`` tuples — one per parameter.

    Raises:
        TypeError: If any parameter lacks a type annotation.
    """
    sig = inspect.signature(func)

    # get_type_hints resolves string annotations (PEP 563).
    # If the function has no annotations at all, this returns {}.
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    plan: list[tuple[str, type]] = []

    for name, param in sig.parameters.items():
        if name == "return":
            continue

        # 1. Prefer the resolved hint from get_type_hints
        annotation = hints.get(name, inspect.Parameter.empty)

        # 2. Fall back to the raw annotation from the signature
        if annotation is inspect.Parameter.empty:
            annotation = param.annotation

        # 3. If it's a string (PEP 563 deferred), try to eval in
        #    the function's module globals
        if isinstance(annotation, str):
            with contextlib.suppress(Exception):
                annotation = eval(  # noqa: S307
                    annotation,
                    getattr(func, "__globals__", {}),
                )

        if annotation is inspect.Parameter.empty:
            msg = (
                f"Parameter '{name}' of handler {func.__qualname__!r} "
                f"has no type annotation. All handler parameters must "
                f"be annotated so the framework can inject dependencies."
            )
            raise TypeError(msg)

        plan.append((name, annotation))

    return plan


def _is_settings_subclass(annotation: type) -> bool:
    """Check if annotation is Settings or a subclass of it."""
    try:
        return issubclass(annotation, Settings)
    except TypeError:
        return False


def build_providers(
    ctx: DeviceContext,
    device_name: str,
) -> dict[type, Any]:
    """Build the providers map from a DeviceContext.

    The providers map contains all framework-known injectable types
    plus all registered adapter port types.

    Args:
        ctx: The per-device context to extract providers from.
        device_name: Device name for logger naming.

    Returns:
        A dict mapping types to live provider instances.
    """
    providers: dict[type, Any] = {
        DeviceContext: ctx,
        Settings: ctx.settings,
        logging.Logger: logging.getLogger(f"cosalette.{device_name}"),
        ClockPort: ctx.clock,
        asyncio.Event: ctx._shutdown_event,
    }
    # Add the concrete Settings subclass too, so users can annotate
    # with their own Settings subclass and still get injection.
    settings_type = type(ctx.settings)
    if settings_type is not Settings:
        providers[settings_type] = ctx.settings

    # Add all adapter port types from the context's adapter registry.
    for port_type, instance in ctx._adapters.items():
        providers[port_type] = instance

    return providers


def resolve_kwargs(
    plan: list[tuple[str, type]],
    providers: dict[type, Any],
) -> dict[str, Any]:
    """Build a kwargs dict from an injection plan and providers map.

    For each ``(param_name, annotation_type)`` in the plan, looks up
    the type in *providers*.  Settings subclasses are matched via
    ``issubclass`` if an exact match isn't found.

    Args:
        plan: Injection plan from :func:`build_injection_plan`.
        providers: Mapping of types to live instances.

    Returns:
        A kwargs dict ready for ``handler(**kwargs)``.

    Raises:
        TypeError: If a requested type cannot be resolved from providers.
    """
    kwargs: dict[str, Any] = {}

    for param_name, annotation in plan:
        # 1. Exact type match
        if annotation in providers:
            kwargs[param_name] = providers[annotation]
            continue

        # 2. Settings subclass match
        if _is_settings_subclass(annotation):
            for ptype, instance in providers.items():
                if _is_settings_subclass(ptype) and isinstance(instance, annotation):
                    kwargs[param_name] = instance
                    break
            else:
                msg = (
                    f"Cannot resolve parameter '{param_name}': "
                    f"type {annotation!r} is not available. "
                    f"Available types: {list(providers.keys())}"
                )
                raise TypeError(msg)
            continue

        # 3. Adapter port type — try issubclass matching
        for ptype, instance in providers.items():
            try:
                if issubclass(ptype, annotation):
                    kwargs[param_name] = instance
                    break
            except TypeError:
                continue
        else:
            msg = (
                f"Cannot resolve parameter '{param_name}': "
                f"type {annotation!r} is not available. "
                f"Available types: {list(providers.keys())}"
            )
            raise TypeError(msg)

    return kwargs
