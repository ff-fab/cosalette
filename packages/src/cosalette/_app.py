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
import dataclasses
import inspect
import logging
import signal
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from pydantic import ValidationError

from cosalette._clock import ClockPort, SystemClock
from cosalette._context import AppContext, DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter, build_will_config
from cosalette._injection import build_injection_plan, build_providers, resolve_kwargs
from cosalette._logging import configure_logging
from cosalette._mqtt import MqttClient, MqttLifecycle, MqttMessageHandler, MqttPort
from cosalette._persist import PersistPolicy
from cosalette._registration import (
    IntervalSpec as IntervalSpec,
)
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
    check_device_name,
)
from cosalette._router import TopicRouter
from cosalette._runner_utils import (
    create_device_store,
    publish_error_safely,
    save_store_on_shutdown,
)
from cosalette._settings import Settings
from cosalette._stores import DeviceStore, Store
from cosalette._strategies import PublishStrategy
from cosalette._telemetry_runner import TelemetryRunner
from cosalette._telemetry_runner import _to_ms as _to_ms  # re-export for tests
from cosalette._utils import _import_string

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
            if not callable(raw_impl):  # narrow for mypy
                msg = (
                    f"expected callable adapter for {port_type.__name__}, "
                    f"got {type(raw_impl).__name__}: {raw_impl!r}"
                )
                raise TypeError(msg)
            resolved[port_type] = _call_factory(raw_impl, providers)
        return resolved

    # --- Command runner ----------------------------------------------------

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
            await publish_error_safely(error_publisher, exc, reg.name, reg.is_root)
        finally:
            save_store_on_shutdown(self._command_stores.get(reg.name), reg.name)

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
        self._resolve_intervals(resolved_settings)
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

    def _resolve_intervals(self, settings: Settings) -> None:
        """Resolve any callable intervals to concrete floats.

        Called once in :meth:`_run_async` after settings are resolved.
        Replaces ``_TelemetryRegistration`` entries that have callable
        intervals with new frozen instances containing the resolved
        float value.

        Raises:
            ValueError: If a resolved interval is zero or negative.
        """
        for i, reg in enumerate(self._telemetry):
            if callable(reg.interval):
                resolved = reg.interval(settings)
                if resolved <= 0:
                    msg = (
                        f"Telemetry interval for {reg.name!r} must be "
                        f"positive, got {resolved}"
                    )
                    raise ValueError(msg)
                self._telemetry[i] = dataclasses.replace(reg, interval=resolved)

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
        """Publish availability for all registered devices.

        When telemetry and command share a name (scoped uniqueness),
        availability is published once for the shared name.
        """
        seen: set[str] = set()
        for reg in self._all_registrations:
            if reg.name not in seen:
                seen.add(reg.name)
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
        """Build a DeviceContext for every registered device.

        When a telemetry and command registration share the same name
        (scoped name uniqueness), only one :class:`DeviceContext` is
        created for that name — they share a single context.
        """
        contexts: dict[str, DeviceContext] = {}
        for reg in self._all_registrations:
            if reg.name not in contexts:
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

    # -- _wire_router helpers --------------------------------------------------

    def _register_device_proxy(
        self,
        reg: _DeviceRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        router: TopicRouter,
    ) -> None:
        """Create a command-handler proxy for a device and register it."""
        dev_ctx = ctx

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
                    await publish_error_safely(_ep, exc, _name, _is_root)

        router.register(reg.name, _proxy, is_root=reg.is_root)

    def _init_command_store(
        self,
        cmd_reg: _CommandRegistration,
    ) -> DeviceStore | None:
        """Create a per-device store for a command handler.

        Returns the store when persistence is enabled, otherwise ``None``.
        """
        if self._store is not None:
            store = create_device_store(self._store, cmd_reg.name)
            self._command_stores[cmd_reg.name] = store
            return store
        return None

    async def _init_command_handler(
        self,
        cmd_reg: _CommandRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
    ) -> None:
        """Run the optional init callback for a command handler.

        Caches the result in ``self._command_init_results``.  If init fails the
        error is logged and published safely.  If the store is dirty after init
        it is flushed.
        """
        if cmd_reg.init is not None:
            cmd_providers = build_providers(ctx, cmd_reg.name)
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
                await publish_error_safely(
                    error_publisher, exc, cmd_reg.name, cmd_reg.is_root
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

    async def _register_command_proxy(
        self,
        cmd_reg: _CommandRegistration,
        ctx: DeviceContext,
        error_publisher: ErrorPublisher,
        router: TopicRouter,
    ) -> None:
        """Orchestrate command store init, handler init, and proxy registration."""
        cmd_ctx = ctx
        self._init_command_store(cmd_reg)
        await self._init_command_handler(cmd_reg, cmd_ctx, error_publisher)

        async def _cmd_proxy(
            topic: str,
            payload: str,
            _reg: _CommandRegistration = cmd_reg,
            _ctx: DeviceContext = cmd_ctx,
            _ep: ErrorPublisher = error_publisher,
        ) -> None:
            await self._run_command(_reg, _ctx, topic, payload, _ep)

        router.register(cmd_reg.name, _cmd_proxy, is_root=cmd_reg.is_root)

    # -- end _wire_router helpers ---------------------------------------------

    async def _wire_router(
        self,
        contexts: dict[str, DeviceContext],
        prefix: str,
        error_publisher: ErrorPublisher,
    ) -> TopicRouter:
        """Create a TopicRouter and register command-handler proxies."""
        router = TopicRouter(topic_prefix=prefix)
        for reg in self._devices:
            self._register_device_proxy(
                reg, contexts[reg.name], error_publisher, router
            )
        for cmd_reg in self._commands:
            await self._register_command_proxy(
                cmd_reg, contexts[cmd_reg.name], error_publisher, router
            )
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
        runner = TelemetryRunner(store=self._store)
        tasks: list[asyncio.Task[None]] = []
        for dev_reg in self._devices:
            tasks.append(
                asyncio.create_task(
                    runner.run_device(
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
                        runner.run_telemetry(
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
                    runner.run_telemetry_group(
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
