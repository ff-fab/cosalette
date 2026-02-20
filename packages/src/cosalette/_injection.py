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
import inspect
import logging
from typing import Any, get_type_hints

from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._settings import Settings

logger = logging.getLogger(__name__)

# The set of types the framework knows how to provide.
# Mapping: annotation type → human-readable source description (for errors).
KNOWN_INJECTABLE_TYPES: dict[type, str] = {
    DeviceContext: "DeviceContext (full context)",
    Settings: "ctx.settings",
    logging.Logger: "logging.getLogger('cosalette.<device>')",
    ClockPort: "ctx.clock",
    asyncio.Event: "shutdown event",
}

# Parameter kinds accepted by the injection system.  Only regular
# positional-or-keyword and keyword-only parameters can be passed
# via ``**kwargs`` at dispatch time.  Positional-only, ``*args``,
# and ``**kwargs`` parameters are rejected at registration time.
_INJECTABLE_KINDS: frozenset[inspect._ParameterKind] = frozenset(
    {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }
)


def _resolve_annotation(
    name: str,
    param: inspect.Parameter,
    hints: dict[str, Any],
    func: Any,
) -> type:
    """Resolve and validate the type annotation for a single parameter.

    Uses a three-stage fallback:

    1. Resolved hint from :func:`typing.get_type_hints` (handles PEP 563).
    2. Raw annotation from the signature object.
    3. ``eval()`` in the function's module globals for deferred strings.

    After resolution, validates that the annotation exists and is a
    concrete type (not a generic like ``Optional[T]``).

    Args:
        name: Parameter name (for error messages).
        param: The :class:`inspect.Parameter` object.
        hints: Pre-resolved type hints dict from ``get_type_hints(func)``.
        func: The original handler function (for ``__qualname__`` and
            ``__globals__``).

    Returns:
        The resolved concrete type.

    Raises:
        TypeError: If the annotation is missing, unresolvable, or not
            a concrete type.
    """
    # 1. Prefer the resolved hint from get_type_hints
    annotation = hints.get(name, inspect.Parameter.empty)

    # 2. Fall back to the raw annotation from the signature
    if annotation is inspect.Parameter.empty:
        annotation = param.annotation

    # 3. If it's a string (PEP 563 deferred), try to eval in
    #    the function's module globals
    if isinstance(annotation, str):
        try:
            annotation = eval(  # noqa: S307
                annotation,
                getattr(func, "__globals__", {}),
            )
        except Exception:
            msg = (
                f"Parameter '{name}' of handler {func.__qualname__!r} "
                f"has unresolvable annotation {annotation!r}. "
                f"Ensure the type is imported and available."
            )
            raise TypeError(msg) from None

    if annotation is inspect.Parameter.empty:
        msg = (
            f"Parameter '{name}' of handler {func.__qualname__!r} "
            f"has no type annotation. All handler parameters must "
            f"be annotated so the framework can inject dependencies."
        )
        raise TypeError(msg)

    if not isinstance(annotation, type):
        msg = (
            f"Parameter '{name}' of handler {func.__qualname__!r} "
            f"has annotation {annotation!r} which is not a type. "
            f"All handler parameters must be annotated with a "
            f"concrete type for dependency injection."
        )
        raise TypeError(msg)

    return annotation


def build_injection_plan(
    func: Any,
    *,
    mqtt_params: set[str] | None = None,
) -> list[tuple[str, type]]:
    """Inspect *func*'s signature and build an injection plan.

    At registration time this validates that every parameter carries a
    type annotation.  The plan records ``(param_name, annotation_type)``
    pairs.  Types that are not in :data:`KNOWN_INJECTABLE_TYPES` are
    still accepted (they may be adapter port types resolved at call
    time).

    Parameters whose names appear in *mqtt_params* are skipped — they
    are injected directly by the framework at dispatch time.

    Annotation resolution uses :func:`typing.get_type_hints` first
    (handles PEP 563 deferred annotations).  When that fails for a
    particular parameter (e.g. locally-defined types in tests), it
    falls back to ``eval()`` in the function's globals, then stores
    the raw annotation.

    Args:
        func: The handler function to inspect.
        mqtt_params: Parameter names that receive MQTT message values
            (e.g. ``{"topic", "payload"}``).  These are excluded from
            the injection plan.

    Returns:
        A list of ``(param_name, type)`` tuples — one per parameter.

    Raises:
        TypeError: If a parameter (not in *mqtt_params*) lacks a type
            annotation, or has an unsupported parameter kind (e.g.
            positional-only, ``*args``, ``**kwargs``).

    Note:
        Only concrete types are supported for injection annotations.
        Generic types (e.g. ``Optional[DeviceContext]``, ``list[str]``)
        are rejected at registration time.  This is an intentional
        design constraint — the DI system resolves by exact type
        identity or ``issubclass`` matching.
    """
    sig = inspect.signature(func)

    # get_type_hints resolves string annotations (PEP 563).
    # If the function has no annotations at all, this returns {}.
    try:
        hints = get_type_hints(func)
    except Exception:
        logger.debug(
            "get_type_hints() failed for %s, falling back to raw annotations",
            getattr(func, "__qualname__", func),
            exc_info=True,
        )
        hints = {}

    plan: list[tuple[str, type]] = []

    for name, param in sig.parameters.items():
        if name == "return":
            continue

        # Skip MQTT message params — injected at dispatch time
        if mqtt_params and name in mqtt_params:
            continue

        # Reject parameter kinds that can't be passed as **kwargs
        if param.kind not in _INJECTABLE_KINDS:
            msg = (
                f"Parameter '{name}' of handler {func.__qualname__!r} "
                f"has unsupported kind {param.kind.name}. "
                f"Only regular and keyword-only parameters can be injected."
            )
            raise TypeError(msg)

        annotation = _resolve_annotation(name, param, hints, func)
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


_SENTINEL = object()


def _find_settings_instance(
    annotation: type,
    providers: dict[type, Any],
) -> Any:
    """Find a Settings instance matching *annotation* via isinstance."""
    for ptype, instance in providers.items():
        if _is_settings_subclass(ptype) and isinstance(instance, annotation):
            return instance
    return _SENTINEL


def _find_subclass_instance(
    annotation: type,
    providers: dict[type, Any],
) -> Any:
    """Find a provider whose type is a subclass of *annotation*."""
    for ptype, instance in providers.items():
        try:
            if issubclass(ptype, annotation):
                return instance
        except TypeError:
            continue
    return _SENTINEL


def _resolve_single(
    param_name: str,
    annotation: type,
    providers: dict[type, Any],
) -> Any:
    """Resolve a single parameter from the providers map.

    Tries three strategies in order: exact match, Settings subclass
    match, then adapter port subclass match.

    Raises:
        TypeError: If no strategy can resolve the parameter.
    """
    # 1. Exact type match
    if annotation in providers:
        return providers[annotation]

    # 2. Settings subclass match
    if _is_settings_subclass(annotation):
        result = _find_settings_instance(annotation, providers)
        if result is not _SENTINEL:
            return result

    # 3. Adapter port type — try issubclass matching
    result = _find_subclass_instance(annotation, providers)
    if result is not _SENTINEL:
        return result

    msg = (
        f"Cannot resolve parameter '{param_name}': "
        f"type {annotation!r} is not available. "
        f"Available types: {list(providers.keys())}"
    )
    raise TypeError(msg)


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
    return {
        param_name: _resolve_single(param_name, annotation, providers)
        for param_name, annotation in plan
    }
