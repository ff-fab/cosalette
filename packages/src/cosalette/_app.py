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
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from cosalette import _adapter_lifecycle, _wiring
from cosalette._adapter_lifecycle import _AdapterEntry
from cosalette._clock import ClockPort, SystemClock
from cosalette._context import DeviceContext
from cosalette._health import HealthReporter
from cosalette._injection import build_injection_plan
from cosalette._logging import configure_logging
from cosalette._mqtt import MqttLifecycle, MqttPort
from cosalette._persist import PersistPolicy
from cosalette._registration import (
    IntervalSpec as IntervalSpec,
)
from cosalette._registration import (
    LifespanFunc as LifespanFunc,
)
from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _noop_lifespan,
    _TelemetryRegistration,
    _validate_init,
    check_device_name,
)
from cosalette._settings import Settings
from cosalette._stores import Store
from cosalette._strategies import PublishStrategy
from cosalette._telemetry_runner import _to_ms as _to_ms  # re-export for tests

logger = logging.getLogger(__name__)


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
        self._adapters: dict[type, _AdapterEntry] = {}
        self._store = store

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
            resolved_name = name if name is not None else func.__name__
            self.add_device(
                resolved_name, func, init=init, enabled=enabled, is_root=name is None
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
        is_root: bool = False,
    ) -> None:
        """Register a command & control device imperatively.

        This is the imperative counterpart to :meth:`device`.  It
        always creates a *named* (non-root) registration by default.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async callable that implements the device loop.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                *func* by type.
            enabled: When ``False``, registration is silently skipped
                — no entry in the registry and no name slot reserved.
                Defaults to ``True``.
            is_root: When ``True``, the device publishes to root-level
                topics (``{prefix}/state`` instead of
                ``{prefix}/{name}/state``).  Defaults to ``False``.

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
        check_device_name(
            name,
            registry_type="device",
            is_root=is_root,
            devices=self._devices,
            telemetry=self._telemetry,
            commands=self._commands,
        )
        plan = build_injection_plan(func)
        self._devices.append(
            _DeviceRegistration(
                name=name,
                func=func,
                injection_plan=plan,
                is_root=is_root,
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
            resolved_name = name if name is not None else func.__name__
            self.add_command(
                resolved_name, func, init=init, enabled=enabled, is_root=name is None
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
        is_root: bool = False,
    ) -> None:
        """Register a command handler imperatively.

        This is the imperative counterpart to :meth:`command`.  It
        always creates a *named* (non-root) registration by default.

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
            is_root: When ``True``, the device publishes to root-level
                topics (``{prefix}/state`` instead of
                ``{prefix}/{name}/state``).  Defaults to ``False``.

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
        check_device_name(
            name,
            registry_type="command",
            is_root=is_root,
            devices=self._devices,
            telemetry=self._telemetry,
            commands=self._commands,
        )
        plan = build_injection_plan(func, mqtt_params={"topic", "payload"})
        sig = inspect.signature(func)
        declared_mqtt = frozenset({"topic", "payload"} & sig.parameters.keys())
        self._commands.append(
            _CommandRegistration(
                name=name,
                func=func,
                injection_plan=plan,
                mqtt_params=declared_mqtt,
                is_root=is_root,
                init=init,
                init_injection_plan=init_plan,
            ),
        )

    def telemetry(
        self,
        name: str | None = None,
        *,
        interval: IntervalSpec,
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
            interval: Polling interval in seconds, or a callable
                ``(Settings) -> float`` for deferred resolution.
                When a callable is provided, it is invoked once in
                :meth:`_run_async` after settings are resolved —
                this allows reading intervals from settings without
                requiring valid settings at registration time (e.g.
                during ``--help`` / ``--version``).
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
            ValueError: If *interval* is a float and <= 0.  For
                callable intervals, validation is deferred to
                :meth:`_run_async`.
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
            resolved_name = name if name is not None else func.__name__
            self.add_telemetry(
                resolved_name,
                func,
                interval=interval,
                publish=publish,
                persist=persist,
                init=init,
                enabled=enabled,
                group=group,
                is_root=name is None,
            )
            return func

        return decorator

    def add_telemetry(
        self,
        name: str,
        func: Callable[..., Awaitable[dict[str, object] | None]],
        *,
        interval: IntervalSpec,
        publish: PublishStrategy | None = None,
        persist: PersistPolicy | None = None,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        group: str | None = None,
        is_root: bool = False,
    ) -> None:
        """Register a telemetry device imperatively.

        This is the imperative counterpart to :meth:`telemetry`.  It
        always creates a *named* (non-root) registration by default.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async callable returning a ``dict`` (published as
                state) or ``None`` (suppresses that cycle).
            interval: Polling interval in seconds, or a callable
                ``(Settings) -> float`` for deferred resolution.
                When a callable is provided, it is invoked once in
                :meth:`_run_async` after settings are resolved —
                this allows reading intervals from settings without
                requiring valid settings at registration time (e.g.
                during ``--help`` / ``--version``).
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
            is_root: When ``True``, the device publishes to root-level
                topics (``{prefix}/state`` instead of
                ``{prefix}/{name}/state``).  Defaults to ``False``.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If *interval* is a float and <= 0.  For
                callable intervals, validation is deferred to
                :meth:`_run_async`.
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
        if not callable(interval) and interval <= 0:
            msg = f"Telemetry interval must be positive, got {interval}"
            raise ValueError(msg)
        check_device_name(
            name,
            registry_type="telemetry",
            is_root=is_root,
            devices=self._devices,
            telemetry=self._telemetry,
            commands=self._commands,
        )
        plan = build_injection_plan(func)
        self._telemetry.append(
            _TelemetryRegistration(
                name=name,
                func=func,
                injection_plan=plan,
                interval=interval,
                is_root=is_root,
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

    def _resolve_adapters(self, settings: Settings) -> dict[type, object]:
        """Resolve all registered adapters to instances.

        Delegates to :func:`_adapter_lifecycle.resolve_adapters`.
        """
        return _adapter_lifecycle.resolve_adapters(
            self._adapters, self._dry_run, settings
        )

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
        resolved_settings = _wiring.resolve_settings(
            settings, self._settings, self._settings_class
        )
        _wiring.resolve_intervals(self._telemetry, resolved_settings)
        prefix = resolved_settings.mqtt.topic_prefix or self._name
        configure_logging(
            resolved_settings.logging,
            service=self._name,
            version=self._version,
        )

        resolved_adapters = _adapter_lifecycle.resolve_adapters(
            self._adapters, self._dry_run, resolved_settings
        )
        resolved_clock = clock if clock is not None else SystemClock()

        mqtt_client = _wiring.create_mqtt(mqtt, resolved_settings, prefix, self._name)
        health_reporter, error_publisher = _wiring.create_services(
            mqtt_client, prefix, self._version, resolved_clock
        )

        if isinstance(mqtt_client, MqttLifecycle):
            await mqtt_client.start()

        shutdown_event = _wiring.install_signal_handlers(shutdown_event)

        try:
            async with _adapter_lifecycle.enter_lifecycle_adapters(
                resolved_adapters, shutdown_event
            ):
                # --- Phase 2: Wire ---
                await _wiring.publish_device_availability(
                    self._all_registrations, health_reporter
                )

                contexts = _wiring.build_contexts(
                    self._all_registrations,
                    resolved_settings,
                    mqtt_client,
                    prefix,
                    shutdown_event,
                    resolved_adapters,
                    resolved_clock,
                )

                router = await _wiring.wire_router(
                    self._devices,
                    self._commands,
                    self._store,
                    contexts,
                    prefix,
                    error_publisher,
                )

                await _wiring.subscribe_and_connect(mqtt_client, router)

                # --- Phase 3: Run ---
                await _wiring.run_lifespan_and_devices(
                    self._lifespan,
                    self._store,
                    self._devices,
                    self._telemetry,
                    self._heartbeat_interval,
                    resolved_settings,
                    resolved_adapters,
                    health_reporter,
                    error_publisher,
                    contexts,
                    shutdown_event,
                )
        finally:
            await health_reporter.shutdown()

            if isinstance(mqtt_client, MqttLifecycle):
                await mqtt_client.stop()

        logger.info("Shutdown complete")

    def _resolve_intervals(self, settings: Settings) -> None:
        """Resolve any callable intervals to concrete floats.

        Delegates to :func:`_wiring.resolve_intervals`.
        """
        _wiring.resolve_intervals(self._telemetry, settings)

    # --- Test-facing convenience delegates --------------------------------

    async def _publish_device_availability(
        self,
        health_reporter: HealthReporter,
    ) -> None:
        """Publish availability for all registered devices.

        Delegates to :func:`_wiring.publish_device_availability`.
        """
        await _wiring.publish_device_availability(
            self._all_registrations, health_reporter
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
        """Build a DeviceContext for every registered device.

        Delegates to :func:`_wiring.build_contexts`.
        """
        return _wiring.build_contexts(
            self._all_registrations,
            settings,
            mqtt,
            prefix,
            shutdown_event,
            adapters,
            clock,
        )
