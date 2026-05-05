"""Domain-event reactor dispatcher for state objects.

Handles draining events from registered state objects and invoking
matching reactors with dependency injection.

See Also:
    ADR-043 — P5 Domain-event reactors.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from cosalette._injection import resolve_kwargs

if TYPE_CHECKING:
    from cosalette._registration import _ReactorRegistration

logger = logging.getLogger(__name__)


async def dispatch_reactors(
    registrations: list[_ReactorRegistration],
    providers: Mapping[Any, Any],
) -> None:
    """Dispatch registered reactors for all state objects with pending events.

    For each reactor registration:
    1. Get the state instance from providers
    2. Call the drain function (or state.drain_events() if not specified)
    3. If events are non-empty, resolve reactor parameters and call it

    Args:
        registrations: List of reactor registrations to process.
        providers: DI provider map (same as used by other handlers).
    """
    for registration in registrations:
        await _dispatch_single_reactor(registration, providers)


async def run_reactor_boundaries(
    async_iterable: Any,
    providers: Mapping[Any, Any],
    reactors: list[_ReactorRegistration] | None,
) -> None:
    """Run an async iterable, dispatching reactors at yield boundaries.

    Dispatches reactors after each yielded value and once after normal
    completion. Does not dispatch on cancellation or generator errors.

    Args:
        async_iterable: The async iterable or generator to iterate.
        providers: DI provider map for reactor dispatch.
        reactors: List of reactor registrations to dispatch at boundaries.
    """
    import asyncio

    try:
        async for _ in async_iterable:
            # Dispatch reactors after each yielded boundary
            if reactors:
                await dispatch_reactors(reactors, providers)
        # Dispatch reactors once at normal completion
        # This handles handlers that mutate before returning
        # but don't yield a final item
        if reactors:
            await dispatch_reactors(reactors, providers)
    except asyncio.CancelledError:
        # No reactor dispatch on cancellation
        raise
    except Exception:
        # No reactor dispatch on generator errors
        raise


async def _dispatch_single_reactor(
    registration: _ReactorRegistration,
    providers: Mapping[Any, Any],
) -> None:
    """Dispatch a single reactor registration."""
    # Get the state instance from providers (missing state is a wiring bug)
    state_instance = providers.get(registration.state_type)
    if state_instance is None:
        type_name = registration.state_type.__qualname__
        msg = (
            f"State type {type_name!r} not found in providers. "
            "This is a wiring bug - ensure state is registered via @app.state."
        )
        raise ValueError(msg)

    # Drain events from the state (let exceptions propagate)
    events = await _drain_events(state_instance, registration.drain)

    # Skip if no events
    if not events:
        return

    # Build kwargs for the reactor function with state instance included in providers
    enhanced_providers = {**providers, registration.state_type: state_instance}
    kwargs = resolve_kwargs(registration.injection_plan, enhanced_providers)

    # Add the events parameter if declared (reserved parameter, injected by name)
    if registration.events_param:
        kwargs[registration.events_param] = events

    # Invoke reactor function (let exceptions propagate)
    result = registration.func(**kwargs)
    if inspect.iscoroutine(result):
        await result


async def _drain_events(
    state_instance: Any,
    drain_callable: Callable[[Any], Any] | None,
) -> list[Any]:
    """Drain events from a state instance.

    Args:
        state_instance: The state object to drain events from.
        drain_callable: Optional custom drain function. When None,
            looks for a drain_events() method on the state instance.

    Returns:
        List of drained events (may be empty).

    Raises:
        AttributeError: If no drain method is found.
        TypeError: If drain result is a non-iterable scalar.
    """
    if drain_callable is not None:
        result = drain_callable(state_instance)
    elif hasattr(state_instance, "drain_events"):
        result = state_instance.drain_events()
    else:
        msg = (
            f"State instance {type(state_instance).__qualname__!r} has no "
            f"drain_events() method and no custom drain callable provided"
        )
        raise AttributeError(msg)

    # Handle async drain functions
    if inspect.iscoroutine(result):
        result = await result

    # Convert None to empty list
    if result is None:
        return []

    # Convert to list if iterable, otherwise raise TypeError for non-iterable scalars
    if not isinstance(result, list):
        try:
            return list(result)
        except TypeError as e:
            msg = (
                f"Drain result must be None or iterable, "
                f"got {type(result).__qualname__!r}: {result!r}"
            )
            raise TypeError(msg) from e

    return result
