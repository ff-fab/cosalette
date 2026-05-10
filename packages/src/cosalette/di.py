"""Dependency injection markers for cosalette handlers.

Provides the :func:`Depends` factory for declaring dependency parameters
in handler functions via PEP 593 :class:`~typing.Annotated`.

See Also:
    ADR-046 — Typed Handler Contract Validation.
"""

from __future__ import annotations

import inspect
from typing import Any


class _DependsMarker:
    """PEP 593 Annotated metadata for handler dependency parameters.

    Created by :func:`Depends`.  The ``dependency`` callable is invoked
    at request time and its return value is injected into the parameter.
    """

    __slots__ = ("dependency",)

    def __init__(self, dependency: Any) -> None:
        self.dependency = dependency

    def __repr__(self) -> str:
        return f"Depends({self.dependency!r})"


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
        TypeError: If *dependency* is an async function — not supported
            in the first wave.

    Example::

        def get_device_id(topic: Annotated[str, Topic()]) -> str:
            return topic.split("/")[2]

        @app.command("devices/{device_id}/setpoint/set")
        async def handle_setpoint(
            device_id: Annotated[str, Depends(get_device_id)],
        ) -> ThermostatState:
            ...

    """
    if inspect.iscoroutinefunction(dependency) or inspect.isasyncgenfunction(
        dependency
    ):
        msg = (
            f"Async dependency functions are not supported in the first wave. "
            f"Use a synchronous callable for Depends(). Got: {dependency!r}"
        )
        raise TypeError(msg)
    return _DependsMarker(dependency)
