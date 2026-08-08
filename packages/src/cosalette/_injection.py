"""Signature-based dependency injection for handler functions.

Inspects handler function signatures at registration time and builds an
*injection plan* — a list of ``(parameter_name, resolved_type)`` tuples.
At call time, :func:`resolve_kwargs` (plain DI) and
:func:`resolve_request_kwargs` (request-scoped with ``Annotated`` markers)
map those types to live provider
objects (DeviceContext, Settings, Logger, etc.) and returns a kwargs dict
ready for ``handler(**kwargs)``.

**Resolution rules:**

1. Prefer explicit ``Annotated`` markers (``Payload()``, ``Topic()``,
   ``Depends(dep)``) for binding request-scoped data.  These are resolved
   by :func:`resolve_request_kwargs`.
2. Name-based conventions apply as a shorthand for common cases (no marker
   required):

   - A parameter named ``payload`` with a non-``str`` annotation receives
     the parsed MQTT payload (via TypeAdapter) **only when** a payload is
     present (command/triggered contexts).
   - A parameter named ``topic`` with a plain ``str`` annotation receives
     the raw MQTT topic string.

   Note: these conventions fire silently at runtime; use explicit markers
   to avoid ambiguity in non-standard parameter naming.
3. All other parameters are matched by *type annotation* against the
   providers map via exact type, Settings subclass, or issubclass matching.
4. Uses :func:`typing.get_type_hints` for robust annotation resolution
   (handles ``from __future__ import annotations`` / PEP 563).
5. Zero-parameter functions are valid (empty plan).
6. Missing annotation → ``TypeError`` at registration time (fail-fast).
7. Unknown types are recorded in the plan; resolution failure is deferred
   to call time so that adapters can be registered in any order.

See Also:
    ADR-006 — Hexagonal architecture (adapter resolution).
    ADR-010 — Device archetypes.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import types
import warnings
from collections.abc import Collection, Sequence
from contextvars import ContextVar
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._persistence._stores import DeviceStore
from cosalette._runners._contracts import parse_payload
from cosalette._runners._stream_types import Stream
from cosalette._runners._trigger import TriggerPayload
from cosalette._settings import Settings
from cosalette._utils import _callable_qualname
from cosalette.di import (
    _UNSET,
    _DependsMarker,
    _MarkerConstructionError,
    _OptionalMarker,
)
from cosalette.mqtt import Message, _PayloadMarker, _TopicMarker

logger = logging.getLogger(__name__)

# The set of types the framework knows how to provide.
# Mapping: annotation type → human-readable source description (for errors).
KNOWN_INJECTABLE_TYPES: dict[type, str] = {
    DeviceContext: "DeviceContext (full context)",
    Settings: "ctx.settings",
    logging.Logger: "logging.getLogger('cosalette.<device>')",
    ClockPort: "ctx.clock",
    asyncio.Event: "shutdown event",
    DeviceStore: "per-device persistence store",
    Stream: "async stream iterator for push-to-pull bridging",
    TriggerPayload: "trigger context (triggerable telemetry)",
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


def _hint_source_for(func: Any) -> Any:
    """Return the object from which :func:`typing.get_type_hints` should read.

    Unwraps :class:`functools.partial` to reach the underlying callable, then
    substitutes the function that actually carries ``__annotations__`` and
    ``__globals__`` so that PEP 563 string annotations resolve against the
    correct module globals:

    - class object → its ``__init__``
    - callable instance → its class's ``__call__``
    - anything else → unchanged
    """
    unwrapped = func.func if isinstance(func, functools.partial) else func
    if isinstance(unwrapped, type):
        return unwrapped.__init__
    if not inspect.isroutine(unwrapped) and callable(unwrapped):
        # A callable *instance* carries neither __annotations__ nor
        # __globals__; both live on the class's __call__.  Without this,
        # eval() would run against an empty globals dict and every
        # PEP 563 annotation would look like a missing import.
        call = type(unwrapped).__call__
        if inspect.isfunction(call):
            return call
    return unwrapped


def _resolve_hints(hint_source: Any, func: Any) -> dict[str, Any]:
    """Resolve ``get_type_hints()`` for *hint_source*, surfacing failures.

    :func:`typing.get_type_hints` resolves PEP 563 string annotations for the
    whole callable at once, so a single unresolvable annotation wipes out the
    hints for every parameter.  That is recoverable — :func:`_resolve_annotation`
    re-tries per parameter via ``eval()`` — but it must not be silent:

    - a DI marker rejecting its argument (e.g. ``Depends(async_fn)``) is the
      user's real error and is re-raised unchanged;
    - any other failure is logged at ``WARNING`` with its cause before falling
      back to raw annotations.

    Args:
        hint_source: Object to read annotations from (see :func:`_hint_source_for`).
        func: The original callable — used for error/log messages only.

    Returns:
        The resolved hints, or ``{}`` when resolution failed.
    """
    try:
        return get_type_hints(hint_source, include_extras=True)
    except _MarkerConstructionError:
        raise
    except Exception as exc:
        logger.warning(
            "get_type_hints() failed for %s (%s: %s); falling back to "
            "per-parameter annotation resolution",
            _callable_qualname(func),
            type(exc).__name__,
            exc,
        )
        logger.debug("get_type_hints() traceback", exc_info=True)
        return {}


def _eval_deferred_annotation(name: str, annotation: str, func: Any) -> Any:
    """Evaluate a PEP 563 deferred annotation string in *func*'s globals.

    Args:
        name: Parameter name (for error messages).
        annotation: The deferred annotation source string.
        func: Callable whose ``__globals__`` provide the namespace — see
            :func:`_hint_source_for`.

    Returns:
        The evaluated annotation object.

    Raises:
        TypeError: If evaluation fails.  A DI marker that rejected its
            argument (e.g. ``Depends(async_fn)``) is re-raised verbatim —
            under PEP 563 the marker is only constructed here, so that *is*
            the user's real error.  Anything else is reported as an
            unresolvable annotation, chained to its cause.
    """
    try:
        # SAFETY: This eval() resolves PEP 563 forward-reference strings
        # (e.g. "MqttPort") back to their types.  The input is the
        # function's own annotation — set by the Python compiler from
        # source — not user-supplied data.  The namespace is restricted
        # to the declaring module's globals.  If third-party code
        # registers handlers, their annotations are still compiled from
        # source by `from __future__ import annotations`.
        # Alternative: typing.get_type_hints() handles this but can
        # fail on unresolvable forward refs and raises different errors;
        # eval + explicit error handling gives clearer diagnostics.
        return eval(  # noqa: S307
            annotation,
            getattr(func, "__globals__", {}),
        )
    except _MarkerConstructionError:
        raise
    except Exception as exc:
        msg = (
            f"Parameter '{name}' of handler {_callable_qualname(func)!r} "
            f"has unresolvable annotation {annotation!r}: "
            f"{type(exc).__name__}: {exc}. "
            f"Ensure the type is imported and available."
        )
        raise TypeError(msg) from exc


# Keep in sync with: _resolve_annotated_marker() dispatch,
# _resolve_annotated_request() dispatch.
_BINDING_MARKERS = (
    _DependsMarker,
    _PayloadMarker,
    _TopicMarker,
    _OptionalMarker,
)


def _build_optional_plan_entry(
    name: str,
    param: inspect.Parameter,
    inner: Any,
    func: Any,
) -> Any:
    """Build a plan annotation for ``Annotated[T | None, Optional()]``."""
    inner_origin = get_origin(inner)
    if inner_origin in (types.UnionType, Union):
        inner_args = [a for a in get_args(inner) if a is not type(None)]
        if len(inner_args) != 1:
            msg = (
                f"Parameter '{name}' of handler {_callable_qualname(func)!r}: "
                f"Optional() inner type must be a single concrete type or "
                f"T | None, got {inner!r}."
            )
            raise TypeError(msg)
        concrete = inner_args[0]
    else:
        concrete = inner

    if concrete is type(None):
        msg = (
            f"Parameter '{name}' of handler {_callable_qualname(func)!r}: "
            f"Optional() requires a concrete inner type, got None."
        )
        raise TypeError(msg)

    if not isinstance(concrete, type):
        msg = (
            f"Parameter '{name}' of handler {_callable_qualname(func)!r}: "
            f"Optional() requires a concrete type, got {concrete!r}. "
            f"Generic types like list[str] are not supported as injectable types."
        )
        raise TypeError(msg)

    if param.default is inspect.Parameter.empty:
        captured_default = _UNSET
    else:
        captured_default = param.default
    return Annotated[concrete, _OptionalMarker(default=captured_default)]


def _resolve_annotated_marker(
    name: str,
    param: inspect.Parameter,
    annotation: Any,
    func: Any,
) -> type:
    """Validate and resolve an ``Annotated[T, marker]`` plan annotation."""
    args = get_args(annotation)
    found_markers = [m for m in args[1:] if isinstance(m, _BINDING_MARKERS)]

    if len(found_markers) == 0:
        msg = (
            f"Parameter '{name}' of handler {_callable_qualname(func)!r} "
            f"has unsupported Annotated marker {annotation!r}. "
            f"Use Depends(), Payload(), Topic(), or Optional() "
            f"markers from cosalette."
        )
        raise TypeError(msg)

    if len(found_markers) > 1:
        marker_reprs = ", ".join(repr(m) for m in found_markers)
        msg = (
            f"Parameter '{name}' of handler {_callable_qualname(func)!r} "
            f"has multiple binding markers: {marker_reprs}. "
            f"Only one binding marker (Depends(), Payload(), Topic(), "
            f"or Optional()) may be used per parameter."
        )
        raise TypeError(msg)

    marker = found_markers[0]

    if isinstance(marker, _TopicMarker):
        inner = args[0]
        if inner is not str:
            msg = (
                f"Parameter '{name}' of handler {_callable_qualname(func)!r}: "
                f"Topic() requires a str inner type, got {inner!r}."
            )
            raise TypeError(msg)
        return annotation

    if isinstance(marker, _OptionalMarker):
        return _build_optional_plan_entry(name, param, args[0], func)

    # _DependsMarker or _PayloadMarker — preserve full Annotated type in plan
    return annotation


def _generic_annotation_error(name: str, annotation: Any, func: Any) -> str:
    """Build the TypeError message for a non-concrete annotation."""
    ann_origin = get_origin(annotation)
    if ann_origin in (types.UnionType, Union):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            t = non_none[0]
            t_name = getattr(t, "__name__", repr(t))
            return (
                f"Parameter '{name}' of handler {_callable_qualname(func)!r} "
                f"has annotation {annotation!r}. "
                f"For an optional dependency use "
                f"Annotated[{t_name} | None, Optional()]; "
                f"for an optional payload use "
                f"Annotated[{t_name} | None, Payload()]."
            )
        return (
            f"Parameter '{name}' of handler {_callable_qualname(func)!r} "
            f"has annotation {annotation!r}. "
            f"For an optional dependency use "
            f"Annotated[T | None, Optional()]; "
            f"for an optional payload use "
            f"Annotated[T | None, Payload()]."
        )
    ann_name = repr(annotation)
    return (
        f"Parameter '{name}' of handler {_callable_qualname(func)!r} "
        f"has annotation {annotation!r} which is not a concrete type. "
        f"For a generic payload use Annotated[{ann_name}, Payload()] "
        f"(supported via TypeAdapter per ADR-046)."
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
        annotation = _eval_deferred_annotation(name, annotation, func)

    if annotation is inspect.Parameter.empty:
        msg = (
            f"Parameter '{name}' of handler {_callable_qualname(func)!r} "
            f"has no type annotation. All handler parameters must "
            f"be annotated so the framework can inject dependencies."
        )
        raise TypeError(msg)

    origin = get_origin(annotation)
    if origin is Stream:
        return annotation

    if origin is Annotated:
        return _resolve_annotated_marker(name, param, annotation, func)

    if not isinstance(annotation, type):
        raise TypeError(_generic_annotation_error(name, annotation, func))

    return annotation


def build_injection_plan(
    func: Any,
    *,
    mqtt_params: Collection[str] | None = None,
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
        mqtt_params: Parameter names that the framework injects directly at
            dispatch time (e.g. ``{"events"}`` for reactors, or raw
            ``{"topic", "payload"}`` for commands).  These are excluded
            from the injection plan unconditionally, regardless of their
            annotation type.

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

    # For classes, inspect.signature already delegates to __init__.
    # Resolve type hints from __init__ too so PEP 563 string
    # annotations evaluate against the correct module globals
    # (classes themselves don't carry __globals__).
    # For functools.partial, unwrap to the underlying callable so that
    # get_type_hints() and eval() can access __annotations__ and __globals__.
    _hint_source = _hint_source_for(func)
    hints = _resolve_hints(_hint_source, func)

    plan: list[tuple[str, type]] = []

    for name, param in sig.parameters.items():
        if name == "return":
            continue

        # Skip framework-reserved parameters unconditionally.  Callers are
        # responsible for populating *mqtt_params* with only the names that
        # the framework owns at dispatch time:
        #   - reactor registration passes {"events"} (list[str]) — always skip.
        #   - command registration passes detect_raw_mqtt_params() results,
        #     which only contains names whose annotation is plain str (or
        #     unannotated).  Typed params like `payload: SomeModel` are *not*
        #     in that set and therefore remain in the plan for typed binding.
        if mqtt_params and name in mqtt_params:
            continue

        # Reject parameter kinds that can't be passed as **kwargs
        if param.kind not in _INJECTABLE_KINDS:
            msg = (
                f"Parameter '{name}' of handler {_callable_qualname(func)!r} "
                f"has unsupported kind {param.kind.name}. "
                f"Only regular and keyword-only parameters can be injected."
            )
            raise TypeError(msg)

        annotation = _resolve_annotation(name, param, hints, _hint_source)
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
    per_device_config: Any = None,
) -> dict[type, Any]:
    """Build the providers map from a DeviceContext.

    The providers map contains all framework-known injectable types
    plus all registered adapter port types.

    Args:
        ctx: The per-device context to extract providers from.
        device_name: Device name for logger naming.
        per_device_config: Optional per-device configuration object
            from dict-name expansion.  When set, its concrete type
            is added to the providers map.

    Returns:
        A dict mapping types to live provider instances.
    """
    providers: dict[type, Any] = {
        DeviceContext: ctx,
        Settings: ctx.settings,
        logging.Logger: logging.getLogger(f"cosalette.{device_name}"),
        ClockPort: ctx.clock,
        asyncio.Event: ctx._shutdown_event,
        TriggerPayload: TriggerPayload.scheduled(),
    }
    # Add the concrete Settings subclass too, so users can annotate
    # with their own Settings subclass and still get injection.
    settings_type = type(ctx.settings)
    if settings_type is not Settings:
        providers[settings_type] = ctx.settings

    # Add all adapter port types from the context's adapter registry.
    for port_type, instance in ctx._adapters.items():
        providers[port_type] = instance

    if per_device_config is not None:
        providers[type(per_device_config)] = per_device_config

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
    """Find a provider whose type is a subclass of *annotation*.

    Raises:
        TypeError: If more than one distinct provider matches (ambiguous).
    """
    matches: list[tuple[type, Any]] = []
    for ptype, instance in providers.items():
        try:
            if issubclass(ptype, annotation):
                matches.append((ptype, instance))
        except TypeError:
            continue

    # Dedupe by instance identity
    seen_ids: set[int] = set()
    distinct: list[tuple[type, Any]] = []
    for ptype, instance in matches:
        if id(instance) not in seen_ids:
            seen_ids.add(id(instance))
            distinct.append((ptype, instance))

    if len(distinct) > 1:
        candidates = ", ".join(_type_display(ptype) for ptype, _ in distinct)
        msg = (
            f"Ambiguous provider for annotation {_type_display(annotation)!r}: "
            f"multiple registered providers match — {candidates}. "
            f"Use a more specific annotation to disambiguate."
        )
        raise TypeError(msg)

    if len(distinct) == 1:
        return distinct[0][1]

    return _SENTINEL


def _type_display(annotation: Any) -> str:
    """Render *annotation* as a readable ``module.QualName`` string.

    Generic aliases (``Stream[int]``, ``int | None``) forward ``__qualname__``
    to their origin and would lose their parameters, so they fall back to
    ``repr()``.  ``builtins`` is omitted as a module prefix.
    """
    if not isinstance(annotation, type):
        return repr(annotation)
    module = annotation.__module__
    if module == "builtins":
        return annotation.__qualname__
    return f"{module}.{annotation.__qualname__}"


def _unresolved_message(
    param_name: str,
    annotation: Any,
    providers: dict[type, Any],
) -> str:
    """Build the actionable error text for an unresolvable parameter."""
    missing = _type_display(annotation)
    short = annotation.__name__ if isinstance(annotation, type) else missing
    available = ", ".join(sorted(_type_display(t) for t in providers)) or "(none)"
    return (
        f"Cannot resolve parameter '{param_name}': no provider is registered "
        f"for type {missing}. Register an implementation with "
        f"app.adapter({short}, <implementation>) before the app starts, or "
        f"annotate the parameter with a type the framework provides. "
        f"Available types: {available}"
    )


def _try_resolve_single(
    param_name: str,  # noqa: ARG001
    annotation: type,
    providers: dict[type, Any],
) -> Any:
    """Resolve *annotation* from *providers*, returning ``_SENTINEL`` if not found.

    Propagates ``TypeError`` from ambiguous subclass matches (a real error);
    only "no provider" is suppressed to ``_SENTINEL``.
    """
    if annotation in providers:
        return providers[annotation]

    if _is_settings_subclass(annotation):
        result = _find_settings_instance(annotation, providers)
        if result is not _SENTINEL:
            return result

    # _find_subclass_instance raises TypeError on ambiguous match
    return _find_subclass_instance(annotation, providers)


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
    result = _try_resolve_single(param_name, annotation, providers)
    if result is _SENTINEL:
        raise TypeError(_unresolved_message(param_name, annotation, providers))
    return result


def resolve_kwargs(
    plan: Sequence[tuple[str, type]],
    providers: dict[type, Any],
) -> dict[str, Any]:
    """Build a kwargs dict from an injection plan and providers map.

    .. deprecated::
        Use :func:`resolve_request_kwargs` instead.  This function has no
        production callers after the migration to request-scoped resolution
        and will be removed in a future version.

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
    warnings.warn(
        "resolve_kwargs() is deprecated and has no production callers. "
        "Use resolve_request_kwargs() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return {
        param_name: _resolve_single(param_name, annotation, providers)
        for param_name, annotation in plan
    }


# ---------------------------------------------------------------------------
# Request-scoped resolution — Annotated markers + Message type
# ---------------------------------------------------------------------------


def _is_raw_string_annotation(hint: Any) -> bool:
    """Return ``True`` when *hint* represents a plain ``str`` (or no) annotation.

    Used by :func:`detect_raw_mqtt_params` to identify parameters whose
    annotation means "inject the raw MQTT string".
    """
    return (
        hint is str
        or hint is inspect.Parameter.empty
        or hint in ("str", "builtins.str")
    )


def detect_raw_mqtt_params(func: Any) -> frozenset[str]:
    """Detect which of ``{"topic", "payload"}`` parameters are raw MQTT strings.

    A parameter is treated as *raw MQTT* only when its annotation is the
    plain ``str`` type or when it has no annotation at all.  Parameters
    named ``"topic"`` or ``"payload"`` with any other annotation (e.g.
    ``payload: SomeModel``, ``topic: Annotated[str, Topic()]``) are
    excluded — the caller must handle them via typed binding instead.

    Args:
        func: Handler function to inspect.

    Returns:
        Subset of ``{"topic", "payload"}`` whose annotations are plain ``str``.
    """
    _hint_source = _hint_source_for(func)
    try:
        hints = get_type_hints(_hint_source, include_extras=True)
    except Exception:
        hints = {}

    sig = inspect.signature(func)
    raw: set[str] = set()
    for name in ("topic", "payload"):
        if name not in sig.parameters:
            continue
        hint = hints.get(name, sig.parameters[name].annotation)
        if _is_raw_string_annotation(hint):
            raw.add(name)
    return frozenset(raw)


@functools.lru_cache(maxsize=256)
def _cached_dep_plan(dep: Any) -> tuple[tuple[str, Any], ...]:
    """Return a cached injection plan for a ``Depends`` dependency callable.

    Wraps :func:`build_injection_plan` with an ``lru_cache`` so that
    per-message overhead for :func:`Depends` resolution is a single
    dict lookup rather than a fresh ``get_type_hints`` + signature
    inspection on every MQTT message.

    The plan is stored as a tuple-of-tuples (hashable) so it can be
    used as an ``lru_cache`` key and converted to a sequence for
    :func:`resolve_request_kwargs`.
    """
    return tuple(build_injection_plan(dep))


# Chain of ``Depends`` callables currently being resolved.  A ContextVar
# keeps the chain per-task instead of threading an extra argument through
# every resolution helper, and unwinds correctly on error.
_DEP_CHAIN: ContextVar[tuple[Any, ...]] = ContextVar("_DEP_CHAIN", default=())


def _reject_awaitable(param_name: str, dep: Any, result: Any) -> None:
    """Raise if a ``Depends`` callable returned an awaitable.

    ``Depends()`` rejects async callables at registration time, but a sync
    callable can still *return* a coroutine (``Depends(lambda: async_fn())``).
    Injecting that raw coroutine would silently hand the handler an
    un-awaited object, so fail loudly instead.

    Raises:
        TypeError: If *result* is awaitable.
    """
    if not inspect.isawaitable(result):
        return
    if inspect.iscoroutine(result):
        result.close()  # suppress "coroutine was never awaited" warning
    msg = (
        f"Dependency {_callable_qualname(dep)!r} for parameter "
        f"'{param_name}' returned an awaitable "
        f"({type(result).__qualname__}). Async dependencies are not "
        f"supported in the first wave — return a plain value from a "
        f"synchronous callable."
    )
    raise TypeError(msg)


def _resolve_depends(
    param_name: str,
    dep: Any,
    providers: dict[type, Any],
    topic: str | None,
    payload: str | None,
) -> Any:
    """Resolve an ``Annotated[T, Depends(dep)]`` parameter.

    Recursively resolves *dep*'s own parameters, guarding against dependency
    cycles and against callables that return an awaitable.

    Raises:
        TypeError: If *dep* is already being resolved (cycle), or returns an
            awaitable.
    """
    chain = _DEP_CHAIN.get()
    if any(seen is dep for seen in chain):
        names = " -> ".join(_callable_qualname(d) for d in (*chain, dep))
        msg = (
            f"Circular dependency detected while resolving parameter "
            f"'{param_name}': {names}. Depends() callables must not depend "
            f"on themselves, directly or transitively."
        )
        raise TypeError(msg)

    token = _DEP_CHAIN.set((*chain, dep))
    try:
        dep_kwargs = resolve_request_kwargs(
            _cached_dep_plan(dep), providers, topic=topic, payload=payload
        )
        result = dep(**dep_kwargs)
    finally:
        _DEP_CHAIN.reset(token)

    _reject_awaitable(param_name, dep, result)
    return result


def _resolve_optional(
    param_name: str,
    inner_type: Any,
    marker: _OptionalMarker,
    providers: dict[type, Any],
) -> Any:
    """Resolve ``Annotated[T, Optional()]``: return provider or captured default."""
    # _SENTINEL = no provider found; _UNSET (from di.py) = no param default captured.
    result = _try_resolve_single(param_name, inner_type, providers)
    if result is not _SENTINEL:
        return result
    return None if marker.default is _UNSET else marker.default


def _resolve_annotated_request(
    param_name: str,
    args: tuple[Any, ...],
    providers: dict[type, Any],
    topic: str | None,
    payload: str | None,
) -> Any:
    """Resolve an ``Annotated[T, marker]`` parameter.

    Handles ``Depends``, ``Payload``, ``Topic``, and ``Optional`` markers.
    ``build_injection_plan`` rejects all other markers at registration time,
    so this function only receives known markers.
    """
    inner_type = args[0]
    # Plan guarantees exactly one binding marker; scan metadata to skip
    # non-marker extras.
    marker = next(
        (m for m in args[1:] if isinstance(m, _BINDING_MARKERS)),
        None,
    )

    if isinstance(marker, _DependsMarker):
        return _resolve_depends(
            param_name, marker.dependency, providers, topic, payload
        )

    if isinstance(marker, _PayloadMarker):
        if marker.raw:
            return payload or ""
        # None payload is valid for optional types (scheduled telemetry runs);
        # TypeAdapter will validate and raise PayloadValidationError if T
        # does not accept None.
        return parse_payload(payload, inner_type, param=param_name)

    if isinstance(marker, _TopicMarker):
        if topic is None:
            msg = (
                f"Parameter '{param_name}': Topic() marker requires a request "
                f"context (MQTT topic) but none is available."
            )
            raise TypeError(msg)
        return topic

    if isinstance(marker, _OptionalMarker):
        return _resolve_optional(param_name, inner_type, marker, providers)

    # build_injection_plan() already raises TypeError for unknown Annotated
    # markers, so this path is unreachable. Any Annotated that reaches here
    # is a known marker type already handled above.
    msg = (
        f"Parameter '{param_name}': unhandled Annotated marker {marker!r}. "
        f"This is a bug in cosalette — please report it."
    )
    raise AssertionError(msg)


def _make_message(param_name: str, topic: str | None, payload: str | None) -> Message:
    """Construct a :class:`~cosalette.mqtt.Message`, raising if context is missing."""
    if topic is None or payload is None:
        msg = (
            f"Parameter '{param_name}': Message type requires a request "
            f"context but none is available (non-command context)."
        )
        raise TypeError(msg)
    return Message(topic=topic, payload=payload)


def _resolve_request_single(
    param_name: str,
    annotation: Any,
    providers: dict[type, Any],
    topic: str | None,
    payload: str | None,
) -> Any:
    """Resolve one plan entry supporting Annotated markers and Message type.

    Resolution order:

    1. ``Annotated[T, Depends(dep)]`` — calls *dep* with its own injection plan.
    2. ``Annotated[T, Payload()]`` — parses *payload* into T via TypeAdapter.
    3. ``Annotated[str, Topic()]`` — injects the full *topic* string.
    4. :class:`~cosalette.mqtt.Message` — injects a ``Message(topic, payload)``.
    5. Everything else — delegates to :func:`_resolve_single`.
    """
    origin = get_origin(annotation)
    if origin is Annotated:
        return _resolve_annotated_request(
            param_name, get_args(annotation), providers, topic, payload
        )

    # Message type — inject full inbound message object
    if annotation is Message:
        return _make_message(param_name, topic, payload)

    # Named "payload" with typed annotation → typed payload binding by convention.
    # Only activates when a request payload is present (command/triggered contexts).
    # Covers the common shorthand ``payload: SomeModel`` without an explicit marker.
    # Use ``Annotated[T | None, Payload()]`` for optional typed trigger payloads.
    if param_name == "payload" and annotation is not str and payload is not None:
        return parse_payload(payload, annotation, param=param_name)

    # Named "topic" with plain str annotation → topic string binding by convention.
    # Requires explicit ``Annotated[str, Topic()]`` for non-str annotations.
    if param_name == "topic" and annotation is str:
        if topic is None:
            msg = (
                f"Parameter '{param_name}': Topic string requires a request "
                f"context but none is available (non-command context)."
            )
            raise TypeError(msg)
        return topic

    # Default: existing DI resolution
    return _resolve_single(param_name, annotation, providers)


def resolve_request_kwargs(
    plan: Sequence[tuple[str, Any]],
    providers: dict[type, Any],
    *,
    topic: str | None = None,
    payload: str | None = None,
) -> dict[str, Any]:
    """Build a kwargs dict handling Annotated DI markers and request-bound params.

    Extends :func:`resolve_kwargs` to handle:

    - ``Annotated[T, Depends(dep)]``: calls *dep* with its own resolved kwargs.
    - ``Annotated[T, Payload()]``: parses *payload* into T via Pydantic TypeAdapter.
    - ``Annotated[str, Topic()]``: injects the full *topic* string.
    - :class:`~cosalette.mqtt.Message`: injects ``Message(topic, payload)``.

    Plain DI types fall back to :func:`_resolve_single` as before.

    Name-based conventions (activated only for non-``Annotated`` params):

    - A parameter named ``payload`` with a non-``str`` annotation receives
      the parsed payload (via TypeAdapter) **only when** *payload* is not
      ``None`` (i.e. in command/triggered contexts, not scheduled runs).
      For typed trigger payloads on optional types use
      ``Annotated[T | None, Payload()]`` instead.
    - A parameter named ``topic`` with a ``str`` annotation receives the raw
      topic string.  Non-``str`` annotations are not bound by this convention.

    Args:
        plan: Injection plan from :func:`build_injection_plan`.
        providers: Mapping of types to live instances.
        topic: Inbound MQTT topic string, or ``None`` if not request-scoped.
        payload: Inbound MQTT payload string, or ``None`` if not request-scoped.

    Returns:
        A kwargs dict ready for ``handler(**kwargs)``.

    Raises:
        TypeError: If a request-bound marker is used without the required context.
        PayloadValidationError: If payload JSON parsing or validation fails.
    """
    return {
        param_name: _resolve_request_single(
            param_name, annotation, providers, topic, payload
        )
        for param_name, annotation in plan
    }
