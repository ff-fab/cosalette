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
from typing import Any, NamedTuple

from cosalette._runners._notifier import EntityNotifier
from cosalette._settings import Settings
from cosalette._utils import _callable_qualname

_MAX_STATE_FACTORY_PARAMS = 2  # one per injectable type: Settings, EntityNotifier


class _FactoryVariant(Enum):
    """Factory variant detected from return annotation."""

    SYNC = "sync"
    CONTEXT_MANAGER = "context_manager"
    ASYNC_GEN = "async_gen"
    ASYNC_CM = "async_cm"


@dataclass(frozen=True, slots=True)
class StateRegistration:
    """Read-only descriptor for a registered ``@app.state`` factory."""

    state_type: type
    factory: Callable[..., Any]
    variant: _FactoryVariant
    settings_type: type
    has_settings_param: bool
    settings_param_name: str
    #: Name of the ``EntityNotifier`` parameter, when the factory
    #: declares one (ADR-064).  ``None`` when it does not.
    notifier_param_name: str | None = None


class _StateParams(NamedTuple):
    """Resolved injectable parameters of a ``@app.state`` factory."""

    has_settings_param: bool
    settings_param_name: str
    settings_type: type
    notifier_param_name: str | None


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


def _detect_params(
    qualname: str,
    type_hints: dict[str, Any],
    parameters: list[inspect.Parameter],
) -> _StateParams:
    """Resolve the injectable parameters of a state factory.

    A factory may declare at most one ``Settings`` (or subclass)
    parameter and at most one :class:`EntityNotifier` parameter, in
    either order.
    """
    if len(parameters) > _MAX_STATE_FACTORY_PARAMS:
        raise TypeError(
            f"Factory {qualname} must accept 0 to "
            f"{_MAX_STATE_FACTORY_PARAMS} (Settings, EntityNotifier) "
            f"parameters, got {len(parameters)}"
        )
    params = _StateParams(False, "", Settings, None)
    for param in parameters:
        annotation = type_hints.get(param.name)
        if annotation is EntityNotifier:
            _reject_duplicate(qualname, param.name, params.notifier_param_name)
            params = params._replace(notifier_param_name=param.name)
            continue
        settings_type = _as_settings_type(qualname, param.name, annotation)
        _reject_duplicate(qualname, param.name, params.settings_param_name or None)
        params = params._replace(
            has_settings_param=True,
            settings_param_name=param.name,
            settings_type=settings_type,
        )
    return params


def _reject_duplicate(qualname: str, param_name: str, existing: str | None) -> None:
    """Raise when a factory declares the same injectable type twice."""
    if existing is not None:
        raise TypeError(
            f"Parameter '{param_name}' of factory {qualname} duplicates "
            f"parameter '{existing}' — each injectable type may appear "
            "at most once"
        )


def _as_settings_type(qualname: str, param_name: str, annotation: Any) -> type:
    """Return *annotation* as a Settings subclass, or raise TypeError."""
    if annotation is None:
        raise TypeError(
            f"Parameter '{param_name}' of factory {qualname} must be "
            "annotated as Settings, a Settings subclass, or EntityNotifier"
        )
    try:
        if inspect.isclass(annotation) and issubclass(annotation, Settings):
            return annotation
        raise TypeError(
            f"Parameter '{param_name}' of factory {qualname} "
            f"is annotated with {annotation.__name__!r}, "
            "but only Settings or Settings subclasses are supported"
        )
    except TypeError as exc:
        raise TypeError(
            f"Parameter '{param_name}' of factory {qualname} "
            f"has unsupported annotation {annotation}. "
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
    params = _detect_params(qualname, type_hints, parameters)

    return StateRegistration(
        state_type=state_type,
        factory=factory,
        variant=variant,
        settings_type=params.settings_type,
        has_settings_param=params.has_settings_param,
        settings_param_name=params.settings_param_name,
        notifier_param_name=params.notifier_param_name,
    )
