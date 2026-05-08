"""Stream mixin for the App class."""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from cosalette._adapter_lifecycle import _AdapterEntry
from cosalette._app._helpers import _check_no_port_in_signature
from cosalette._injection import build_injection_plan
from cosalette._registration import (
    EnabledSpec,
    _StreamRegistration,
    validate_mqtt_name,
    validate_stream_signature,
)
from cosalette._stream import BackpressurePolicy
from cosalette._utils import _callable_name, _callable_qualname

logger = logging.getLogger(__name__)


class _StreamMixin:
    """Mixin for stream-related App methods."""

    _streams: list[_StreamRegistration]
    _adapters: dict[type, _AdapterEntry]

    @abstractmethod
    def registered_names(self) -> frozenset[str]: ...

    def stream(
        self,
        name: str | None = None,
        *,
        enabled: EnabledSpec = True,
        maxsize: int = 0,
        backpressure: BackpressurePolicy = "drop_newest",
        summary: str | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> Callable[..., Any]:
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
            summary: One-line description of the stream handler for
                documentation.  Informational only.
            behavior: List of phrases describing what the handler does.
                Informational only.
            effects: List of side effects the handler produces.
                Informational only.

        Raises:
            TypeError: If the function lacks a ``Stream[T]`` parameter.
            TypeError: If ``Stream`` parameter is not parameterized.
            TypeError: If no ``StreamablePort[T]`` adapter is registered
                for the stream item type ``T``.
        """
        if callable(enabled):
            return self._make_deferred_stream_decorator(
                name,
                enabled,
                # Buffer and backpressure settings
                maxsize,
                backpressure,
                # Documentation fields
                summary,
                behavior,
                effects,
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
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
                behavior=behavior,
                effects=effects,
            )
            return func

        return decorator

    def _make_deferred_stream_decorator(
        self,
        name: str | None,
        enabled: EnabledSpec,
        maxsize: int,
        backpressure: BackpressurePolicy,
        summary: str | None,
        behavior: list[str] | None,
        effects: list[str] | None,
    ) -> Callable[..., Any]:
        """Create a deferred stream decorator for enabled=callable case."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            # Validate MQTT name and name uniqueness at decoration time,
            # mirroring _validate_periodic_early — adapter availability
            # is deferred to bootstrap (adapters may be registered later).
            resolved_name = name or _callable_name(func)
            validate_mqtt_name(resolved_name)
            if resolved_name in self.registered_names():
                msg = f"Name '{resolved_name}' is already registered"
                raise ValueError(msg)
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
                    behavior=behavior,
                    effects=effects,
                ),
            )
            return func

        return decorator

    def _validate_stream_signature(self, func: Callable[..., Any]) -> None:
        """Validate Stream[T] parameter signature without checking adapter availability.

        Adapter availability is deferred to startup/runtime (cos-s2q.4).
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
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> None:
        """Register a stream handler imperatively.

        Imperative equivalent of ``@app.stream``.  See
        :meth:`~App.stream` for full parameter documentation.
        """
        if not enabled:
            return

        self._validate_stream_signature(func)

        plan = build_injection_plan(func)
        resolved_name = name

        # Check name uniqueness before appending
        validate_mqtt_name(resolved_name)
        if resolved_name in self.registered_names():
            msg = f"Name '{resolved_name}' is already registered"
            raise ValueError(msg)

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
                behavior=behavior,
                effects=effects,
            ),
        )
