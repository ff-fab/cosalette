"""Configuration mixin for the App class."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cosalette._persistence._state import StateRegistration

logger = logging.getLogger(__name__)


class _ConfigureMixin:
    """Mixin for configuration-related App methods."""

    _configure_hooks: list[Callable[..., Any]]
    _state_factories: list[StateRegistration]

    def on_configure(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Register a configuration hook called before devices start.

        The hook runs after settings and adapters are resolved but
        before the run-loop.  Parameters are injected by type
        annotation (Settings, adapter ports, Logger, ClockPort).

        Use ``@app.on_configure`` (no parentheses).

        See Also:
            ADR-023 — on_configure lifecycle phase.
        """
        self._configure_hooks.append(func)
        return func

    def state(self, factory: Callable[..., Any]) -> Callable[..., Any]:
        """Register a lifespan-scoped shared-state factory.

        The factory runs once at bootstrap (after settings resolution, before
        adapters enter their lifecycle).  Its return value is registered in the
        DI container by type and injected into any handler that declares the
        corresponding type annotation.

        Factory forms supported (detected from return annotation):

        - ``def f(settings) -> T`` — sync, no teardown.
        - ``def f(settings) -> ContextManager[T]`` — sync context manager.
        - ``async def f(settings) -> AsyncIterator[T]`` — async generator;
          framework enters the generator and exits it on shutdown.
        - ``async def f(settings) -> AsyncContextManager[T]`` — async CM.

        Teardown runs in reverse registration order (LIFO).

        Args:
            factory: Callable returning the state object.  May optionally
                declare one parameter annotated with ``Settings`` or a
                concrete subclass — the framework passes the resolved
                settings as that type.  Zero-parameter factories are valid.

        Raises:
            TypeError: If the factory has no return type annotation.
            TypeError: If the return annotation is not a supported form.
            ValueError: If another factory already returns the same type.

        See Also:
            ADR-039 — @app.state factory.
        """
        from cosalette._persistence._state import build_state_registration

        registered_types = {reg.state_type for reg in self._state_factories}
        reg = build_state_registration(factory, registered_types)
        self._state_factories.append(reg)
        return factory
