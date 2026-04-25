"""@app.state factory registration for lifespan-scoped shared state.

Supports four factory variants detected by return annotation:
- ``def f(...) -> T`` — sync, no teardown
- ``def f(...) -> ContextManager[T]`` — sync context manager
- ``async def f(...) -> AsyncIterator[T]`` — async generator with teardown
- ``async def f(...) -> AsyncContextManager[T]`` — async context manager

The return type T is used as the DI key.

See Also:
    ADR-039 — @app.state factory.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import AsyncIterator, Callable
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

    # Get type hints
    try:
        type_hints = typing.get_type_hints(factory)
    except Exception as exc:
        raise TypeError(f"Failed to resolve type hints for {qualname}: {exc}") from exc

    # Check for return annotation
    return_annotation = type_hints.get("return")
    if return_annotation is None:
        raise TypeError(
            f"Factory {qualname} must have a return type annotation. "
            "Supported forms: T, ContextManager[T], AsyncIterator[T], "
            "AsyncContextManager[T]"
        )

    # Detect factory variant and extract state type
    origin = typing.get_origin(return_annotation)
    args = typing.get_args(return_annotation)

    variant: _FactoryVariant
    state_type: type

    if origin is None:
        # Simple type: def f() -> T
        variant = _FactoryVariant.SYNC
        state_type = return_annotation
    elif origin is AsyncIterator:
        # async def f() -> AsyncIterator[T]
        variant = _FactoryVariant.ASYNC_GEN
        if not args:
            raise TypeError(
                f"Factory {qualname} return type AsyncIterator must be "
                "parameterized: AsyncIterator[T]"
            )
        state_type = args[0]
    elif origin is AbstractAsyncContextManager:
        # async def f() -> AsyncContextManager[T]
        variant = _FactoryVariant.ASYNC_CM
        if not args:
            raise TypeError(
                f"Factory {qualname} return type AsyncContextManager must be "
                "parameterized: AsyncContextManager[T]"
            )
        state_type = args[0]
    elif origin is AbstractContextManager:
        # def f() -> ContextManager[T]
        variant = _FactoryVariant.CONTEXT_MANAGER
        if not args:
            raise TypeError(
                f"Factory {qualname} return type ContextManager must be "
                "parameterized: ContextManager[T]"
            )
        state_type = args[0]
    else:
        raise TypeError(
            f"Factory {qualname} return annotation {return_annotation} is not "
            "supported. Supported forms: T, ContextManager[T], AsyncIterator[T], "
            "AsyncContextManager[T]"
        )

    # Check for duplicate state type
    if state_type in registered_types:
        raise ValueError(
            f"Duplicate @app.state for type {state_type.__name__!r}. "
            "Each state type may only have one factory."
        )

    # Detect settings parameter
    signature = inspect.signature(factory)
    parameters = list(signature.parameters.values())

    has_settings_param = False
    settings_param_name = ""
    settings_type = Settings

    # Check first parameter (excluding return)
    if parameters:
        first_param = parameters[0]
        param_annotation = type_hints.get(first_param.name)

        if param_annotation is not None:
            # Check if it's Settings or a subclass
            try:
                if inspect.isclass(param_annotation) and issubclass(
                    param_annotation, Settings
                ):
                    has_settings_param = True
                    settings_param_name = first_param.name
                    settings_type = param_annotation
                else:
                    raise TypeError(
                        f"Parameter '{first_param.name}' of factory {qualname} "
                        f"is annotated with {param_annotation.__name__!r}, "
                        "but only Settings or Settings subclasses are supported"
                    )
            except TypeError as exc:
                # issubclass can raise TypeError for non-class types
                raise TypeError(
                    f"Parameter '{first_param.name}' of factory {qualname} "
                    f"has unsupported annotation {param_annotation}. "
                    "Only Settings or Settings subclasses are supported"
                ) from exc

    return StateRegistration(
        state_type=state_type,
        factory=factory,
        variant=variant,
        settings_type=settings_type,
        has_settings_param=has_settings_param,
        settings_param_name=settings_param_name,
    )
