"""Stream mixin for the App class."""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

from cosalette._app._helpers import _check_no_port_in_signature
from cosalette._app._telemetry_validators import validate_timeout
from cosalette._injection import build_injection_plan
from cosalette._registration import (
    EnabledSpec,
    TimeoutSpec,
    _StreamRegistration,
    validate_mqtt_name,
    validate_stream_signature,
)
from cosalette._runners._stream_types import BackpressurePolicy
from cosalette._utils import _callable_name, _callable_qualname
from cosalette._wiring._adapter_lifecycle import _AdapterEntry

logger = logging.getLogger(__name__)


def validate_stream_health(
    name: str,
    *,
    stale_after: TimeoutSpec | None,
    feeds: Sequence[str],
    is_root: bool,
) -> tuple[str, ...]:
    """Validate a stream's ``stale_after=`` and ``feeds=`` at decoration time.

    Checks what is knowable before bootstrap: a concrete ``stale_after``
    must be a finite positive number, ``feeds`` must be a sequence of
    names (a bare ``str`` is rejected — it is itself a sequence), and a
    root stream may not declare ``feeds``.  Whether the fed names exist
    is checked at bootstrap, after every registration is known.

    Args:
        name: Resolved stream name, for error messages.
        stale_after: The ``stale_after=`` value.
        feeds: The ``feeds=`` value.
        is_root: Whether the stream is unnamed (root).

    Returns:
        *feeds* normalised to a tuple.

    Raises:
        ValueError: On an invalid ``stale_after`` or ``feeds`` value.
        TypeError: If *feeds* is a ``str``.
    """
    validate_timeout(stale_after, "stale_after")
    if isinstance(feeds, str):
        msg = (
            f"Stream {name!r}: feeds= must be a sequence of entity names, "
            f"not a str; use feeds=[{feeds!r}]"
        )
        raise TypeError(msg)
    fed = tuple(feeds)
    if fed and is_root:
        msg = (
            f"Root stream {name!r} cannot declare feeds=: a root stream has no "
            "availability topic of its own, so it cannot drive the availability "
            "of other entities. Give the stream a name."
        )
        raise ValueError(msg)
    return fed


class _StreamMixin:
    """Mixin for stream-related App methods."""

    _streams: list[_StreamRegistration]
    _adapters: dict[type, _AdapterEntry]

    @property
    @abstractmethod
    def registered_names(self) -> frozenset[str]: ...

    def stream[**P, R](
        self,
        name: str | None = None,
        *,
        enabled: EnabledSpec = True,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        summary: str | None = None,
        state_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
        stale_after: TimeoutSpec | None = None,
        feeds: Sequence[str] = (),
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Register a streaming handler for push-to-pull data bridging.

        The decorated function processes items from a ``Stream[T]`` parameter
        via ``async for`` iteration.  The framework requires a corresponding
        ``StreamablePort[T]`` adapter for the same item type ``T``.

        Args:
            name: Device name for MQTT topics and logging.  When
                ``None``, the function name is used internally and
                topics omit the device segment.
            enabled: When ``False``, registration is silently skipped.
                When a callable ``(Settings) -> bool``, the decision
                is deferred to the bootstrap phase after settings
                resolution.  Defaults to ``True``.
            maxsize: Maximum number of items buffered in the internal
                :class:`Stream` queue.  ``0`` (default) means unbounded.
                Use a positive integer to cap memory use on constrained
                IoT devices.
            backpressure: Policy applied when ``maxsize > 0`` and the
                queue is full.  Defaults to ``"drop_newest"`` — the
                incoming item is silently discarded, keeping the queue
                at capacity without blocking the producer.  Other
                options: ``"drop_oldest"`` (evict the oldest item to
                make room) and ``"raise"`` (raise
                :exc:`asyncio.QueueFull`).  Note that :class:`Stream`
                itself defaults to ``"raise"``; the ``@app.stream``
                default of ``"drop_newest"`` is the safer choice for
                IoT producers.
            summary: One-line description of the stream handler.
                Surfaced in the registry snapshot
                (:func:`~cosalette.build_registry_snapshot`,
                :func:`~cosalette.format_registry_table`, and the
                ``cosalette_inspect_app`` MCP tool).
            state_model: Declared contract for the static retained
                ``{prefix}/{stream}/state`` topic.  **Runtime
                load-bearing**: every ``ctx.publish_state()`` payload from
                this handler is validated and normalised against the model,
                raising :exc:`~cosalette.ReturnValidationError` on a
                mismatch.  Omit it (default ``None``) to publish unvalidated
                dicts exactly as before.  Stream handlers are async
                generators yielding ``None``, so there is no return
                annotation to fall back on — this is the only contract
                source (ADR-046, ADR-045 amendment).
            behavior: List of phrases describing what the handler does.
                Surfaced in the registry snapshot.
            effects: List of side effects the handler produces.
                Surfaced in the registry snapshot.
            stale_after: Opt-in freshness bound in seconds (or a
                ``(Settings) -> float`` callable resolved at bootstrap).
                Every yielded item counts as a success; when no item
                arrives within the bound, the heartbeat reports the stream
                ``stale`` and a named stream publishes ``offline`` to its
                ``{prefix}/{stream}/availability`` (source ``freshness``).
                The next item restores it.  The bound also feeds
                ``exit_after_stale=`` and the health file.  ``None``
                (default) disables freshness tracking — nothing is derived.
                On a root stream the bound is heartbeat-only.
            feeds: Names of devices or telemetry entities whose
                availability follows this stream.  While the stream is
                offline for any reason, each fed entity is held
                ``offline`` under the ``stream:{name}`` source; the
                entity's own sources stay independent.  Validated at
                bootstrap: unknown names and root entities are rejected.
                Not allowed on a root stream.

        Raises:
            TypeError: If the function lacks a ``Stream[T]`` parameter.
            TypeError: If ``Stream`` parameter is not parameterized.
            TypeError: If no ``StreamablePort[T]`` adapter is registered
                for the stream item type ``T``.
            TypeError: If *feeds* is a bare ``str``.
            ValueError: If *stale_after* is not a finite positive number,
                or a root stream declares *feeds*.

        Note:
            A named stream owns a retained
            ``{prefix}/{stream}/availability`` topic (ADR-081 amendment):
            it goes ``offline`` when the handler crashes, on
            ``ctx.mark_unavailable()``, or when ``stale_after`` lapses, and
            back ``online`` on the next item.  A root stream is
            heartbeat-only and never touches the app-wide
            ``{prefix}/availability``.  Stream ``state`` topics appear in
            the generated AsyncAPI document like any other entity.
        """
        if callable(enabled):
            return self._make_deferred_stream_decorator(
                name,
                enabled,
                # Buffer and backpressure settings
                maxsize,
                backpressure,
                # Contract metadata
                summary,
                state_model,
                behavior,
                effects,
                stale_after,
                feeds,
            )

        def decorator(func: Callable[P, R]) -> Callable[P, R]:
            if not enabled:
                return func

            # Validate Stream[T] parameter at registration time
            self._validate_stream_signature(func)

            effective_name = name if name is not None else _callable_name(func)
            self.add_stream(
                effective_name,
                func,
                enabled=enabled,
                is_root=name is None,
                maxsize=maxsize,
                backpressure=backpressure,
                summary=summary,
                state_model=state_model,
                behavior=behavior,
                effects=effects,
                stale_after=stale_after,
                feeds=feeds,
            )
            return func

        return decorator

    def _make_deferred_stream_decorator[**P, R](
        self,
        name: str | None,
        enabled: EnabledSpec,
        maxsize: int,
        backpressure: BackpressurePolicy,
        summary: str | None,
        state_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        stale_after: TimeoutSpec | None,
        feeds: Sequence[str],
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Create a deferred stream decorator for enabled=callable case."""

        def decorator(func: Callable[P, R]) -> Callable[P, R]:
            # Validate MQTT name and name uniqueness at decoration time,
            # mirroring _validate_periodic_early — adapter availability
            # is deferred to bootstrap (adapters may be registered later).
            resolved_name = name or _callable_name(func)
            validate_mqtt_name(resolved_name)
            if resolved_name in self.registered_names:
                msg = f"Name '{resolved_name}' is already registered"
                raise ValueError(msg)
            fed = validate_stream_health(
                resolved_name,
                stale_after=stale_after,
                feeds=feeds,
                is_root=name is None,
            )
            plan = build_injection_plan(func)
            self._streams.append(
                _StreamRegistration(
                    name=resolved_name,
                    func=func,
                    injection_plan=plan,
                    enabled_spec=enabled,
                    is_root=name is None,
                    maxsize=maxsize,
                    backpressure=backpressure,
                    summary=summary,
                    state_model=state_model,
                    behavior=behavior,
                    effects=effects,
                    stale_after=stale_after,
                    feeds=fed,
                ),
            )
            return func

        return decorator

    def _validate_stream_signature(self, func: Callable[..., Any]) -> None:
        """Validate Stream[T] parameter signature without checking adapter availability.

        Adapter availability is deferred to startup/runtime.
        """
        stream_params, hints = validate_stream_signature(func)

        if len(stream_params) > 1:
            param_names = [name for name, _ in stream_params]
            msg = (
                f"Function {_callable_qualname(func)} declares multiple"
                f" Stream parameters: {param_names}."
                " Only one Stream[T] parameter is supported."
            )
            raise TypeError(msg)

        stream_param, item_type = stream_params[0]
        _check_no_port_in_signature(func, hints, item_type)

    def add_stream(
        self,
        name: str,
        func: Callable[..., Any],
        *,
        enabled: bool = True,
        is_root: bool = False,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        summary: str | None = None,
        state_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
        stale_after: TimeoutSpec | None = None,
        feeds: Sequence[str] = (),
    ) -> None:
        """Register a stream handler imperatively.

        Imperative equivalent of ``@app.stream``.  See
        :meth:`~App.stream` for full parameter documentation.

        Args:
            name: Stream name used as the MQTT device segment in topics.
            func: Async generator or async iterable handler function.
            enabled: When ``False``, the handler is not registered.
            is_root: When ``True``, the stream's MQTT topics omit the
                device-name segment — equivalent to calling ``@app.stream``
                without a name argument.  Set automatically when the
                decorator is used without a name; rarely needed explicitly.
            maxsize: Maximum queue depth (0 = unbounded).
            backpressure: Policy when the queue is full.
            summary: Short description surfaced in the registry snapshot.
            state_model: Declared state contract; validates every
                ``ctx.publish_state()`` payload from this handler.
            behavior: Behaviour tags surfaced in the registry snapshot.
            effects: Side-effect tags surfaced in the registry snapshot.
            stale_after: Seconds without a yielded item before the stream
                is reported stale and offline (see ``App.stream``).
            feeds: Entity names whose availability follows this stream's
                (see ``App.stream``).
        """
        if not enabled:
            return

        self._validate_stream_signature(func)

        plan = build_injection_plan(func)
        resolved_name = name

        # Check name uniqueness before appending
        validate_mqtt_name(resolved_name)
        if resolved_name in self.registered_names:
            msg = f"Name '{resolved_name}' is already registered"
            raise ValueError(msg)
        fed = validate_stream_health(
            resolved_name, stale_after=stale_after, feeds=feeds, is_root=is_root
        )

        self._streams.append(
            _StreamRegistration(
                name=resolved_name,
                func=func,
                injection_plan=plan,
                enabled_spec=enabled,
                is_root=is_root,
                maxsize=maxsize,
                backpressure=backpressure,
                summary=summary,
                state_model=state_model,
                behavior=behavior,
                effects=effects,
                stale_after=stale_after,
                feeds=fed,
            ),
        )
