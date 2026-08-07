"""Stream mixin for the Router class."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from cosalette._app._helpers import _check_no_port_in_signature
from cosalette._injection import build_injection_plan
from cosalette._registration import (
    EnabledSpec,
    _StreamRegistration,
    validate_stream_signature,
)
from cosalette._runners._stream_types import BackpressurePolicy
from cosalette._utils import _callable_name, _callable_qualname


class _RouterStreamMixin:
    """Mixin for stream-related Router methods."""

    _streams: list[_StreamRegistration]

    @property
    @abstractmethod
    def registered_names(self) -> frozenset[str]: ...

    @abstractmethod
    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]: ...

    @abstractmethod
    def _name_to_kind(self, name: str) -> str: ...

    def _build_stream_registration(
        self,
        func: Callable[..., Any],
        name: str | None,
        enabled: EnabledSpec,
        maxsize: int,
        backpressure: BackpressurePolicy,
        summary: str | None,
        state_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        tags: list[str] | None,
    ) -> Callable[..., Any]:
        """Build stream registration and return func unchanged.

        Shared helper for immediate and deferred stream decorator paths.
        """
        effective_name = name if name is not None else _callable_name(func)
        if effective_name in self.registered_names:
            msg = (
                f"Stream handler name {effective_name!r} already registered "
                f"as {self._name_to_kind(effective_name)}"
            )
            raise ValueError(msg)

        stream_params, hints = validate_stream_signature(func)
        if len(stream_params) > 1:
            param_names = [pname for pname, _ in stream_params]
            msg = (
                f"Function {_callable_qualname(func)!r} declares multiple "
                f"Stream parameters: {param_names!r}. Only one is supported."
            )
            raise TypeError(msg)
        _, item_type = stream_params[0]
        _check_no_port_in_signature(func, hints, item_type)

        # Stream adapter validation is deferred to App startup (cos-s2q.4)
        # Router only records the registration; no adapter check here.

        plan = build_injection_plan(func)
        is_root = effective_name == _callable_qualname(func)
        merged_tags = self._merge_tags(tags)

        reg = _StreamRegistration(
            name=effective_name,
            func=func,
            injection_plan=plan,
            enabled_spec=enabled,
            is_root=is_root,
            maxsize=maxsize,
            backpressure=backpressure,
            tags=tuple(merged_tags),
            summary=summary,
            state_model=state_model,
            behavior=behavior,
            effects=effects,
        )
        self._streams.append(reg)
        return func

    def stream(
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
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a streaming handler for push-to-pull data bridging.

        Extends ``App.stream`` with router-specific parameters
        (``tags``, ``dependencies``).

        Args:
            name: Device name for MQTT topics and logging.
            enabled: When ``False``, registration is skipped.
            maxsize: Maximum number of items buffered in the internal Stream queue.
            backpressure: Policy applied when maxsize > 0 and the queue is full.
            summary: One-line description surfaced in the registry snapshot.
            state_model: Declared state contract for the stream's static
                ``state`` topic.  Runtime load-bearing — validates every
                ``ctx.publish_state()`` payload (see ``App.stream``).
            behavior: Phrases describing what the handler does.
            effects: Side effects produced by the handler.
            tags: Additional tags for this stream.
            dependencies: Reserved for cos-ebc.  Must be None or empty.

        Returns:
            The decorated function, unchanged.

        Raises:
            TypeError: If the function lacks a Stream[T] parameter.
            NotImplementedError: If *dependencies* is not None or empty.
        """
        if dependencies is not None and len(dependencies) > 0:
            msg = (
                "dependencies= is reserved for the cos-ebc epic "
                "and is not yet implemented. Pass None or omit the parameter."
            )
            raise NotImplementedError(msg)

        if callable(enabled):

            def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                return self._build_stream_registration(
                    func,
                    name,
                    enabled,
                    maxsize,
                    backpressure,
                    summary,
                    state_model,
                    behavior,
                    effects,
                    tags,
                )

            return decorator

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not enabled:
                return func
            return self._build_stream_registration(
                func,
                name,
                enabled,
                maxsize,
                backpressure,
                summary,
                state_model,
                behavior,
                effects,
                tags,
            )

        return decorator
