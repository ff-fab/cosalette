"""Dependency injection markers for cosalette handlers.

Provides the :func:`Depends` and :func:`Optional` factories for declaring
dependency parameters in handler functions via PEP 593 :class:`~typing.Annotated`.

See Also:
    ADR-046 — Typed Handler Contract Validation.
    ADR-053 — Semantics of T | None optional dependency injection.
"""

from __future__ import annotations

import inspect
from typing import Any, override

# Sentinel for "no default provided" — distinct from None.
_UNSET: object = object()


class _MarkerConstructionError(TypeError):
    """A DI marker rejected its argument.

    Subclasses :class:`TypeError` so existing ``except TypeError`` handlers
    keep working.  The distinct type lets annotation resolution
    (:mod:`cosalette._injection`) recognise "the marker itself said no" and
    re-raise the message unchanged, instead of reporting it as an
    unresolvable annotation — markers constructed inside PEP 563 string
    annotations only run when the annotation is evaluated.
    """


class _DependsMarker:
    """PEP 593 Annotated metadata for handler dependency parameters.

    Created by :func:`Depends`.  The ``dependency`` callable is invoked
    at request time and its return value is injected into the parameter.
    """

    __slots__ = ("dependency",)

    def __init__(self, dependency: Any) -> None:
        self.dependency = dependency

    @override
    def __repr__(self) -> str:
        return f"Depends({self.dependency!r})"


_ASYNC_DEP_HINT = (
    "Async dependency functions are not supported in the first wave. "
    "Use a synchronous callable for Depends()."
)


def _is_async_callable(obj: Any) -> bool:
    """Return ``True`` when *obj* is a coroutine or async-generator function."""
    return inspect.iscoroutinefunction(obj) or inspect.isasyncgenfunction(obj)


def Depends(dependency: Any) -> _DependsMarker:
    """Declare a handler parameter as a resolved dependency.

    The *dependency* callable is invoked at request time.  Its return
    value is injected into the annotated parameter.  Dependency callables
    may themselves declare framework-injected types or other ``Depends``
    dependencies.  Only synchronous callables are supported in the first wave.

    Args:
        dependency: Sync callable whose return value is injected.

    Returns:
        A marker suitable for ``Annotated[ReturnType, Depends(callable)]``.

    Raises:
        TypeError: If *dependency* is an async function, a callable object
            whose ``__call__`` is async (neither is supported in the first
            wave), or is not hashable (dependency plans are cached by
            identity).

    Example::

        def get_device_id(topic: Annotated[str, Topic()]) -> str:
            return topic.split("/")[2]

        @app.command("devices/{device_id}/setpoint/set")
        async def handle_setpoint(
            device_id: Annotated[str, Depends(get_device_id)],
        ) -> ThermostatState:
            ...

    """
    if _is_async_callable(dependency):
        msg = f"{_ASYNC_DEP_HINT} Got: {dependency!r}"
        raise _MarkerConstructionError(msg)
    # Catch callable instances whose __call__ is async (iscoroutinefunction
    # only inspects the object itself, not its __call__ dunder) — mirrors
    # the init= validator in _registration/_model.py.
    if callable(dependency) and _is_async_callable(type(dependency).__call__):
        msg = (
            f"{_ASYNC_DEP_HINT} The __call__ method is a coroutine function. "
            f"Got: {dependency!r}"
        )
        raise _MarkerConstructionError(msg)
    try:
        hash(dependency)
    except TypeError:
        msg = (
            f"Depends() requires a hashable dependency callable — its "
            f"injection plan is cached by identity. Got unhashable "
            f"{type(dependency).__qualname__} instance: {dependency!r}. "
            f"Define __hash__ on the class, or wrap the call in a plain "
            f"function."
        )
        raise _MarkerConstructionError(msg) from None
    return _DependsMarker(dependency)


class _OptionalMarker:
    """PEP 593 Annotated metadata for optional handler dependency parameters.

    Created by :func:`Optional`.  When the framework cannot find a registered
    provider for the inner type ``T``, it injects the captured *default*
    instead (or ``None`` when no default is set).
    """

    __slots__ = ("default",)

    def __init__(self, default: Any = _UNSET) -> None:
        self.default = default

    @override
    def __repr__(self) -> str:
        return "Optional()"


def Optional() -> _OptionalMarker:  # noqa: N802  (intentional name shadow — mirrors Depends/Payload/Topic)
    """Declare a handler parameter as an optionally-resolved dependency.

    Resolves the inner provider type ``T`` if a provider is registered;
    otherwise injects the parameter's default value (implicitly ``None`` when
    no default is given).

    .. note::
        This function intentionally shadows :data:`typing.Optional`.  The
        name choice is deliberate and consistent with the ``Depends``,
        ``Payload``, and ``Topic`` binding-marker family.  Import from
        ``cosalette.di`` (or ``cosalette``) to get the DI marker.

    Returns:
        A marker suitable for ``Annotated[T | None, Optional()]`` or
        ``Annotated[T, Optional()]``.

    Example::

        from typing import Annotated
        from cosalette.di import Optional
        from myapp.stores import DeviceStore

        @app.command("update_setpoint")
        async def handle_update(
            store: Annotated[DeviceStore | None, Optional()] = None,
        ) -> None:
            if store is None:
                return  # adapter not registered in this deployment
            ...

    """
    return _OptionalMarker()
