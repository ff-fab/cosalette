"""Handlers whose annotations are deferred by PEP 563 (``from __future__``).

Injection diagnostics behave differently when annotations are strings: the
markers inside ``Annotated[...]`` are constructed by
:func:`typing.get_type_hints` / ``eval()`` at *registration* time rather than at
``def`` time, and ``eval()`` needs the declaring module's globals.  Neither
condition can be reproduced from a test module that defines its handlers
locally, so the affected handlers live here — in a module that genuinely
carries ``from __future__ import annotations``.

See ``packages/tests/unit/injection/test_injection.py`` for the tests.
"""

from __future__ import annotations

from typing import Annotated, Any

from cosalette._context import DeviceContext
from cosalette.di import Depends


class LocalPort:
    """Module-level port type, resolvable from this module's globals only."""

    def read(self) -> str:
        return "local"


async def async_dep() -> str:
    """Async dependency — must be rejected by ``Depends()``."""
    return "nope"


def sync_dep() -> str:
    """Plain sync dependency."""
    return "ok"


async def _async_value() -> str:
    """Coroutine function invoked (not awaited) by :func:`awaitable_dep`."""
    return "late"


def awaitable_dep() -> Any:
    """Sync callable that *returns* a coroutine — invisible to static checks."""
    return _async_value()


async def handler_with_async_depends(
    value: Annotated[str, Depends(async_dep)],
) -> None:
    """``Depends(async_dep)`` is only constructed when hints are resolved."""


async def handler_with_awaitable_depends(
    value: Annotated[str, Depends(awaitable_dep)],
) -> None:
    """The dependency is sync but hands back an un-awaited coroutine."""


async def handler_with_missing_type(
    port: DoesNotExistAnywhere,  # ty: ignore[unresolved-reference]  # noqa: F821
) -> None:
    """Genuinely unresolvable annotation — the missing-import case."""


async def handler_with_async_depends_and_missing_type(
    value: Annotated[str, Depends(async_dep)],
    port: AlsoDoesNotExist,  # ty: ignore[unresolved-reference]  # noqa: F821
) -> None:
    """Both failures at once.

    ``get_type_hints()`` gives up on the missing name, so the ``Depends``
    marker is instead constructed by the per-parameter ``eval()`` fallback.
    """


def self_recursive_dep(nested: Annotated[str, Depends(self_recursive_dep)]) -> str:
    """Dependency that depends on itself — a one-node cycle."""
    return nested


def cycle_dep_a(right: Annotated[str, Depends(cycle_dep_b)]) -> str:
    """First half of a two-node dependency cycle."""
    return right


def cycle_dep_b(left: Annotated[str, Depends(cycle_dep_a)]) -> str:
    """Second half of a two-node dependency cycle."""
    return left


class CallableDependency:
    """Callable instance whose ``__call__`` annotations need module globals."""

    def __call__(self, port: LocalPort) -> str:
        return port.read()


class CallableHandler:
    """Callable instance used directly as a handler."""

    def __call__(self, ctx: DeviceContext, port: LocalPort) -> str:
        return f"{ctx.name}:{port.read()}"


class AsyncCallableDependency:
    """Callable instance whose ``__call__`` is a coroutine function."""

    async def __call__(self) -> str:
        return "nope"


class AsyncGenCallableDependency:
    """Callable instance whose ``__call__`` is an async generator function."""

    async def __call__(self) -> Any:
        yield "nope"


class UnhashableDependency:
    """Callable instance that is unhashable (``__eq__`` without ``__hash__``)."""

    __hash__ = None  # type: ignore[assignment]

    def __call__(self) -> str:
        return "unhashable"
