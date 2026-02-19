"""Application orchestrator for cosalette IoT-to-MQTT bridges.

The :class:`App` class is the central composition root. It provides a
decorator-based API for registering devices, lifecycle hooks, and
adapters, then orchestrates the full application lifecycle via
:meth:`run`.

Typical usage::

    import cosalette

    app = cosalette.App(name="mybridge", version="0.1.0")

    @app.device("sensor")
    async def sensor(ctx: cosalette.DeviceContext) -> None:
        while not ctx.shutdown_requested:
            await ctx.publish_state({"value": read_sensor()})
            await ctx.sleep(10)

    app.run()

See Also:
    ADR-001 — Framework architecture (IoC, composition root).
    ADR-010 — Device archetypes (device vs telemetry).
    ADR-006 — Hexagonal architecture (adapter registration).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cosalette._clock import ClockPort, SystemClock
from cosalette._context import AppContext, DeviceContext, _import_string
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter, build_will_config
from cosalette._logging import configure_logging
from cosalette._mqtt import MqttClient, MqttLifecycle, MqttMessageHandler, MqttPort
from cosalette._router import TopicRouter
from cosalette._settings import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DeviceRegistration:
    """Internal record of a registered @app.device function."""

    name: str
    func: Callable[[DeviceContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _TelemetryRegistration:
    """Internal record of a registered @app.telemetry function."""

    name: str
    func: Callable[[DeviceContext], Awaitable[dict[str, object]]]
    interval: float


@dataclass(frozen=True, slots=True)
class _AdapterEntry:
    """Internal record of a registered adapter.

    Both impl and dry_run can be either a class, a factory callable,
    or a ``module:ClassName`` string for lazy import.
    """

    impl: type | str | Callable[[], object]
    dry_run: type | str | Callable[[], object] | None = None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class App:
    """Central composition root and application orchestrator.

    Collects device registrations, lifecycle hooks, and adapter
    mappings via a decorator-based API, then runs the full async
    lifecycle in :meth:`run`.

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
        self._devices: list[_DeviceRegistration] = []
        self._telemetry: list[_TelemetryRegistration] = []
        self._startup_hooks: list[Callable[[AppContext], Awaitable[None]]] = []
        self._shutdown_hooks: list[Callable[[AppContext], Awaitable[None]]] = []
        self._adapters: dict[type, _AdapterEntry] = {}

    # --- Registration decorators -------------------------------------------

    def device(self, name: str) -> Callable[..., Any]:
        """Register a command & control device.

        The decorated function receives a :class:`DeviceContext` and runs
        as a concurrent asyncio task.  The framework subscribes to
        ``{name}/set`` and routes commands to the handler registered via
        ``ctx.on_command``.

        Args:
            name: Device name for MQTT topics and logging.

        Raises:
            ValueError: If a device with this name is already registered.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._check_device_name(name)
            self._devices.append(_DeviceRegistration(name=name, func=func))
            return func

        return decorator

    def telemetry(self, name: str, *, interval: float) -> Callable[..., Any]:
        """Register a telemetry device with periodic polling.

        The decorated function receives a :class:`DeviceContext` and
        returns a dict.  The framework calls it at the specified interval
        and publishes the returned dict as JSON state.

        Args:
            name: Device name for MQTT topics and logging.
            interval: Polling interval in seconds.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If interval <= 0.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if interval <= 0:
                msg = f"Telemetry interval must be positive, got {interval}"
                raise ValueError(msg)
            self._check_device_name(name)
            self._telemetry.append(
                _TelemetryRegistration(name=name, func=func, interval=interval),
            )
            return func

        return decorator

    def on_startup(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Register a startup hook.

        Called after MQTT connects, before devices start.
        """
        self._startup_hooks.append(func)
        return func

    def on_shutdown(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Register a shutdown hook.

        Called after devices stop, before MQTT disconnects.
        """
        self._shutdown_hooks.append(func)
        return func

    def adapter(
        self,
        port_type: type,
        impl: type | str | Callable[[], object],
        *,
        dry_run: type | str | Callable[[], object] | None = None,
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

    def _check_device_name(self, name: str) -> None:
        """Raise ValueError if name is already used by any device or telemetry."""
        all_names = [d.name for d in self._devices] + [t.name for t in self._telemetry]
        if name in all_names:
            msg = f"Device name '{name}' is already registered"
            raise ValueError(msg)

    def _resolve_adapters(self) -> dict[type, object]:
        """Resolve all registered adapters to instances.

        When ``self._dry_run`` is True and an entry has a ``dry_run``
        variant, the dry-run implementation is used instead of the
        normal one.  String values are lazily imported via
        :func:`_import_string` before instantiation.  Factory callables
        (non-type callables) are invoked directly — useful when an
        adapter needs constructor arguments.
        """
        resolved: dict[type, object] = {}
        for port_type, entry in self._adapters.items():
            raw_impl: type | str | Callable[[], object] = (
                entry.dry_run if (self._dry_run and entry.dry_run) else entry.impl
            )
            if isinstance(raw_impl, str):
                imported: Any = _import_string(raw_impl)
                resolved[port_type] = imported()
            elif isinstance(raw_impl, type):
                resolved[port_type] = raw_impl()
            else:
                # Factory callable — invoke directly
                resolved[port_type] = raw_impl()
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
            await reg.func(ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Device '%s' crashed: %s", reg.name, exc)
            await error_publisher.publish(exc, device=reg.name)

    async def _run_telemetry(
        self,
        reg: _TelemetryRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
    ) -> None:
        """Run a telemetry polling loop."""
        while not ctx.shutdown_requested:
            try:
                result = await reg.func(ctx)
                await ctx.publish_state(result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Telemetry '%s' error: %s", reg.name, exc)
                await error_publisher.publish(exc, device=reg.name)
            await ctx.sleep(reg.interval)

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
        3. Run startup hooks, start devices, block until shutdown.
        4. Tear down (shutdown hooks, cancel tasks, health offline).

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

        resolved_adapters = self._resolve_adapters()
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
        await self._run_hooks(self._startup_hooks, app_context, "Startup")

        # Publish an initial heartbeat immediately, then start the
        # periodic loop (if enabled).  The initial heartbeat overwrites
        # the LWT "offline" string that the broker may have retained.
        await health_reporter.publish_heartbeat()
        heartbeat_task = self._start_heartbeat_task(health_reporter)

        device_tasks = self._start_device_tasks(contexts, error_publisher)

        await shutdown_event.wait()

        # --- Phase 4: Tear down ---
        await self._cancel_tasks(device_tasks)
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        await self._run_hooks(self._shutdown_hooks, app_context, "Shutdown")
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
        for dev_reg in self._devices:
            await health_reporter.publish_device_available(dev_reg.name)
        for tel_reg in self._telemetry:
            await health_reporter.publish_device_available(tel_reg.name)

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
        all_names = [d.name for d in self._devices] + [t.name for t in self._telemetry]
        for dev_name in all_names:
            contexts[dev_name] = DeviceContext(
                name=dev_name,
                settings=settings,
                mqtt=mqtt,
                topic_prefix=prefix,
                shutdown_event=shutdown_event,
                adapters=adapters,
                clock=clock,
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
            ) -> None:
                handler = _ctx.command_handler
                if handler is not None:
                    try:
                        await handler(topic, payload)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        with contextlib.suppress(Exception):
                            await _ep.publish(exc, device=_name)

            router.register(reg.name, _proxy)
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

    @staticmethod
    async def _run_hooks(
        hooks: list[Callable[[AppContext], Awaitable[None]]],
        app_context: AppContext,
        label: str,
    ) -> None:
        """Run a list of lifecycle hooks, logging errors."""
        for hook in hooks:
            try:
                await hook(app_context)
            except Exception:
                logger.exception("%s hook error", label)

    def _start_device_tasks(
        self,
        contexts: dict[str, DeviceContext],
        error_publisher: ErrorPublisher,
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
