"""Configuration mixin for the App class."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cosalette._registration import build_reactor_registration
from cosalette._utils import _callable_qualname

if TYPE_CHECKING:
    from cosalette._persistence._state import StateRegistration
    from cosalette._registration import _ReactorRegistration

logger = logging.getLogger(__name__)


class _ConfigureMixin:
    """Mixin for configuration-related App methods."""

    _configure_hooks: list[Callable[..., Any]]
    _state_factories: list[StateRegistration]
    _reactors: list[_ReactorRegistration]

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
        self._entity_set_is_dynamic = True
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

    def react(
        self,
        state_type: type,
        *,
        drain: Callable[[Any], Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a reactor for domain events from a state object.

        The decorated function is called after framework-managed handler
        execution boundaries when the specified state has pending events.
        Events are injected by name only when a reactor declares an
        ``events`` parameter; reactors may omit it.

        Args:
            state_type: The state type registered via ``@app.state`` to
                watch for events.  Must already be registered.
            drain: Optional drain callable to invoke on the state instance.
                When ``None``, the framework looks for a ``drain_events()``
                method on the state instance.

        Returns:
            The decorated function, registered as a reactor.

        Raises:
            ValueError: If ``state_type`` is not registered via ``@app.state``.
            TypeError: If the decorated function is not async.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not inspect.iscoroutinefunction(func):
                msg = f"Reactor function {_callable_qualname(func)!r} must be async"
                raise TypeError(msg)

            # Validate that state_type is registered
            registered_types = {reg.state_type for reg in self._state_factories}
            if state_type not in registered_types:
                msg = (
                    f"State type {state_type.__qualname__!r} is not registered "
                    f"via @app.state. Register the state factory first."
                )
                raise ValueError(msg)

            return build_reactor_registration(func, state_type, drain, self._reactors)

        return decorator
