"""Domain-event reactor dispatcher for state objects.

Handles draining events from registered state objects and invoking
matching reactors with dependency injection.

See Also:
    ADR-043 — P5 Domain-event reactors.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from cosalette._injection import resolve_kwargs

if TYPE_CHECKING:
    from cosalette._registration import _ReactorRegistration

logger = logging.getLogger(__name__)

_MAX_DRAIN_EVENTS = 100_000


async def dispatch_reactors(
    registrations: list[_ReactorRegistration],
    providers: Mapping[Any, Any],
) -> None:
    """Dispatch registered reactors for all state objects with pending events.

    Groups reactor registrations by event source (state_type, drain callable)
    to ensure multiple reactors for the same state receive the same events.
    Drains events once per group and dispatches all matching reactors.

    Args:
        registrations: List of reactor registrations to process.
        providers: DI provider map (same as used by other handlers).
    """
    if not registrations:
        return

    # Group registrations by event source to drain once per group.
    # Use drain identity rather than hashing the callable because the public
    # API accepts callable instances that may be unhashable or define custom
    # equality semantics.
    groups: dict[tuple[type, int | None], list[_ReactorRegistration]] = {}
    for registration in registrations:
        drain_key = None if registration.drain is None else id(registration.drain)
        key = (registration.state_type, drain_key)
        groups.setdefault(key, []).append(registration)

    # Process each group: drain once, dispatch all reactors with same events
    for group_registrations in groups.values():
        await _dispatch_reactor_group(group_registrations, providers)


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


async def _dispatch_reactor_group(
    group_registrations: list[_ReactorRegistration],
    providers: Mapping[Any, Any],
) -> None:
    """Dispatch a group of reactors that share the same event source."""
    if not group_registrations:
        return

    # All registrations in a group share the same state_type and drain
    representative = group_registrations[0]
    state_type = representative.state_type
    drain_callable = representative.drain

    # Get the state instance from providers (missing state is a wiring bug)
    state_instance = providers.get(state_type)
    if state_instance is None:
        type_name = state_type.__qualname__
        msg = (
            f"State type {type_name!r} not found in providers. "
            "This is a wiring bug - ensure state is registered via @app.state."
        )
        raise ValueError(msg)

    # Drain events from the state once for this group
    events = await _drain_events(state_instance, drain_callable)

    # Skip if no events
    if not events:
        return

    # Dispatch all reactors in the group with the same drained events
    for registration in group_registrations:
        await _dispatch_single_reactor_with_events(registration, providers, events)


async def _dispatch_single_reactor_with_events(
    registration: _ReactorRegistration,
    providers: Mapping[Any, Any],
    events: list[Any],
) -> None:
    """Dispatch a single reactor with pre-drained events."""
    # Build kwargs for the reactor function
    kwargs = resolve_kwargs(
        registration.injection_plan,
        cast(dict[type, Any], providers),
    )

    # Add the events parameter if declared (reserved parameter, injected by name)
    if registration.events_param:
        kwargs[registration.events_param] = events

    # Invoke reactor function (let exceptions propagate)
    result = registration.func(**kwargs)
    if inspect.iscoroutine(result):
        await result


async def _call_drain(
    state_instance: Any,
    drain_callable: Callable[[Any], Any] | None,
) -> Any:
    """Call the configured drain function for a state instance."""
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

    if inspect.iscoroutine(result):
        return await result

    return result


def _drain_result_type_error(result: Any) -> TypeError:
    """Build the standard drain result type error."""
    msg = (
        "Drain result must be None or iterable of events, "
        f"got {type(result).__qualname__!r}: {result!r}"
    )
    return TypeError(msg)


def _ensure_event_count(event_count: int, *, is_list: bool) -> None:
    """Reject drain results that exceed the configured event cap."""
    if event_count <= _MAX_DRAIN_EVENTS:
        return

    if is_list:
        msg = (
            f"Event list too large ({event_count} events). "
            f"Consider batching or pagination for memory safety."
        )
        raise ValueError(msg)

    msg = (
        f"Event iterable too large (>{_MAX_DRAIN_EVENTS} events). "
        f"Consider batching or pagination for memory safety."
    )
    raise ValueError(msg)


def _iter_events_with_limit(result: Any) -> list[Any]:
    """Materialize an iterable drain result with a safety cap."""
    events = []
    for event_count, event in enumerate(result, start=1):
        _ensure_event_count(event_count, is_list=False)
        events.append(event)
    return events


def _coerce_events(result: Any) -> list[Any]:
    """Normalize a drain result into a concrete event list."""
    if result is None:
        return []

    if isinstance(result, (str, bytes, bytearray)):
        raise _drain_result_type_error(result)

    if isinstance(result, list):
        _ensure_event_count(len(result), is_list=True)
        return result

    try:
        return _iter_events_with_limit(result)
    except TypeError as exc:
        raise _drain_result_type_error(result) from exc


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
        TypeError: If drain result is a non-iterable or text scalar.
        ValueError: If iterable result is too large for memory safety.
    """
    result = await _call_drain(state_instance, drain_callable)
    return _coerce_events(result)
