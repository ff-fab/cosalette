"""@app.state factory registration for lifespan-scoped shared state.

Supports four factory variants detected by return annotation:
- ``def f(...) -> T`` — sync, no teardown
- ``def f(...) -> ContextManager[T]`` — sync context manager; also accepts
  ``Iterator[T]`` (the annotation produced by ``@contextlib.contextmanager``)
- ``async def f(...) -> AsyncIterator[T]`` — async generator with teardown
- ``async def f(...) -> AsyncContextManager[T]`` — async context manager

The return type T is used as the DI key.

See Also:
    ADR-039 — @app.state factory.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from typing import Any

from cosalette._settings import Settings
from cosalette._utils import _callable_qualname


class _FactoryVariant(Enum):
    """Factory variant detected from return annotation."""

    SYNC = "sync"
    CONTEXT_MANAGER = "context_manager"
    ASYNC_GEN = "async_gen"
    ASYNC_CM = "async_cm"


@dataclass(frozen=True, slots=True)
class StateRegistration:
    """Internal record of a registered @app.state factory."""

    state_type: type
    factory: Callable[..., Any]
    variant: _FactoryVariant
    settings_type: type
    has_settings_param: bool
    settings_param_name: str


def _detect_variant(
    qualname: str, return_annotation: Any
) -> tuple[_FactoryVariant, type]:
    """Return (variant, state_type) from a resolved return annotation."""
    origin = typing.get_origin(return_annotation)
    args = typing.get_args(return_annotation)

    if origin is None:
        if not inspect.isclass(return_annotation):
            raise TypeError(
                f"Factory {qualname} SYNC return annotation must be a concrete "
                f"class, got {return_annotation!r}. "
                "Supported forms: T, ContextManager[T], AsyncIterator[T], "
                "AsyncContextManager[T]"
            )
        return _FactoryVariant.SYNC, return_annotation

    if origin is AsyncIterator:
        if not args:
            raise TypeError(
                f"Factory {qualname} return type AsyncIterator must be "
                "parameterized: AsyncIterator[T]"
            )
        return _FactoryVariant.ASYNC_GEN, args[0]

    if origin is AbstractAsyncContextManager:
        if not args:
            raise TypeError(
                f"Factory {qualname} return type AsyncContextManager must be "
                "parameterized: AsyncContextManager[T]"
            )
        return _FactoryVariant.ASYNC_CM, args[0]

    if origin in (AbstractContextManager, Iterator, Generator):
        if not args:
            raise TypeError(
                f"Factory {qualname} return type ContextManager must be "
                "parameterized: ContextManager[T] or Iterator[T]"
            )
        return _FactoryVariant.CONTEXT_MANAGER, args[0]

    raise TypeError(
        f"Factory {qualname} return annotation {return_annotation} is not "
        "supported. Supported forms: T, ContextManager[T], AsyncIterator[T], "
        "AsyncContextManager[T]"
    )


def _detect_settings_param(
    qualname: str,
    type_hints: dict[str, Any],
    parameters: list[inspect.Parameter],
) -> tuple[bool, str, type]:
    """Return (has_settings_param, param_name, settings_type)."""
    if len(parameters) > 1:
        raise TypeError(
            f"Factory {qualname} must accept 0 or 1 (Settings) parameters, "
            f"got {len(parameters)}"
        )
    if not parameters:
        return False, "", Settings

    first_param = parameters[0]
    param_annotation = type_hints.get(first_param.name)

    if param_annotation is None:
        raise TypeError(
            f"Parameter '{first_param.name}' of factory {qualname} must be "
            "annotated as Settings or a Settings subclass"
        )

    try:
        if inspect.isclass(param_annotation) and issubclass(param_annotation, Settings):
            return True, first_param.name, param_annotation
        raise TypeError(
            f"Parameter '{first_param.name}' of factory {qualname} "
            f"is annotated with {param_annotation.__name__!r}, "
            "but only Settings or Settings subclasses are supported"
        )
    except TypeError as exc:
        raise TypeError(
            f"Parameter '{first_param.name}' of factory {qualname} "
            f"has unsupported annotation {param_annotation}. "
            "Only Settings or Settings subclasses are supported"
        ) from exc


def build_state_registration(
    factory: Callable[..., Any], registered_types: set[type]
) -> StateRegistration:
    """Build a StateRegistration from a factory function.

    Args:
        factory: The factory function decorated with @app.state.
        registered_types: Set of already-registered state types to check for duplicates.

    Returns:
        A StateRegistration instance.

    Raises:
        TypeError: If the factory has no return type annotation.
        TypeError: If the return annotation is not a supported form.
        TypeError: If the factory has a non-Settings parameter annotation.
        ValueError: If another factory already returns the same type.
    """
    qualname = _callable_qualname(factory)

    try:
        type_hints = typing.get_type_hints(factory)
    except Exception as exc:
        raise TypeError(f"Failed to resolve type hints for {qualname}: {exc}") from exc

    return_annotation = type_hints.get("return")
    if return_annotation is None:
        raise TypeError(
            f"Factory {qualname} must have a return type annotation. "
            "Supported forms: T, ContextManager[T], AsyncIterator[T], "
            "AsyncContextManager[T]"
        )

    variant, state_type = _detect_variant(qualname, return_annotation)

    if state_type in registered_types:
        raise ValueError(
            f"Duplicate @app.state for type {state_type.__name__!r}. "
            "Each state type may only have one factory."
        )

    parameters = list(inspect.signature(factory).parameters.values())
    has_settings_param, settings_param_name, settings_type = _detect_settings_param(
        qualname, type_hints, parameters
    )

    return StateRegistration(
        state_type=state_type,
        factory=factory,
        variant=variant,
        settings_type=settings_type,
        has_settings_param=has_settings_param,
        settings_param_name=settings_param_name,
    )
