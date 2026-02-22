"""Application orchestrator for cosalette IoT-to-MQTT bridges.

The :class:`App` class is the central composition root. It provides a
decorator-based API for registering devices and adapters, an optional
lifespan context manager for startup/shutdown, then orchestrates the
full application lifecycle via :meth:`run`.

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
import signal
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from cosalette._clock import ClockPort, SystemClock
from cosalette._context import AppContext, DeviceContext, _import_string
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter, build_will_config
from cosalette._injection import build_injection_plan, build_providers, resolve_kwargs
from cosalette._logging import configure_logging
from cosalette._mqtt import MqttClient, MqttLifecycle, MqttMessageHandler, MqttPort
from cosalette._router import TopicRouter
from cosalette._settings import Settings
from cosalette._strategies import PublishStrategy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DeviceRegistration:
    """Internal record of a registered @app.device function."""

    name: str
    func: Callable[..., Awaitable[None]]
    injection_plan: list[tuple[str, type]]
    is_root: bool = False


@dataclass(frozen=True, slots=True)
class _TelemetryRegistration:
    """Internal record of a registered @app.telemetry function."""

    name: str
    func: Callable[..., Awaitable[dict[str, object] | None]]
    injection_plan: list[tuple[str, type]]
    interval: float
    is_root: bool = False
    publish_strategy: PublishStrategy | None = None


@dataclass(frozen=True, slots=True)
class _CommandRegistration:
    """Internal record of a registered @app.command handler."""

    name: str
    func: Callable[..., Awaitable[dict[str, object] | None]]
    injection_plan: list[tuple[str, type]]
    mqtt_params: frozenset[str]  # subset of {"topic", "payload"} declared by handler
    is_root: bool = False


@dataclass(frozen=True, slots=True)
class _AdapterEntry:
    """Internal record of a registered adapter.

    Both impl and dry_run can be either a class, a factory callable,
    or a ``module:ClassName`` string for lazy import.
    """

    impl: type | str | Callable[..., object]
    dry_run: type | str | Callable[..., object] | None = None


# ---------------------------------------------------------------------------
# Lifespan type + no-op default
# ---------------------------------------------------------------------------

type LifespanFunc = Callable[[AppContext], AbstractAsyncContextManager[None]]
"""Type alias for the lifespan parameter."""


@asynccontextmanager
async def _noop_lifespan(_ctx: AppContext) -> AsyncIterator[None]:
    """No-op lifespan used when no user lifespan is provided."""
    yield


# ---------------------------------------------------------------------------
# Adapter resolution helpers
# ---------------------------------------------------------------------------


def _build_adapter_providers(settings: Settings) -> dict[type, Any]:
    """Build the provider map available during adapter resolution.

    At adapter-resolution time only the parsed :class:`Settings`
    instance is available (MQTT, clock, and device contexts are
    created later in the bootstrap sequence).

    Returns a mapping keyed by both :class:`Settings` and the
    concrete settings subclass, so factories can annotate with
    either.
    """
    # Extend this dict when new types become injectable at
    # adapter-resolution time (e.g. app metadata, logging config).
    providers: dict[type, Any] = {Settings: settings}
    settings_type = type(settings)
    if settings_type is not Settings:
        providers[settings_type] = settings
    return providers


def _call_factory(
    factory: Callable[..., object],
    providers: dict[type, Any],
) -> object:
    """Invoke an adapter factory with signature-based injection.

    Introspects *factory*'s parameters and resolves each from
    *providers*.  Zero-arg factories are called directly (backward
    compatible).  This reuses the same :func:`build_injection_plan`
    / :func:`resolve_kwargs` machinery that device handlers use.

    Args:
        factory: A callable returning an adapter instance.
        providers: Type → instance map (currently just Settings).

    Returns:
        The adapter instance created by *factory*.
    """
    plan = build_injection_plan(factory)
    if not plan:
        return factory()
    kwargs = resolve_kwargs(plan, providers)
    return factory(**kwargs)


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
        """
        self._name = name
        self._version = version
        self._description = description
        self._settings_class = settings_class
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

    # --- Registration decorators -------------------------------------------

    def device(self, name: str | None = None) -> Callable[..., Any]:
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

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            TypeError: If any handler parameter lacks a type annotation.
        """
        if callable(name):
            raise TypeError("Use @app.device(), not @app.device (parentheses required)")

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            resolved_name = name if name is not None else func.__name__
            is_root = name is None
            self._check_device_name(resolved_name, is_root=is_root)
            plan = build_injection_plan(func)
            self._devices.append(
                _DeviceRegistration(
                    name=resolved_name,
                    func=func,
                    injection_plan=plan,
                    is_root=is_root,
                ),
            )
            return func

        return decorator

    def command(self, name: str | None = None) -> Callable[..., Any]:
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
            is_root = name is None
            self._check_device_name(resolved_name, is_root=is_root)
            plan = build_injection_plan(func, mqtt_params={"topic", "payload"})
            sig = inspect.signature(func)
            declared_mqtt = frozenset({"topic", "payload"} & sig.parameters.keys())
            self._commands.append(
                _CommandRegistration(
                    name=resolved_name,
                    func=func,
                    injection_plan=plan,
                    mqtt_params=declared_mqtt,
                    is_root=is_root,
                ),
            )
            return func

        return decorator

    def telemetry(
        self,
        name: str | None = None,
        *,
        interval: float,
        publish: PublishStrategy | None = None,
    ) -> Callable[..., Any]:
        """Register a telemetry device with periodic polling.

        The decorated function returns a dict published as JSON state.
        Parameters are injected based on type annotations — declare
        only what you need.  Zero-parameter handlers are valid.

        The framework calls the handler at the specified interval
        and publishes the returned dict.

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

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            ValueError: If interval <= 0.
            TypeError: If any handler parameter lacks a type annotation.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            resolved_name = name if name is not None else func.__name__
            is_root = name is None
            if interval <= 0:
                msg = f"Telemetry interval must be positive, got {interval}"
                raise ValueError(msg)
            self._check_device_name(resolved_name, is_root=is_root)
            plan = build_injection_plan(func)
            self._telemetry.append(
                _TelemetryRegistration(
                    name=resolved_name,
                    func=func,
                    injection_plan=plan,
                    interval=interval,
                    is_root=is_root,
                    publish_strategy=publish,
                ),
            )
            return func

        return decorator

    def adapter(
        self,
        port_type: type,
        impl: type | str | Callable[..., object],
        *,
        dry_run: type | str | Callable[..., object] | None = None,
    ) -> None:
        """Register an adapter for a port type.

        Args:
            port_type: The Protocol type to register.
            impl: The adapter class, a ``module:ClassName`` lazy import
                string, or a factory callable returning an adapter instance.
            dry_run: Optional dry-run variant (class, lazy import string,
                or factory callable).

        Raises:
            ValueError: If an adapter is already registered for this port type.
        """
        if port_type in self._adapters:
            msg = f"Adapter already registered for {port_type!r}"
            raise ValueError(msg)
        self._adapters[port_type] = _AdapterEntry(impl=impl, dry_run=dry_run)

    # --- Internal helpers --------------------------------------------------

    @property
    def _all_registrations(
        self,
    ) -> list[_DeviceRegistration | _TelemetryRegistration | _CommandRegistration]:
        """All device registrations across the three registries."""
        return [*self._devices, *self._telemetry, *self._commands]

    def _check_device_name(self, name: str, *, is_root: bool = False) -> None:
        """Raise if name collides with any device, telemetry, or command.

        When *is_root* is True, also enforces that at most one root
        (unnamed) device exists and logs a warning when root and named
        devices are mixed.
        """
        names, has_root = self._registration_summary()
        self._validate_name_unique(name, names)
        if is_root:
            self._validate_single_root(has_root)
        self._warn_if_mixing(is_root, has_root=has_root, has_named=bool(names))

    def _registration_summary(self) -> tuple[set[str], bool]:
        """Return (registered names, has_root_device) in a single pass."""
        names: set[str] = set()
        has_root = False
        for reg in self._all_registrations:
            names.add(reg.name)
            has_root = has_root or reg.is_root
        return names, has_root

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
        :func:`_import_string` before instantiation.  Factory callables
        (non-type callables) are resolved via signature-based injection
        — if the callable declares a parameter annotated with
        ``Settings`` (or a subclass), the parsed settings instance is
        injected automatically.
        """
        providers = _build_adapter_providers(settings)
        resolved: dict[type, object] = {}
        for port_type, entry in self._adapters.items():
            raw_impl: type | str | Callable[..., object] = (
                entry.dry_run if (self._dry_run and entry.dry_run) else entry.impl
            )
            if isinstance(raw_impl, str):
                imported: Any = _import_string(raw_impl)
                resolved[port_type] = imported()
            elif isinstance(raw_impl, type):
                resolved[port_type] = raw_impl()
            else:
                # Factory callable — signature-based injection
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
        try:
            providers = build_providers(ctx, reg.name)
            kwargs = resolve_kwargs(reg.injection_plan, providers)
            await reg.func(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Device '%s' crashed: %s", reg.name, exc)
            await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)

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
        providers = build_providers(ctx, reg.name)
        kwargs = resolve_kwargs(reg.injection_plan, providers)
        strategy = reg.publish_strategy
        if strategy is not None:
            strategy._bind(ctx.clock)
        last_published: dict[str, object] | None = None
        last_error_type: type[Exception] | None = None
        while not ctx.shutdown_requested:
            try:
                result = await reg.func(**kwargs)

                # None return = suppress this cycle
                if result is None:
                    await ctx.sleep(reg.interval)
                    continue

                if self._should_publish_telemetry(result, last_published, strategy):
                    await ctx.publish_state(result)
                    last_published = result
                    if strategy is not None:
                        strategy.on_published()

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
            providers = build_providers(ctx, reg.name)
            kwargs = resolve_kwargs(reg.injection_plan, providers)
            # Inject only the MQTT message params the handler declared
            if "topic" in reg.mqtt_params:
                kwargs["topic"] = topic
            if "payload" in reg.mqtt_params:
                kwargs["payload"] = payload
            result = await reg.func(**kwargs)
            if result is not None:
                await ctx.publish_state(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Command handler '%s' error: %s", reg.name, exc)
            with contextlib.suppress(Exception):
                await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)

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
        resolved_settings = settings if settings is not None else self._settings_class()
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

        # --- Phase 2: Device registration and routing ---
        shutdown_event = self._install_signal_handlers(shutdown_event)

        await self._publish_device_availability(health_reporter)

        contexts = self._build_contexts(
            resolved_settings,
            mqtt,
            prefix,
            shutdown_event,
            resolved_adapters,
            resolved_clock,
        )
        router = self._wire_router(contexts, prefix, error_publisher)

        await self._subscribe_and_connect(mqtt, router)

        # --- Phase 3: Run ---
        app_context = AppContext(
            settings=resolved_settings,
            adapters=resolved_adapters,
        )

        # Enter lifespan — startup code runs before yield.
        # Startup errors propagate immediately, preventing device launch.
        lifespan_cm = self._lifespan(app_context)
        await lifespan_cm.__aenter__()

        try:
            # Publish an initial heartbeat immediately, then start the
            # periodic loop (if enabled).  The initial heartbeat overwrites
            # the LWT "offline" string that the broker may have retained.
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
            # Exit lifespan — teardown code runs after yield.
            # Teardown errors are logged but don't mask device errors.
            # Pass real exception info so the lifespan can inspect it
            # (e.g. for cleanup decisions based on error type).
            # Return value intentionally ignored — device exceptions must
            # always propagate; the lifespan cannot suppress them.
            exc_info = sys.exc_info()
            try:
                await lifespan_cm.__aexit__(*exc_info)
            except Exception:
                logger.exception("Lifespan teardown error")
            finally:
                del exc_info  # avoid reference cycle (PEP 3110)

        await health_reporter.shutdown()

        if isinstance(mqtt, MqttLifecycle):
            await mqtt.stop()

        logger.info("Shutdown complete")

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

    def _wire_router(
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
        for tel_reg in self._telemetry:
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
