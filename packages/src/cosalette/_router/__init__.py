"""Public Router class for composition-based cosalette applications.

The Router provides the same MQTT-native decorator surface as App but defers
registration until include_router() call time. This enables multi-module
composition without circular import dependencies.

See Also:
    ADR-044 — Public Router and composition API.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from cosalette._injection import build_injection_plan
from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _ReactorRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    build_reactor_registration,
    process_adapters_dict,
    validate_mqtt_name,
)
from cosalette._registration_views import _RegistrationViewsMixin
from cosalette._router._command import _RouterCommandMixin
from cosalette._router._device import _RouterDeviceMixin
from cosalette._router._periodic import _RouterPeriodicMixin
from cosalette._router._stream import _RouterStreamMixin
from cosalette._router._telemetry import _RouterTelemetryMixin
from cosalette._runners._periodic import _PeriodicRegistration
from cosalette._utils import _callable_qualname
from cosalette._wiring._adapter_lifecycle import _AdapterEntry

logger = logging.getLogger(__name__)


class Router(
    _RegistrationViewsMixin,
    _RouterDeviceMixin,
    _RouterCommandMixin,
    _RouterTelemetryMixin,
    _RouterStreamMixin,
    _RouterPeriodicMixin,
):
    """Composition primitive for multi-module cosalette applications.

    Router provides the same MQTT-native decorator surface as App
    (telemetry, command, device, stream, periodic, react) and defers
    registration until ``App.include_router()`` call time.

    See Also:
        ADR-044 — Public Router and composition API.

    Example::

        # sensors.py
        router = cosalette.Router(prefix="sensors", tags=["environment"])

        @router.telemetry("temperature", interval=30)
        async def read_temperature() -> dict:
            return {"celsius": await sensor.read()}

        # main.py
        app = cosalette.App("bridge")
        app.include_router(router)
        # → publishes to: bridge/sensors/temperature/state
    """

    def __init__(
        self,
        *,
        prefix: str | None = None,
        tags: list[str] | None = None,
        dependencies: list[Any] | None = None,
        adapters: dict[
            type,
            type
            | str
            | Callable[..., object]
            | tuple[
                type | str | Callable[..., object],
                type | str | Callable[..., object],
            ],
        ]
        | None = None,
    ) -> None:
        """Initialize a Router with optional shared metadata.

        Args:
            prefix: Single MQTT topic segment prepended to all operation
                names at include time.  Must not contain ``/``, ``+``,
                ``#``, or NUL.  When ``None``, no prefix is applied.
            tags: List of tags applied to all operations registered on
                this router.  Accumulates with include_router tags and
                operation-level tags.
            dependencies: Reserved for cos-ebc.  Must be ``None`` or empty.
            adapters: Adapter declarations in the same shape as
                ``App(adapters=...)``.  Merged into the app at include time.

        Raises:
            ValueError: If *prefix* contains MQTT special characters.
            NotImplementedError: If *dependencies* is not None or empty.
        """
        if prefix is not None:
            validate_mqtt_name(prefix)
        self._prefix = prefix
        self._tags = list(tags) if tags is not None else []

        if dependencies is not None and len(dependencies) > 0:
            msg = (
                "dependencies= is reserved for the cos-ebc epic "
                "and is not yet implemented. Pass None or omit the parameter."
            )
            raise NotImplementedError(msg)
        self._dependencies = dependencies

        self._devices: list[_DeviceRegistration] = []
        self._telemetry: list[_TelemetryRegistration] = []
        self._commands: list[_CommandRegistration] = []
        self._streams: list[_StreamRegistration] = []
        self._periodic: list[_PeriodicRegistration] = []
        self._reactors: list[_ReactorRegistration] = []

        self._adapters: dict[type, _AdapterEntry] = {}

        def _register(
            pt: type,
            impl: type | str | Callable[..., object],
            dry_run: type | str | Callable[..., object] | None,
        ) -> None:
            self._register_adapter(pt, impl, dry_run=dry_run)

        process_adapters_dict(adapters, _register)

    def _register_adapter(
        self,
        port_type: type,
        impl: type | str | Callable[..., object],
        *,
        dry_run: type | str | Callable[..., object] | None = None,
    ) -> None:
        """Register an adapter for a port type (internal).

        Args:
            port_type: The Protocol type to register.
            impl: The adapter class, lazy import string, or factory callable.
            dry_run: Optional dry-run variant.

        Raises:
            ValueError: If an adapter is already registered for this port type.
        """
        if port_type in self._adapters:
            msg = f"Adapter already registered for {port_type!r}"
            raise ValueError(msg)

        # Validate callable signatures at registration time
        for candidate in (impl, dry_run):
            if (
                candidate is not None
                and callable(candidate)
                and not isinstance(candidate, str)
            ):
                build_injection_plan(candidate)

        self._adapters[port_type] = _AdapterEntry(impl=impl, dry_run=dry_run)

    @property
    def registered_names(self) -> frozenset[str]:
        """All operation names registered on this router."""
        names: set[str] = set()
        for reg in self._devices:
            names.add(reg.name)
        for reg in self._telemetry:
            names.add(reg.name)
        for reg in self._commands:
            names.add(reg.name)
        for reg in self._streams:
            names.add(reg.name)
        for reg in self._periodic:
            names.add(reg.name)
        return frozenset(names)

    # -----------------------------------------------------------------------
    # React decorator
    # -----------------------------------------------------------------------

    def react(
        self,
        state_type: type,
        *,
        drain: Callable[[Any], Any] | None = None,
    ) -> Callable[..., Any]:
        """Register a reactor for domain events from a state object.

        Extends ``App.react`` with no additional router-specific parameters.
        Validation that ``state_type`` is registered via ``@app.state``
        is deferred until ``include_router()`` call time.

        Args:
            state_type: The state type registered via ``@app.state`` to
                watch for events.  Must be registered on the app when
                ``include_router`` is called.
            drain: Optional drain callable to invoke on the state instance.
                When ``None``, the framework looks for a ``drain_events()``
                method on the state instance.

        Returns:
            The decorated function, registered as a reactor.

        Raises:
            TypeError: If the decorated function is not async.

        Note:
            Unlike ``App.react``, this method defers validation of
            ``state_type`` registration until ``include_router`` time,
            as the router has no access to app state factories.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if not inspect.iscoroutinefunction(func):
                msg = f"Reactor function {_callable_qualname(func)!r} must be async"
                raise TypeError(msg)

            return build_reactor_registration(func, state_type, drain, self._reactors)

        return decorator

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _merge_tags(self, operation_tags: list[str] | None) -> list[str]:
        """Merge router constructor tags with operation-level tags.

        Order: Router constructor → operation decorator.
        Deduplicate while preserving first occurrence.
        """
        tags = list(self._tags)  # Router constructor tags
        if operation_tags is not None:
            for tag in operation_tags:
                if tag not in tags:
                    tags.append(tag)
        return tags

    def _name_to_kind(self, name: str) -> str:
        """Return the kind of registration for a given name."""
        # Table-driven lookup reduces cyclomatic complexity
        registries = [
            (self._devices, "device"),
            (self._telemetry, "telemetry"),
            (self._commands, "command"),
            (self._streams, "stream"),
            (self._periodic, "periodic"),
        ]
        for registry, kind in registries:
            if any(reg.name == name for reg in registry):
                return kind
        return "unknown"
