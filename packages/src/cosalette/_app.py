"""Application orchestrator for cosalette IoT-to-MQTT bridges.

The :class:`App` class is the central composition root. It provides a
decorator-based API for registering devices and adapters, an optional
lifespan context manager for startup/shutdown, then orchestrates the
full application lifecycle via :meth:`run`.

Adapters that implement the async context manager protocol
(``__aenter__``/``__aexit__``) are auto-managed: the framework enters
them during bootstrap (before the user lifespan hook) and exits them
during teardown (after the lifespan exits), using an
:class:`~contextlib.AsyncExitStack` for LIFO ordering and exception
safety.

Typical usage::

    import cosalette

    app = cosalette.App(name="mybridge", version="0.1.0")

    @app.device("sensor")
    async def sensor(ctx: cosalette.DeviceContext) -> None:
        while not ctx.shutdown_requested:
            await ctx.publish_state({"value": read_sensor()})
            await ctx.sleep(10)

    # Handlers declare only the parameters they need (signature-based
    # injection).  Zero-arg handlers are valid too:
    @app.telemetry("temp", interval=30)
    async def temp() -> dict[str, object]:
        return {"celsius": 22.5}

    app.run()

See Also:
    ADR-001 — Framework architecture (IoC, composition root).
    ADR-010 — Device archetypes (device vs telemetry).
    ADR-006 — Hexagonal architecture (adapter registration).
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import inspect
import logging
import signal
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

_RegistryType = Literal["device", "telemetry", "command"]

from pydantic import ValidationError

from cosalette._clock import ClockPort, SystemClock
from cosalette._context import AppContext, DeviceContext, _import_string
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter, build_will_config
from cosalette._injection import build_injection_plan, build_providers, resolve_kwargs
from cosalette._logging import configure_logging
from cosalette._mqtt import MqttClient, MqttLifecycle, MqttMessageHandler, MqttPort
from cosalette._persist import PersistPolicy
from cosalette._registration import (
    LifespanFunc as LifespanFunc,
)
from cosalette._registration import (
    _AdapterEntry,
    _build_adapter_providers,
    _call_factory,
    _call_init,
    _CommandRegistration,
    _DeviceRegistration,
    _is_async_context_manager,
    _noop_lifespan,
    _TelemetryRegistration,
    _validate_init,
)
from cosalette._router import TopicRouter
from cosalette._settings import Settings
from cosalette._stores import DeviceStore, Store
from cosalette._strategies import PublishStrategy

logger = logging.getLogger(__name__)

_TICK_PRECISION = 1000  # milliseconds


def _to_ms(seconds: float) -> int:
    """Convert seconds to integer milliseconds for tick arithmetic.

    Positive intervals are clamped to a minimum of 1 ms so that
    scheduler ticks always advance in time.
    """
    if seconds <= 0:
        return 0
    ms = round(seconds * _TICK_PRECISION)
    return ms or 1


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class App:
    """Central composition root and application orchestrator.

    Collects device registrations, adapter mappings, and an optional
    lifespan context manager, then runs the full async lifecycle
    in :meth:`run`.

    See Also:
        ADR-001 — Framework architecture (IoC, composition root).
    """

    def __init__(
        self,
        name: str,
        version: str = "0.0.0",
        *,
        description: str = "IoT-to-MQTT bridge",
        settings_class: type[Settings] = Settings,
        dry_run: bool = False,
        heartbeat_interval: float | None = 60.0,
        lifespan: LifespanFunc | None = None,
        store: Store | None = None,
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
        """Initialise the application orchestrator.

        Args:
            name: Application name (used as MQTT topic prefix and client ID).
            version: Application version string.
            description: Short description for CLI help text.
            settings_class: Settings subclass to instantiate at startup.
            dry_run: When True, resolve dry-run adapter variants.
            heartbeat_interval: Seconds between periodic heartbeats
                published to ``{prefix}/status``.  Set to ``None`` to
                disable periodic heartbeats entirely.  Defaults to 60.
            lifespan: Async context manager for application startup
                and shutdown.  Code before ``yield`` runs before devices
                start; code after ``yield`` runs after devices stop.
                Receives an :class:`AppContext`.  When ``None``, a no-op
                default is used.
            store: Optional :class:`Store` backend for device persistence.
                When set, the framework creates a :class:`DeviceStore`
                per device and injects it into handlers that declare a
                ``DeviceStore`` parameter.
            adapters: Optional mapping of port types to adapter
                implementations.  Each key is a Protocol type; each
                value is either a single implementation (class,
                lazy-import string, or factory callable) or a
                ``(impl, dry_run)`` tuple.  Entries are registered via
                :meth:`adapter` and coexist with later imperative calls.
        """
        self._name = name
        self._version = version
        self._description = description
        self._settings_class = settings_class
        try:
            self._settings: Settings | None = settings_class()
        except ValidationError:
            self._settings = None
        self._dry_run = dry_run
        if heartbeat_interval is not None and heartbeat_interval <= 0:
            msg = f"heartbeat_interval must be positive, got {heartbeat_interval}"
            raise ValueError(msg)
        self._heartbeat_interval = heartbeat_interval
        self._lifespan: LifespanFunc = (
            lifespan if lifespan is not None else _noop_lifespan
        )
        self._devices: list[_DeviceRegistration] = []
        self._telemetry: list[_TelemetryRegistration] = []
        self._commands: list[_CommandRegistration] = []
        self._startup_hooks: list[Callable[[AppContext], Awaitable[None]]] = []
        self._shutdown_hooks: list[Callable[[AppContext], Awaitable[None]]] = []
        self._adapters: dict[type, _AdapterEntry] = {}
        self._command_init_results: dict[str, Any] = {}
        self._store = store
        self._command_stores: dict[str, DeviceStore] = {}

        if adapters is not None:
            for port_type, value in adapters.items():
                if isinstance(value, tuple):
                    if len(value) != 2:  # noqa: PLR2004
                        msg = (
                            f"adapters value for {port_type!r} must be an impl "
                            f"or (impl, dry_run) 2-tuple, got {len(value)}-tuple"
                        )
                        raise ValueError(msg)
                    impl, dry_run_impl = value
                    self.adapter(port_type, impl, dry_run=dry_run_impl)
                else:
                    self.adapter(port_type, value)

    @property
    def settings(self) -> Settings:
        """Application settings, instantiated at construction time.

        The instance is created eagerly in ``__init__`` from the
        ``settings_class`` parameter.  Environment variables and
        ``.env`` files are read at that point, so decorator arguments
        like ``interval=app.settings.poll_interval`` reflect the
        actual runtime configuration.

        The CLI entrypoint (:meth:`cli`) re-instantiates settings
        with ``--env-file`` support and passes the result to
        :meth:`_run_async`, which takes precedence over this
        instance.

        Raises:
            RuntimeError: If the settings class could not be
                instantiated at construction time (e.g. required
                fields with no defaults and no matching environment
                variables).  Use ``app.cli()`` with ``--env-file``
                instead.
        """
        if self._settings is None:
            msg = (
                "Settings could not be instantiated at construction time "
                "(missing required fields?). Ensure required environment "
                "variables are set, or use app.cli() with --env-file."
            )
            raise RuntimeError(msg)
        return self._settings

    # --- Registration decorators -------------------------------------------

    def device(
        self,
        name: str | None = None,
        *,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
    ) -> Callable[..., Any]:
        """Register a command & control device.

        The decorated function runs as a concurrent asyncio task.
        Parameters are injected based on type annotations — declare
        only what you need (e.g. ``ctx: DeviceContext``,
        ``settings: Settings``, ``logger: logging.Logger``).
        Zero-parameter handlers are valid.

        The framework subscribes to ``{name}/set`` and routes commands
        to the handler registered via ``ctx.on_command``.

        When *name* is ``None``, the function name is used internally
        and the device publishes to root-level topics (``{prefix}/state``
        instead of ``{prefix}/{device}/state``).

        Args:
            name: Device name for MQTT topics and logging.  When
                ``None``, the function name is used internally and
                topics omit the device segment.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                the handler by type.
            enabled: When ``False``, registration is silently skipped.
                The decorator returns the original function unmodified
                and no name slot is reserved.  Defaults to ``True``.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            TypeError: If any handler parameter lacks a type annotation.
        """
        if callable(name):
            raise TypeError("Use @app.device(), not @app.device (parentheses required)")

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if name is not None:
                # Named device — delegate to imperative method
                self.add_device(name, func, init=init, enabled=enabled)
            else:
                # Root device — inline (add_device doesn't support root)
                if not enabled:
                    return func
                resolved_name = func.__name__
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                self._check_device_name(resolved_name, registry_type="device", is_root=True)
                plan = build_injection_plan(func)
                self._devices.append(
                    _DeviceRegistration(
                        name=resolved_name,
                        func=func,
                        injection_plan=plan,
                        is_root=True,
                        init=init,
                        init_injection_plan=init_plan,
                    ),
                )
            return func

        return decorator

    def add_device(
        self,
        name: str,
        func: Callable[..., Awaitable[None]],
        *,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
    ) -> None:
        """Register a command & control device imperatively.

        This is the imperative counterpart to :meth:`device`.  It
        always creates a *named* (non-root) registration.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async callable that implements the device loop.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                *func* by type.
            enabled: When ``False``, registration is silently skipped
                — no entry in the registry and no name slot reserved.
                Defaults to ``True``.

        Raises:
            ValueError: If a device with this name is already registered.
            TypeError: If *init* is async or has un-annotated parameters.
            TypeError: If *func* has un-annotated parameters.

        See Also:
            :meth:`device` — decorator equivalent.
        """
        if not enabled:
            return
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        self._check_device_name(name, registry_type="device", is_root=False)
        plan = build_injection_plan(func)
        self._devices.append(
            _DeviceRegistration(
                name=name,
                func=func,
                injection_plan=plan,
                is_root=False,
                init=init,
                init_injection_plan=init_plan,
            ),
        )

    def command(
        self,
        name: str | None = None,
        *,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
    ) -> Callable[..., Any]:
        """Register a command handler for an MQTT device.

        The decorated function is called each time a command arrives
        on the ``{prefix}/{name}/set`` topic.  Parameters named
        ``topic`` and ``payload`` receive the MQTT message values;
        all other parameters are injected by type annotation, exactly
        like ``@app.device`` and ``@app.telemetry`` handlers.

        If the handler returns a ``dict``, the framework publishes it
        as device state via ``publish_state()``.  Return ``None`` to
        skip auto-publishing.

        When *name* is ``None``, the function name is used internally
        and the device publishes to root-level topics.

        Args:
            name: Device name used for MQTT topics and logging.  When
                ``None``, the function name is used internally and
                topics omit the device segment.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                the handler by type.
            enabled: When ``False``, registration is silently skipped.
                The decorator returns the original function unmodified
                and no name slot is reserved.  Defaults to ``True``.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            TypeError: If any handler parameter lacks a type annotation.
        """
        if callable(name):
            raise TypeError(
                "Use @app.command(), not @app.command (parentheses required)"
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if name is not None:
                # Named command — delegate to imperative method
                self.add_command(name, func, init=init, enabled=enabled)
            else:
                # Root command — inline (add_command doesn't support root)
                if not enabled:
                    return func
                resolved_name = func.__name__
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                self._check_device_name(resolved_name, registry_type="command", is_root=True)
                plan = build_injection_plan(func, mqtt_params={"topic", "payload"})
                sig = inspect.signature(func)
                declared_mqtt = frozenset({"topic", "payload"} & sig.parameters.keys())
                self._commands.append(
                    _CommandRegistration(
                        name=resolved_name,
                        func=func,
                        injection_plan=plan,
                        mqtt_params=declared_mqtt,
                        is_root=True,
                        init=init,
                        init_injection_plan=init_plan,
                    ),
                )
            return func

        return decorator

    def add_command(
        self,
        name: str,
        func: Callable[..., Awaitable[dict[str, object] | None]],
        *,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
    ) -> None:
        """Register a command handler imperatively.

        This is the imperative counterpart to :meth:`command`.  It
        always creates a *named* (non-root) registration.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async callable invoked on each incoming command.
                Parameters named ``topic`` and ``payload`` receive the
                MQTT message values; others are injected by type.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                *func* by type.
            enabled: When ``False``, registration is silently skipped
                — no entry in the registry and no name slot reserved.
                Defaults to ``True``.

        Raises:
            ValueError: If a device with this name is already registered.
            TypeError: If *init* is async or has un-annotated parameters.
            TypeError: If *func* has un-annotated parameters.

        See Also:
            :meth:`command` — decorator equivalent.
        """
        if not enabled:
            return
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        self._check_device_name(name, registry_type="command", is_root=False)
        plan = build_injection_plan(func, mqtt_params={"topic", "payload"})
        sig = inspect.signature(func)
        declared_mqtt = frozenset({"topic", "payload"} & sig.parameters.keys())
        self._commands.append(
            _CommandRegistration(
                name=name,
                func=func,
                injection_plan=plan,
                mqtt_params=declared_mqtt,
                is_root=False,
                init=init,
                init_injection_plan=init_plan,
            ),
        )

    def telemetry(
        self,
        name: str | None = None,
        *,
        interval: float,
        publish: PublishStrategy | None = None,
        persist: PersistPolicy | None = None,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        group: str | None = None,
    ) -> Callable[..., Any]:
        """Register a telemetry device with periodic polling.

        The decorated function returns a ``dict`` published as JSON
        state, or ``None`` to suppress publishing for that cycle.
        Parameters are injected based on type annotations — declare
        only what you need.  Zero-parameter handlers are valid.

        The framework calls the handler at the specified interval
        and publishes the returned dict (unless suppressed by a
        ``None`` return or a publish strategy).

        When *name* is ``None``, the function name is used internally
        and the device publishes to root-level topics.

        Args:
            name: Device name for MQTT topics and logging.  When
                ``None``, the function name is used internally and
                topics omit the device segment.
            interval: Polling interval in seconds.
            publish: Optional publish strategy controlling when
                readings are actually published (e.g. ``OnChange()``,
                ``Every(seconds=60)``).  When ``None``, every reading
                is published unconditionally.
            persist: Optional save policy controlling when the
                :class:`DeviceStore` is persisted (e.g.
                ``SaveOnPublish()``, ``SaveOnChange()``).  Requires
                ``store=`` on the :class:`App`.  When ``None``, the
                store is saved only on shutdown (the safety net).
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                the handler by type.
            enabled: When ``False``, registration is silently skipped.
                The decorator returns the original function unmodified
                and no name slot is reserved.  Defaults to ``True``.
            group: Optional coalescing group name.  Telemetry devices
                in the same group share a single scheduler tick so
                their readings are published together.  When ``None``
                (the default), the device runs on its own independent
                timer.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            ValueError: If interval <= 0.
            ValueError: If ``persist`` is set but no ``store=`` backend
                was configured on the App.
            ValueError: If *group* is an empty string.
            TypeError: If any handler parameter lacks a type annotation.
        """
        # Skip all validation when disabled — a disabled device shouldn't raise.
        if enabled and group is not None and group == "":
            msg = "group must be non-empty"
            raise ValueError(msg)

        # Eagerly validate persist/store at decoration time
        # (add_telemetry re-checks for the imperative path).
        # Skip when disabled — a disabled device shouldn't raise.
        if enabled and persist is not None and self._store is None:
            msg = (
                "persist= requires a store= backend on the App. "
                "Pass store=MemoryStore() (or another Store) to App()."
            )
            raise ValueError(msg)

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if name is not None:
                # Named telemetry — delegate to imperative method
                self.add_telemetry(
                    name,
                    func,
                    interval=interval,
                    publish=publish,
                    persist=persist,
                    init=init,
                    enabled=enabled,
                    group=group,
                )
            else:
                # Root telemetry — inline (add_telemetry doesn't support root)
                if not enabled:
                    return func
                resolved_name = func.__name__
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                if interval <= 0:
                    msg = f"Telemetry interval must be positive, got {interval}"
                    raise ValueError(msg)
                self._check_device_name(resolved_name, registry_type="telemetry", is_root=True)
                plan = build_injection_plan(func)
                self._telemetry.append(
                    _TelemetryRegistration(
                        name=resolved_name,
                        func=func,
                        injection_plan=plan,
                        interval=interval,
                        is_root=True,
                        publish_strategy=publish,
                        persist_policy=persist,
                        init=init,
                        init_injection_plan=init_plan,
                        group=group,
                    ),
                )
            return func

        return decorator

    def add_telemetry(
        self,
        name: str,
        func: Callable[..., Awaitable[dict[str, object] | None]],
        *,
        interval: float,
        publish: PublishStrategy | None = None,
        persist: PersistPolicy | None = None,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        group: str | None = None,
    ) -> None:
        """Register a telemetry device imperatively.

        This is the imperative counterpart to :meth:`telemetry`.  It
        always creates a *named* (non-root) registration.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async callable returning a ``dict`` (published as
                state) or ``None`` (suppresses that cycle).
            interval: Polling interval in seconds.  Must be positive.
            publish: Optional publish strategy (e.g. ``OnChange()``)
                controlling when readings are actually published.
            persist: Optional save policy.  Requires ``store=`` on the
                :class:`App`.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                *func* by type.
            enabled: When ``False``, registration is silently skipped
                — no entry in the registry and no name slot reserved.
                Defaults to ``True``.
            group: Optional coalescing group name.  Telemetry devices
                in the same group share a single scheduler tick so
                their readings are published together.  When ``None``
                (the default), the device runs on its own independent
                timer.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If *interval* is zero or negative.
            ValueError: If *persist* is set but no ``store=`` backend
                was configured on the App.
            ValueError: If *group* is an empty string.
            TypeError: If *init* is async or has un-annotated parameters.
            TypeError: If *func* has un-annotated parameters.

        See Also:
            :meth:`telemetry` — decorator equivalent.
        """
        if not enabled:
            return
        if group is not None and group == "":
            msg = "group must be non-empty"
            raise ValueError(msg)
        if persist is not None and self._store is None:
            msg = (
                "persist= requires a store= backend on the App. "
                "Pass store=MemoryStore() (or another Store) to App()."
            )
            raise ValueError(msg)
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        if interval <= 0:
            msg = f"Telemetry interval must be positive, got {interval}"
            raise ValueError(msg)
        self._check_device_name(name, registry_type="telemetry", is_root=False)
        plan = build_injection_plan(func)
        self._telemetry.append(
            _TelemetryRegistration(
                name=name,
                func=func,
                injection_plan=plan,
                interval=interval,
                is_root=False,
                publish_strategy=publish,
                persist_policy=persist,
                init=init,
                init_injection_plan=init_plan,
                group=group,
            ),
        )

    def adapter(
        self,
        port_type: type,
        impl: type | str | Callable[..., object],
        *,
        dry_run: type | str | Callable[..., object] | None = None,
    ) -> None:
        """Register an adapter for a port type.

        All adapter forms support dependency injection: if a class
        ``__init__`` or factory callable declares a parameter
        annotated with ``Settings`` (or a subclass), the parsed
        settings instance is auto-injected at resolution time.

        Args:
            port_type: The Protocol type to register.
            impl: The adapter class, a ``module:ClassName`` lazy import
                string, or a factory callable returning an adapter instance.
            dry_run: Optional dry-run variant (class, lazy import string,
                or factory callable).

        Raises:
            ValueError: If an adapter is already registered for this port type.
            TypeError: If a callable (class or factory) has invalid
                signatures (e.g. un-annotated parameters or
                unresolvable types).
        """
        if port_type in self._adapters:
            msg = f"Adapter already registered for {port_type!r}"
            raise ValueError(msg)

        # Fail-fast: validate callable signatures at registration time
        # so errors surface here rather than at runtime resolution.
        # Classes are included — inspect.signature(cls) inspects __init__.
        for candidate in (impl, dry_run):
            if (
                candidate is not None
                and callable(candidate)
                and not isinstance(candidate, str)
            ):
                build_injection_plan(candidate)

        self._adapters[port_type] = _AdapterEntry(impl=impl, dry_run=dry_run)

    # --- Internal helpers --------------------------------------------------

    @property
    def _all_registrations(
        self,
    ) -> list[_DeviceRegistration | _TelemetryRegistration | _CommandRegistration]:
        """All device registrations across the three registries."""
        return [*self._devices, *self._telemetry, *self._commands]

    def _check_device_name(
        self, name: str, *, registry_type: _RegistryType, is_root: bool = False
    ) -> None:
        """Raise if name collides with an incompatible registration.

        Name sharing rules:
        - telemetry + command: ALLOWED (different MQTT suffixes)
        - All other cross-type combinations: REJECTED
        - Same-type duplicates: REJECTED

        When *is_root* is True, also enforces that at most one root
        (unnamed) device exists and logs a warning when root and named
        devices are mixed.

        Root and mixing checks are always global (all registrations)
        because they concern MQTT topic layout, not name scoping.
        """
        colliding_names = self._colliding_names(registry_type)
        self._validate_name_unique(name, colliding_names)

        # Root / mixing checks use ALL registrations (MQTT layout concern)
        all_names: set[str] = set()
        has_root = False
        for reg in self._all_registrations:
            all_names.add(reg.name)
            has_root = has_root or reg.is_root

        if is_root:
            self._validate_single_root(has_root)
        self._warn_if_mixing(is_root, has_root=has_root, has_named=bool(all_names))

    def _colliding_names(self, registry_type: _RegistryType) -> set[str]:
        """Return names that would collide with *registry_type*.

        Rules:
        - ``'device'`` collides with ALL other registrations
        - ``'telemetry'`` collides with devices + other telemetry (NOT commands)
        - ``'command'`` collides with devices + other commands (NOT telemetry)
        """
        names: set[str] = set()

        # Devices always collide with everything
        for reg in self._devices:
            names.add(reg.name)

        if registry_type == "device":
            # Devices collide with everything
            for reg in [*self._telemetry, *self._commands]:
                names.add(reg.name)
        elif registry_type == "telemetry":
            # Telemetry collides with devices (above) + other telemetry
            for reg in self._telemetry:
                names.add(reg.name)
        elif registry_type == "command":
            # Commands collide with devices (above) + other commands
            for reg in self._commands:
                names.add(reg.name)

        return names

    @staticmethod
    def _validate_name_unique(name: str, existing: set[str]) -> None:
        if name in existing:
            msg = f"Device name '{name}' is already registered"
            raise ValueError(msg)

    @staticmethod
    def _validate_single_root(has_root: bool) -> None:
        if has_root:
            msg = "Only one root device (unnamed) is allowed per app"
            raise ValueError(msg)

    @staticmethod
    def _warn_if_mixing(is_root: bool, *, has_root: bool, has_named: bool) -> None:
        """Log a warning when root and named devices coexist."""
        will_mix = (is_root and has_named) or (not is_root and has_root)
        if will_mix:
            logger.warning(
                "Mixing root (unnamed) and named devices may cause MQTT "
                "wildcard subscription issues — {prefix}/+/state won't "
                "match {prefix}/state"
            )

    def _resolve_adapters(self, settings: Settings) -> dict[type, object]:
        """Resolve all registered adapters to instances.

        When ``self._dry_run`` is True and an entry has a ``dry_run``
        variant, the dry-run implementation is used instead of the
        normal one.  String values are lazily imported via
        :func:`_import_string` before instantiation.

        All adapter forms — classes, import strings, and factory
        callables — are resolved via :func:`_call_factory` with
        signature-based injection.  If the callable (or class
        ``__init__``) declares a parameter annotated with
        ``Settings`` (or a subclass), the parsed settings instance
        is injected automatically.  Zero-arg constructors and
        callables remain backward compatible.

        Returned adapter instances that implement the async context
        manager protocol (``__aenter__``/``__aexit__``) will be
        auto-entered by the caller (:meth:`_run_async`) and exited
        during teardown.
        """
        providers = _build_adapter_providers(settings)
        resolved: dict[type, object] = {}
        for port_type, entry in self._adapters.items():
            raw_impl: type | str | Callable[..., object] = (
                entry.dry_run if (self._dry_run and entry.dry_run) else entry.impl
            )
            if isinstance(raw_impl, str):
                raw_impl = _import_string(raw_impl)
            # At this point raw_impl is a class or callable — both
            # accepted by _call_factory (classes are callable).
            assert callable(raw_impl)  # narrow for mypy
            resolved[port_type] = _call_factory(raw_impl, providers)
        return resolved

    # --- Device / telemetry runners ----------------------------------------

    async def _run_device(
        self,
        reg: _DeviceRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
    ) -> None:
        """Run a single device function with error isolation."""
        device_store: DeviceStore | None = None
        try:
            providers = build_providers(ctx, reg.name)

            # Create per-device store if app has a store backend
            if self._store is not None:
                device_store = DeviceStore(self._store, reg.name)
                device_store.load()
                providers[DeviceStore] = device_store

            if reg.init is not None:
                init_result = _call_init(reg.init, reg.init_injection_plan, providers)
                providers[type(init_result)] = init_result
            kwargs = resolve_kwargs(reg.injection_plan, providers)
            await reg.func(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Device '%s' crashed: %s", reg.name, exc)
            await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)
        finally:
            self._save_store_on_shutdown(device_store, reg.name)

    @staticmethod
    def _should_publish_telemetry(
        result: dict[str, object],
        last_published: dict[str, object] | None,
        strategy: PublishStrategy | None,
    ) -> bool:
        """Decide whether a telemetry reading should be published.

        First reading always goes through. Without a strategy, every
        reading is published. With a strategy, the decision is delegated.
        """
        if last_published is None:
            return True
        if strategy is None:
            return True
        return strategy.should_publish(result, last_published)

    @staticmethod
    def _maybe_persist(
        device_store: DeviceStore | None,
        persist_policy: PersistPolicy | None,
        did_publish: bool,
        device_name: str,
    ) -> None:
        """Save device store if the persist policy says to."""
        if device_store is None or persist_policy is None:
            return
        if not persist_policy.should_save(device_store, did_publish):
            return
        try:
            device_store.save()
        except Exception:
            logger.exception("Failed to save store for device '%s'", device_name)

    @staticmethod
    def _save_store_on_shutdown(
        device_store: DeviceStore | None, device_name: str
    ) -> None:
        """Unconditional store save for shutdown safety net."""
        if device_store is None:
            return
        try:
            device_store.save()
        except Exception:
            logger.exception("Failed to save store for device '%s'", device_name)

    def _prepare_telemetry_providers(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
    ) -> tuple[dict[type, object], DeviceStore | None]:
        """Build the DI provider map for a telemetry handler."""
        providers = build_providers(ctx, reg.name)
        device_store: DeviceStore | None = None
        if self._store is not None:
            device_store = DeviceStore(self._store, reg.name)
            device_store.load()
            providers[DeviceStore] = device_store
        return providers, device_store

    def _prepare_command_kwargs(
        self,
        reg: _CommandRegistration,
        ctx: DeviceContext,
        topic: str,
        payload: str,
    ) -> dict[str, Any]:
        """Build the resolved kwargs for a command handler."""
        providers = build_providers(ctx, reg.name)
        if reg.name in self._command_init_results:
            cached = self._command_init_results[reg.name]
            providers[type(cached)] = cached
        if reg.name in self._command_stores:
            providers[DeviceStore] = self._command_stores[reg.name]
        kwargs = resolve_kwargs(reg.injection_plan, providers)
        if "topic" in reg.mqtt_params:
            kwargs["topic"] = topic
        if "payload" in reg.mqtt_params:
            kwargs["payload"] = payload
        return kwargs

    async def _run_telemetry(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> None:
        """Run a telemetry polling loop with optional publish strategy.

        Strategy lifecycle (when ``reg.publish_strategy`` is set):

        1. ``_bind(clock)`` — inject the clock before the loop.
        2. First non-``None`` result is always published.
        3. Subsequent results gated by ``strategy.should_publish()``.
        4. ``strategy.on_published()`` called after each publish.
        """
        providers, device_store = self._prepare_telemetry_providers(reg, ctx)

        if reg.init is not None:
            try:
                init_result = _call_init(reg.init, reg.init_injection_plan, providers)
                providers[type(init_result)] = init_result
            except Exception as exc:
                await self._handle_telemetry_error(
                    reg,
                    exc,
                    None,
                    error_publisher,
                    health_reporter,
                )
                return  # cannot continue without init result
        kwargs = resolve_kwargs(reg.injection_plan, providers)
        strategy = reg.publish_strategy
        if strategy is not None:
            strategy._bind(ctx.clock)
        last_published: dict[str, object] | None = None
        last_error_type: type[Exception] | None = None
        try:
            while not ctx.shutdown_requested:
                try:
                    result = await reg.func(**kwargs)

                    # None return = suppress this cycle
                    if result is None:
                        self._maybe_persist(
                            device_store, reg.persist_policy, False, reg.name
                        )
                        await ctx.sleep(reg.interval)
                        continue

                    if self._should_publish_telemetry(result, last_published, strategy):
                        await ctx.publish_state(result)
                        last_published = result
                        did_publish = True
                        if strategy is not None:
                            strategy.on_published()
                    else:
                        did_publish = False

                    self._maybe_persist(
                        device_store, reg.persist_policy, did_publish, reg.name
                    )

                    last_error_type = self._clear_telemetry_error(
                        reg.name, last_error_type, health_reporter
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error_type = await self._handle_telemetry_error(
                        reg,
                        exc,
                        last_error_type,
                        error_publisher,
                        health_reporter,
                    )
                await ctx.sleep(reg.interval)
        finally:
            self._save_store_on_shutdown(device_store, reg.name)

    async def _run_telemetry_group(
        self,
        group_name: str,
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> None:
        """Run a coalescing-group scheduler for grouped telemetry handlers.

        Handlers in the same group are managed by a shared tick-aligned
        scheduler.  A priority queue (min-heap) of ``(fire_time_ms, index)``
        entries determines when each handler fires.  Handlers that share a
        fire time execute sequentially in a single batch — enabling adapter
        session sharing for resources like serial buses.

        Integer-millisecond tick arithmetic avoids floating-point
        accumulation errors (e.g. 300 s × 12 == 3600 s exactly).

        Per-handler semantics are preserved: each handler has its own
        ``DeviceContext``, ``PublishStrategy``, error state, persistence
        policy, and init function.
        """
        logger.debug(
            "Starting coalescing group '%s' with %d handler(s)",
            group_name,
            len(registrations),
        )

        # --- 1. INIT: prepare each handler ---
        init_result = await self._init_group_handlers(
            registrations, contexts, error_publisher, health_reporter
        )
        if init_result is None:
            return  # all handlers failed init

        (
            kwargs_arr,
            device_stores,
            strategies,
            last_published,
            last_error_type,
            intervals_ms,
            heap,
            sleep_ctx,
            epoch,
            active_stores,
        ) = init_result

        # --- 2. MAIN LOOP ---
        try:
            while not sleep_ctx.shutdown_requested and heap:
                # 2a. Peek at the next fire time
                next_fire_ms = heap[0][0]

                # 2b. Sleep until fire time
                elapsed = sleep_ctx.clock.now() - epoch
                wait_seconds = (next_fire_ms / _TICK_PRECISION) - elapsed
                if wait_seconds > 0:
                    await sleep_ctx.sleep(wait_seconds)
                    if sleep_ctx.shutdown_requested:
                        break

                # 2c. Pop all handlers due at this tick
                batch: list[int] = []
                while heap and heap[0][0] == next_fire_ms:
                    _, idx = heapq.heappop(heap)
                    batch.append(idx)

                # 2d. Execute batch sequentially (registration order)
                await self._process_group_handler_result(
                    batch,
                    registrations,
                    contexts,
                    kwargs_arr,
                    device_stores,
                    strategies,
                    last_published,
                    last_error_type,
                    error_publisher,
                    health_reporter,
                    sleep_ctx,
                )

                # 2e. Reschedule all handlers in the batch
                for idx in batch:
                    next_time = next_fire_ms + intervals_ms[idx]
                    heapq.heappush(heap, (next_time, idx))

        finally:
            # --- 3. CLEANUP: save all active device stores ---
            for store, name in active_stores:
                self._save_store_on_shutdown(store, name)

    async def _init_group_handlers(
        self,
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> (
        tuple[
            list[dict[str, Any]],
            list[DeviceStore | None],
            list[PublishStrategy | None],
            list[dict[str, object] | None],
            list[type[Exception] | None],
            list[int],
            list[tuple[int, int]],
            DeviceContext,
            float,
            list[tuple[DeviceStore | None, str]],
        ]
        | None
    ):
        """Initialise per-handler state for a coalescing-group scheduler.

        Prepares DI providers, calls init functions, binds publish
        strategies, and builds the priority-queue heap.

        Returns ``None`` when every handler fails its init — the caller
        should exit early.  Otherwise returns a tuple of:

        - ``kwargs_arr`` — resolved kwargs per handler
        - ``device_stores`` — per-handler persistence stores
        - ``strategies`` — per-handler publish strategies
        - ``last_published`` — per-handler last-published state
        - ``last_error_type`` — per-handler last error type
        - ``intervals_ms`` — per-handler interval in ms
        - ``heap`` — priority queue of ``(fire_time_ms, index)``
        - ``sleep_ctx`` — context for shutdown-aware sleep
        - ``epoch`` — reference timestamp
        - ``active_stores`` — ``(store, name)`` pairs for cleanup
        """
        n = len(registrations)

        # Per-handler state arrays
        providers_arr: list[dict[type, object]] = [{} for _ in range(n)]
        device_stores: list[DeviceStore | None] = [None] * n
        kwargs_arr: list[dict[str, Any]] = [{} for _ in range(n)]
        strategies: list[PublishStrategy | None] = [None] * n
        last_published: list[dict[str, object] | None] = [None] * n
        last_error_type: list[type[Exception] | None] = [None] * n
        intervals_ms: list[int] = [0] * n
        active: list[bool] = [False] * n

        for i, reg in enumerate(registrations):
            ctx = contexts[reg.name]
            providers_arr[i], device_stores[i] = self._prepare_telemetry_providers(
                reg, ctx
            )
            if reg.init is not None:
                try:
                    init_result = _call_init(
                        reg.init, reg.init_injection_plan, providers_arr[i]
                    )
                    providers_arr[i][type(init_result)] = init_result
                except Exception as exc:
                    await self._handle_telemetry_error(
                        reg, exc, None, error_publisher, health_reporter
                    )
                    continue  # exclude this handler

            kwargs_arr[i] = resolve_kwargs(reg.injection_plan, providers_arr[i])
            strategy = reg.publish_strategy
            strategies[i] = strategy
            if strategy is not None:
                strategy._bind(ctx.clock)
            intervals_ms[i] = _to_ms(reg.interval)
            active[i] = True

        # Build priority queue and active-stores list in a single pass
        heap: list[tuple[int, int]] = []
        active_stores: list[tuple[DeviceStore | None, str]] = []
        for i in range(n):
            if active[i]:
                heapq.heappush(heap, (0, i))
                active_stores.append((device_stores[i], registrations[i].name))

        if not heap:
            return None

        # First active handler's context for shutdown-aware sleep.
        # heap[0][1] is the lowest-index active handler.
        sleep_ctx = contexts[registrations[heap[0][1]].name]
        epoch = sleep_ctx.clock.now()

        return (
            kwargs_arr,
            device_stores,
            strategies,
            last_published,
            last_error_type,
            intervals_ms,
            heap,
            sleep_ctx,
            epoch,
            active_stores,
        )

    async def _process_group_handler_result(
        self,
        batch: list[int],
        registrations: list[_TelemetryRegistration],
        contexts: dict[str, DeviceContext],
        kwargs_arr: list[dict[str, Any]],
        device_stores: list[DeviceStore | None],
        strategies: list[PublishStrategy | None],
        last_published: list[dict[str, object] | None],
        last_error_type: list[type[Exception] | None],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
        sleep_ctx: DeviceContext,
    ) -> None:
        """Execute all handlers due at the current tick and process results.

        Iterates through the batch of handler indices, invoking each
        handler and processing its result: publishing state when the
        publish strategy allows, persisting stores according to policy,
        and clearing or recording error state.

        Respects ``sleep_ctx.shutdown_requested`` to skip remaining
        handlers when shutdown is in progress.
        """
        for idx in batch:
            if sleep_ctx.shutdown_requested:
                break
            reg = registrations[idx]
            ctx = contexts[reg.name]
            try:
                result = await reg.func(**kwargs_arr[idx])

                # None return → suppress this cycle, persist only
                if result is None:
                    self._maybe_persist(
                        device_stores[idx], reg.persist_policy, False, reg.name
                    )
                    continue

                # Decide whether to publish this reading
                should_publish = self._should_publish_telemetry(
                    result, last_published[idx], strategies[idx]
                )
                if should_publish:
                    await ctx.publish_state(result)
                    last_published[idx] = result
                    pub_strategy = strategies[idx]
                    if pub_strategy is not None:
                        pub_strategy.on_published()

                self._maybe_persist(
                    device_stores[idx],
                    reg.persist_policy,
                    should_publish,
                    reg.name,
                )
                last_error_type[idx] = self._clear_telemetry_error(
                    reg.name, last_error_type[idx], health_reporter
                )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error_type[idx] = await self._handle_telemetry_error(
                    reg,
                    exc,
                    last_error_type[idx],
                    error_publisher,
                    health_reporter,
                )

    @staticmethod
    def _clear_telemetry_error(
        name: str,
        last_error_type: type[Exception] | None,
        health_reporter: HealthReporter,
    ) -> type[Exception] | None:
        """Clear error state on successful telemetry poll."""
        if last_error_type is not None:
            logger.info("Telemetry '%s' recovered", name)
            health_reporter.set_device_status(name, "ok")
        return None

    @staticmethod
    async def _handle_telemetry_error(
        reg: _TelemetryRegistration,
        exc: Exception,
        last_error_type: type[Exception] | None,
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> type[Exception]:
        """Handle a telemetry polling error with deduplication."""
        if type(exc) is not last_error_type:
            logger.error("Telemetry '%s' error: %s", reg.name, exc)
            await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)
        health_reporter.set_device_status(reg.name, "error")
        return type(exc)

    async def _run_command(
        self,
        reg: _CommandRegistration,
        ctx: DeviceContext,
        topic: str,
        payload: str,
        error_publisher: ErrorPublisher,
    ) -> None:
        """Dispatch a single command to a @app.command handler."""
        try:
            kwargs = self._prepare_command_kwargs(reg, ctx, topic, payload)
            result = await reg.func(**kwargs)
            if result is not None:
                await ctx.publish_state(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Command handler '%s' error: %s", reg.name, exc)
            with contextlib.suppress(Exception):
                await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)
        finally:
            self._save_store_on_shutdown(self._command_stores.get(reg.name), reg.name)

    # --- Lifecycle ---------------------------------------------------------

    def run(
        self,
        *,
        mqtt: MqttPort | None = None,
        settings: Settings | None = None,
        shutdown_event: asyncio.Event | None = None,
        clock: ClockPort | None = None,
    ) -> None:
        """Start the application (blocking, synchronous entrypoint).

        Wraps :meth:`_run_async` in :func:`asyncio.run`, handling
        ``KeyboardInterrupt`` for clean Ctrl-C shutdown.  This is the
        recommended way to launch a cosalette application::

            app = cosalette.App(name="mybridge", version="0.1.0")
            app.run()

        All parameters are optional and intended for programmatic or
        test use — production apps typically call ``run()`` with no
        arguments.

        Args:
            mqtt: Override MQTT client (e.g. ``MockMqttClient`` for
                testing).  When ``None``, a real ``MqttClient`` is
                created from settings.
            settings: Override settings (skip env-file loading).
            shutdown_event: Override shutdown event (skip OS signal
                handlers).  Useful in tests to control shutdown timing.
            clock: Override clock (e.g. ``FakeClock`` for tests).

        See Also:
            :meth:`cli` — CLI entrypoint with Typer argument parsing.
        """
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                self._run_async(
                    mqtt=mqtt,
                    settings=settings,
                    shutdown_event=shutdown_event,
                    clock=clock,
                ),
            )

    def cli(self) -> None:
        """Start the application with CLI argument parsing.

        Builds a Typer CLI from the application's configuration,
        parses command-line arguments (``--dry-run``, ``--version``,
        ``--log-level``, ``--log-format``, ``--env-file``), and
        orchestrates the full async lifecycle.

        For production use without CLI parsing, prefer :meth:`run`.

        See Also:
            ADR-005 — CLI framework.
        """
        from cosalette._cli import build_cli

        cli = build_cli(self)
        cli(standalone_mode=True)

    async def _run_async(
        self,
        *,
        mqtt: MqttPort | None = None,
        settings: Settings | None = None,
        shutdown_event: asyncio.Event | None = None,
        clock: ClockPort | None = None,
    ) -> None:
        """Async orchestration — the heart of the framework.

        Orchestration order:

        1. Bootstrap infrastructure (settings, logging, adapters, MQTT).
        2. Register devices and wire command routing.
        3. Enter lifespan, start devices, block until shutdown.
        4. Tear down (cancel tasks, exit lifespan, health offline).

        Parameters are provided for testability — inject
        :class:`MockMqttClient`, :class:`FakeClock`, and a manual
        :class:`asyncio.Event` to avoid real I/O in tests.

        Args:
            mqtt: Override MQTT client (inject mock for tests).
            settings: Override settings (skip instantiation).
            shutdown_event: Override shutdown event (skip signal handlers).
            clock: Override clock (inject fake for tests).
        """
        # --- Phase 1: Bootstrap infrastructure ---
        resolved_settings = self._resolve_settings(settings)
        prefix = resolved_settings.mqtt.topic_prefix or self._name
        configure_logging(
            resolved_settings.logging,
            service=self._name,
            version=self._version,
        )

        resolved_adapters = self._resolve_adapters(resolved_settings)
        resolved_clock = clock if clock is not None else SystemClock()

        mqtt = self._create_mqtt(mqtt, resolved_settings, prefix)
        health_reporter, error_publisher = self._create_services(
            mqtt,
            prefix,
            resolved_clock,
        )

        if isinstance(mqtt, MqttLifecycle):
            await mqtt.start()

        # Install signal handlers before adapter entry so that
        # SIGTERM/SIGINT during a slow __aenter__ sets the shutdown
        # event instead of hard-killing the process.  The event may
        # be set before anything awaits it — that's fine: the run
        # phase will see it immediately and trigger clean teardown.
        shutdown_event = self._install_signal_handlers(shutdown_event)

        # Enter lifecycle adapters — adapters implementing the async
        # context manager protocol are auto-managed via an
        # AsyncExitStack.  They are entered BEFORE the user lifespan
        # and exited AFTER it (LIFO), ensuring adapters remain live
        # for the entire application lifetime.
        #
        # Health offline + MQTT disconnect live in `finally` so they
        # run even when an adapter __aenter__/__aexit__ raises.
        try:
            async with self._enter_lifecycle_adapters(
                resolved_adapters, shutdown_event
            ):
                # --- Phase 2: Device registration and routing ---

                await self._publish_device_availability(health_reporter)

                contexts = self._build_contexts(
                    resolved_settings,
                    mqtt,
                    prefix,
                    shutdown_event,
                    resolved_adapters,
                    resolved_clock,
                )
                router = await self._wire_router(contexts, prefix, error_publisher)

                await self._subscribe_and_connect(mqtt, router)

                # --- Phase 3: Run ---
                await self._run_lifespan_and_devices(
                    resolved_settings,
                    resolved_adapters,
                    health_reporter,
                    error_publisher,
                    contexts,
                    shutdown_event,
                )
        finally:
            # Adapter lifecycle cleanup completed (AsyncExitStack LIFO).
            # Health offline and MQTT disconnect run unconditionally.
            await health_reporter.shutdown()

            if isinstance(mqtt, MqttLifecycle):
                await mqtt.stop()

        logger.info("Shutdown complete")

    def _resolve_settings(self, settings: Settings | None) -> Settings:
        """Return the effective settings instance.

        Priority: explicit override > eagerly-created > fresh from class.
        """
        if settings is not None:
            return settings
        if self._settings is not None:
            return self._settings
        return self._settings_class()

    @asynccontextmanager
    async def _enter_lifecycle_adapters(
        self,
        resolved_adapters: dict[type, object],
        shutdown_event: asyncio.Event,
    ) -> AsyncIterator[None]:
        """Enter adapters that implement the async context manager protocol.

        Uses :class:`~contextlib.AsyncExitStack` for LIFO exit ordering
        and exception safety.  Non-lifecycle adapters are ignored.
        Shared instances (same object registered for multiple ports)
        are entered only once, identified by ``id()``.

        Each adapter entry is raced against *shutdown_event* so that a
        signal arriving during a slow ``__aenter__`` triggers a clean
        abort instead of an indefinite hang.
        """
        async with contextlib.AsyncExitStack() as stack:
            seen: set[int] = set()
            for adapter in resolved_adapters.values():
                if not _is_async_context_manager(adapter):
                    continue
                if id(adapter) in seen:
                    continue
                if not callable(getattr(adapter, "__aenter__", None)):
                    msg = f"Adapter {adapter!r} has __aenter__ but it's not callable"
                    raise TypeError(msg)
                seen.add(id(adapter))

                aborted = await self._enter_adapter_or_abort(
                    stack, adapter, shutdown_event
                )
                if aborted:
                    break
            yield

    async def _enter_adapter_or_abort(
        self,
        stack: contextlib.AsyncExitStack,
        adapter: object,
        shutdown_event: asyncio.Event,
    ) -> bool:
        """Enter a single adapter, racing against the shutdown event.

        Returns ``True`` if the shutdown event fired before entry
        completed (caller should stop entering further adapters).
        Returns ``False`` on successful entry.
        """
        entry_task: asyncio.Task[object] = asyncio.create_task(
            stack.enter_async_context(adapter)  # type: ignore[arg-type]
        )
        shutdown_task: asyncio.Task[bool] = asyncio.create_task(shutdown_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {entry_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            # Guarantee cleanup on all exit paths (including external
            # cancellation of this coroutine).
            entry_task.cancel()
            shutdown_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(entry_task, shutdown_task)

        # Re-raise with original traceback if __aenter__ failed.
        if entry_task.done() and not entry_task.cancelled():
            entry_task.result()

        if shutdown_task in done and entry_task not in done:
            logger.warning(
                "Shutdown requested during entry of adapter %s "
                "— skipping remaining adapters",
                type(adapter).__name__,
            )
            return True

        return False

    async def _run_lifespan_and_devices(
        self,
        resolved_settings: Settings,
        resolved_adapters: dict[type, object],
        health_reporter: HealthReporter,
        error_publisher: ErrorPublisher,
        contexts: dict[str, DeviceContext],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Enter lifespan, run devices, and tear down.

        Startup errors in the lifespan propagate immediately,
        preventing device launch.  Teardown errors are logged but
        do not mask device errors.
        """
        app_context = AppContext(
            settings=resolved_settings,
            adapters=resolved_adapters,
        )

        lifespan_cm = self._lifespan(app_context)
        await lifespan_cm.__aenter__()

        try:
            await health_reporter.publish_heartbeat()
            heartbeat_task = self._start_heartbeat_task(health_reporter)

            device_tasks = self._start_device_tasks(
                contexts, error_publisher, health_reporter
            )

            await shutdown_event.wait()

            # --- Phase 4: Tear down ---
            await self._cancel_tasks(device_tasks)
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
        finally:
            exc_info = sys.exc_info()
            try:
                await lifespan_cm.__aexit__(*exc_info)
            except Exception:
                logger.exception("Lifespan teardown error")
            finally:
                del exc_info  # avoid reference cycle (PEP 3110)

    # --- _run_async helpers ------------------------------------------------

    def _create_mqtt(
        self,
        mqtt: MqttPort | None,
        resolved_settings: Settings,
        prefix: str,
    ) -> MqttPort:
        """Create the MQTT client, or return the injected one.

        When no explicit ``client_id`` is configured, generates one
        from the app name and a short random suffix (e.g.
        ``"velux2mqtt-a1b2c3d4"``) for debuggability.
        """
        if mqtt is not None:
            return mqtt
        mqtt_settings = resolved_settings.mqtt
        if not mqtt_settings.client_id:
            generated_id = f"{self._name}-{uuid.uuid4().hex[:8]}"
            mqtt_settings = mqtt_settings.model_copy(
                update={"client_id": generated_id},
            )
        will = build_will_config(prefix)
        return MqttClient(settings=mqtt_settings, will=will)

    def _create_services(
        self,
        mqtt: MqttPort,
        prefix: str,
        clock: ClockPort,
    ) -> tuple[HealthReporter, ErrorPublisher]:
        """Build the HealthReporter and ErrorPublisher."""
        health_reporter = HealthReporter(
            mqtt=mqtt,
            topic_prefix=prefix,
            version=self._version,
            clock=clock,
        )
        error_publisher = ErrorPublisher(
            mqtt=mqtt,
            topic_prefix=prefix,
        )
        return health_reporter, error_publisher

    def _install_signal_handlers(
        self,
        shutdown_event: asyncio.Event | None,
    ) -> asyncio.Event:
        """Install SIGTERM/SIGINT handlers. Returns the shutdown event."""
        if shutdown_event is not None:
            return shutdown_event
        event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, event.set)
        return event

    async def _publish_device_availability(
        self,
        health_reporter: HealthReporter,
    ) -> None:
        """Publish availability for all registered devices."""
        for reg in self._all_registrations:
            await health_reporter.publish_device_available(
                reg.name,
                is_root=reg.is_root,
            )

    def _build_contexts(
        self,
        settings: Settings,
        mqtt: MqttPort,
        prefix: str,
        shutdown_event: asyncio.Event,
        adapters: dict[type, object],
        clock: ClockPort,
    ) -> dict[str, DeviceContext]:
        """Build a DeviceContext for every registered device."""
        contexts: dict[str, DeviceContext] = {}
        for reg in self._all_registrations:
            contexts[reg.name] = DeviceContext(
                name=reg.name,
                settings=settings,
                mqtt=mqtt,
                topic_prefix=prefix,
                shutdown_event=shutdown_event,
                adapters=adapters,
                clock=clock,
                is_root=reg.is_root,
            )
        return contexts

    async def _wire_router(
        self,
        contexts: dict[str, DeviceContext],
        prefix: str,
        error_publisher: ErrorPublisher,
    ) -> TopicRouter:
        """Create a TopicRouter and register command-handler proxies."""
        router = TopicRouter(topic_prefix=prefix)
        for reg in self._devices:
            dev_ctx = contexts[reg.name]

            async def _proxy(
                topic: str,
                payload: str,
                _ctx: DeviceContext = dev_ctx,
                _ep: ErrorPublisher = error_publisher,
                _name: str = reg.name,
                _is_root: bool = reg.is_root,
            ) -> None:
                handler = _ctx.command_handler
                if handler is not None:
                    try:
                        await handler(topic, payload)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.error(
                            "Device '%s' command handler error: %s",
                            _name,
                            exc,
                        )
                        with contextlib.suppress(Exception):
                            await _ep.publish(exc, device=_name, is_root=_is_root)

            router.register(reg.name, _proxy, is_root=reg.is_root)

        for cmd_reg in self._commands:
            cmd_ctx = contexts[cmd_reg.name]

            # Create per-device store for command handler
            if self._store is not None:
                cmd_store = DeviceStore(self._store, cmd_reg.name)
                cmd_store.load()
                self._command_stores[cmd_reg.name] = cmd_store

            # Run init callback once and cache the result
            if cmd_reg.init is not None:
                cmd_providers = build_providers(cmd_ctx, cmd_reg.name)
                if cmd_reg.name in self._command_stores:
                    cmd_providers[DeviceStore] = self._command_stores[cmd_reg.name]
                try:
                    init_result = _call_init(
                        cmd_reg.init, cmd_reg.init_injection_plan, cmd_providers
                    )
                    self._command_init_results[cmd_reg.name] = init_result
                except Exception as exc:
                    logger.error(
                        "Command '%s' init= callback failed: %s",
                        cmd_reg.name,
                        exc,
                    )
                    with contextlib.suppress(Exception):
                        await error_publisher.publish(
                            exc,
                            device=cmd_reg.name,
                            is_root=cmd_reg.is_root,
                        )

            # Flush store if init= mutated it
            if cmd_reg.name in self._command_stores:
                cmd_st = self._command_stores[cmd_reg.name]
                if cmd_st.dirty:
                    try:
                        cmd_st.save()
                    except Exception:
                        logger.exception(
                            "Failed to save store after init= for command '%s'",
                            cmd_reg.name,
                        )

            async def _cmd_proxy(
                topic: str,
                payload: str,
                _reg: _CommandRegistration = cmd_reg,
                _ctx: DeviceContext = cmd_ctx,
                _ep: ErrorPublisher = error_publisher,
            ) -> None:
                await self._run_command(_reg, _ctx, topic, payload, _ep)

            router.register(cmd_reg.name, _cmd_proxy, is_root=cmd_reg.is_root)

        return router

    @staticmethod
    async def _subscribe_and_connect(
        mqtt: MqttPort,
        router: TopicRouter,
    ) -> None:
        """Subscribe to command topics and wire message handler."""
        for topic in router.subscriptions:
            await mqtt.subscribe(topic)
        if isinstance(mqtt, MqttMessageHandler):
            mqtt.on_message(router.route)

    def _start_device_tasks(
        self,
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
        health_reporter: HealthReporter,
    ) -> list[asyncio.Task[None]]:
        """Create asyncio tasks for all registered devices."""
        tasks: list[asyncio.Task[None]] = []
        for dev_reg in self._devices:
            tasks.append(
                asyncio.create_task(
                    self._run_device(
                        dev_reg,
                        contexts[dev_reg.name],
                        error_publisher,
                    ),
                ),
            )
        # Partition telemetry by group
        groups: dict[str, list[_TelemetryRegistration]] = {}
        for tel_reg in self._telemetry:
            if tel_reg.group is None:
                # Ungrouped — independent task (unchanged behavior)
                tasks.append(
                    asyncio.create_task(
                        self._run_telemetry(
                            tel_reg,
                            contexts[tel_reg.name],
                            error_publisher,
                            health_reporter,
                        ),
                    ),
                )
            else:
                groups.setdefault(tel_reg.group, []).append(tel_reg)

        # Create one scheduler task per coalescing group
        for group_name, group_regs in groups.items():
            tasks.append(
                asyncio.create_task(
                    self._run_telemetry_group(
                        group_name,
                        group_regs,
                        contexts,
                        error_publisher,
                        health_reporter,
                    ),
                ),
            )

        return tasks

    def _start_heartbeat_task(
        self,
        health_reporter: HealthReporter,
    ) -> asyncio.Task[None] | None:
        """Start the periodic heartbeat background task, if enabled.

        Returns ``None`` when ``heartbeat_interval`` is ``None``
        (heartbeats disabled).
        """
        if self._heartbeat_interval is None:
            return None
        return asyncio.create_task(
            self._heartbeat_loop(health_reporter, self._heartbeat_interval),
        )

    @staticmethod
    async def _heartbeat_loop(
        health_reporter: HealthReporter,
        interval: float,
    ) -> None:
        """Publish heartbeats at a fixed interval until cancelled.

        The loop sleeps *first*, then publishes — the initial heartbeat
        is published separately before this task starts so there is no
        delay on startup.  ``publish_heartbeat()`` is fire-and-forget
        (errors are logged, never propagated).
        """
        while True:
            await asyncio.sleep(interval)
            await health_reporter.publish_heartbeat()

    @staticmethod
    async def _cancel_tasks(tasks: list[asyncio.Task[None]]) -> None:
        """Cancel device tasks and wait for graceful completion."""
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(
                result,
                asyncio.CancelledError,
            ):
                logger.error("Task error during shutdown: %s", result)
