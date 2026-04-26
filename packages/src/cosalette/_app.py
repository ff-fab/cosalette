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
import datetime
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from cosalette import _adapter_lifecycle, _wiring
from cosalette._adapter_lifecycle import _AdapterEntry
from cosalette._clock import ClockPort, SystemClock
from cosalette._context import DeviceContext
from cosalette._cron import CronSchedule
from cosalette._health import HealthReporter
from cosalette._injection import build_injection_plan
from cosalette._logging import configure_logging
from cosalette._mqtt import MqttLifecycle, MqttPort
from cosalette._periodic import _PeriodicRegistration
from cosalette._persist import PersistPolicy
from cosalette._registration import (
    CronSpec,
    NameSpec,
    _CommandRegistration,
    _DeviceRegistration,
    _noop_lifespan,
    _TelemetryRegistration,
    _validate_init,
    check_device_name,
    validate_mqtt_name,
)
from cosalette._registration import (
    EnabledSpec as EnabledSpec,
)
from cosalette._registration import (
    IntervalSpec as IntervalSpec,
)
from cosalette._registration import (
    LifespanFunc as LifespanFunc,
)
from cosalette._retry import (
    _DEFAULT_BACKOFF,
    _DEFAULT_RETRY_ON,
    BackoffStrategy,
    CircuitBreaker,
)
from cosalette._schema import SchemaRegistry
from cosalette._schema import _enforcement as _schema_enforcement
from cosalette._settings import Settings
from cosalette._state import StateRegistration
from cosalette._stores import Store
from cosalette._strategies import PublishStrategy
from cosalette._telemetry_runner import _to_ms as _to_ms  # re-export for tests
from cosalette._utils import _callable_name, _callable_qualname

if TYPE_CHECKING:
    from cosalette._schema._validator import ValidatingMqttPort

logger = logging.getLogger(__name__)


def _validate_positive_interval(name: str, value: float | None) -> None:
    """Raise ``ValueError`` if *value* is non-``None`` and not positive."""
    if value is not None and value <= 0:
        msg = f"{name} must be positive, got {value}"
        raise ValueError(msg)


def _apply_schema_enforcement(
    mqtt_client: MqttPort,
    schema_registry: SchemaRegistry | None,
    prefix: str,
    registered_names: frozenset[str],
) -> tuple[MqttPort, ValidatingMqttPort | None]:
    """Wrap *mqtt_client* with validation if enforcement is active.

    Returns ``(mqtt_client, validating_port)``; the second element is
    ``None`` when enforcement is off.
    """
    if schema_registry is None or not schema_registry.enforcement.on_publish:
        return mqtt_client, None

    from cosalette._schema._validator import (
        PayloadValidator,
        ValidatingMqttPort,
        build_skip_topics,
    )

    skip = build_skip_topics(prefix, registered_names)
    validator = PayloadValidator(schema_registry)
    port = ValidatingMqttPort(
        inner=mqtt_client,
        validator=validator,
        enforcement=schema_registry.enforcement,
        skip_topics=skip,
    )
    return port, port


async def _publish_schema_status(
    mqtt_client: MqttPort,
    validating_port: ValidatingMqttPort | None,
    schema_registry: SchemaRegistry | None,
    prefix: str,
) -> None:
    """Publish initial schema status if validation is active."""
    if validating_port is None or schema_registry is None:
        return

    from cosalette._schema._validator import SchemaStatusPublisher

    publisher = SchemaStatusPublisher(
        _mqtt=mqtt_client,
        _topic_prefix=prefix,
        _enforcement_mode=schema_registry.enforcement.mode,
        _validating_port=validating_port,
    )
    await publisher.publish_status()


def _validate_periodic_early(
    name: str,
    registered_names: frozenset[str] | set[str],
    interval: object,
) -> None:
    """Validate name uniqueness and interval positivity at decoration time."""
    validate_mqtt_name(name)
    if name in registered_names:
        msg = f"Name '{name}' is already registered"
        raise ValueError(msg)
    if isinstance(interval, (int, float)) and interval <= 0:
        msg = f"Periodic interval for '{name}' must be positive, got {interval}"
        raise ValueError(msg)


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
        health_check_interval: float | None = 30.0,
        lifespan: LifespanFunc | None = None,
        store: Store | Callable[..., Store] | None = None,
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
        restart_after_failures: int = 5,
        max_restarts: int = 3,
        restart_cooldown: float = 5.0,
        sustained_health_reset: float = 300.0,
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
            health_check_interval: Seconds between periodic health
                checks for adapters implementing
                :class:`~cosalette.HealthCheckable`.  Set to ``None`` to
                disable health checks entirely.  Defaults to 30.
            lifespan: Async context manager for application startup
                and shutdown.  Code before ``yield`` runs before devices
                start; code after ``yield`` runs after devices stop.
                Receives an :class:`AppContext`.  When ``None``, a no-op
                default is used.
            store: Optional :class:`Store` backend for device persistence,
                or a callable factory ``Callable[..., Store]`` whose
                parameters are injected from resolved settings and
                adapters at bootstrap time.
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
        validate_mqtt_name(name)
        self._name = name
        self._version = version
        self._description = description
        self._settings_class = settings_class
        try:
            self._settings: Settings | None = settings_class()
        except ValidationError:
            self._settings = None
        self._dry_run = dry_run
        _validate_positive_interval("heartbeat_interval", heartbeat_interval)
        self._heartbeat_interval = heartbeat_interval
        _validate_positive_interval("health_check_interval", health_check_interval)
        self._health_check_interval = health_check_interval
        if restart_after_failures < 0:
            msg = f"restart_after_failures must be >= 0, got {restart_after_failures}"
            raise ValueError(msg)
        self._restart_after_failures = restart_after_failures
        if max_restarts < 0:
            msg = f"max_restarts must be >= 0, got {max_restarts}"
            raise ValueError(msg)
        self._max_restarts = max_restarts
        _validate_positive_interval("restart_cooldown", restart_cooldown)
        self._restart_cooldown = restart_cooldown
        _validate_positive_interval("sustained_health_reset", sustained_health_reset)
        self._sustained_health_reset = sustained_health_reset
        self._lifespan: LifespanFunc = (
            lifespan if lifespan is not None else _noop_lifespan
        )
        self._devices: list[_DeviceRegistration] = []
        self._telemetry: list[_TelemetryRegistration] = []
        self._commands: list[_CommandRegistration] = []
        self._periodic: list[_PeriodicRegistration] = []
        self._state_factories: list[StateRegistration] = []
        self._state_overrides: dict[type, Any] = {}  # for tests
        self._adapters: dict[type, _AdapterEntry] = {}
        self._store_factory: Callable[..., Store] | None = None
        self._store: Store | None = None
        self._apply_store_arg(store)
        self._configure_hooks: list[Callable[..., Any]] = []

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
                    self.adapter(port_type, impl, dry_run=dry_run_impl)  # ty: ignore[invalid-argument-type]
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

    @property
    def _store_configured(self) -> bool:
        return self._store is not None or self._store_factory is not None

    def _apply_store_arg(self, store: Store | Callable[..., Store] | None) -> None:
        if store is None:
            return
        if isinstance(store, Store):
            self._store = store
        elif callable(store):
            self._store_factory = store
        else:
            msg = (
                "store must be None, a Store instance, or a callable factory, "
                f"got {type(store).__name__!r}"
            )
            raise TypeError(msg)

    @property
    def name(self) -> str:
        """Application name (used as MQTT topic prefix and client ID)."""
        return self._name

    @property
    def version(self) -> str:
        """Application version string."""
        return self._version

    @property
    def description(self) -> str:
        """Short description for CLI help text."""
        return self._description

    @property
    def devices(self) -> Sequence[_DeviceRegistration]:
        """Registered device handlers (read-only view)."""
        return tuple(self._devices)

    @property
    def telemetry_registrations(self) -> Sequence[_TelemetryRegistration]:
        """Registered telemetry handlers (read-only view).

        Named ``telemetry_registrations`` rather than ``telemetry`` to
        avoid shadowing the :meth:`telemetry` registration decorator.
        """
        return tuple(self._telemetry)

    @property
    def commands(self) -> Sequence[_CommandRegistration]:
        """Registered command handlers (read-only view)."""
        return tuple(self._commands)

    @property
    def periodic_registrations(self) -> Sequence[_PeriodicRegistration]:
        """Registered periodic handlers (read-only view)."""
        return tuple(self._periodic)

    @property
    def adapters(self) -> Mapping[type, _AdapterEntry]:
        """Registered adapter entries keyed by port type (read-only view)."""
        return MappingProxyType(self._adapters)

    def registered_names(self) -> frozenset[str]:
        """Collect registered device/telemetry/command/periodic names."""
        return frozenset(
            r.name
            for regs in (self._devices, self._telemetry, self._commands, self._periodic)
            for r in regs
        )

    # --- Registration decorators -------------------------------------------

    def on_configure(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Register a configuration hook called before devices start.

        The hook runs after settings and adapters are resolved but
        before the run-loop.  Parameters are injected by type
        annotation (Settings, adapter ports, Logger, ClockPort).

        Use ``@app.on_configure`` (no parentheses).

        See Also:
            ADR-023 — on_configure lifecycle phase.
        """
        self._configure_hooks.append(func)
        return func

    def state(self, factory: Callable[..., Any]) -> Callable[..., Any]:
        """Register a lifespan-scoped shared-state factory.

        The factory runs once at bootstrap (after settings resolution, before
        adapters enter their lifecycle).  Its return value is registered in the
        DI container by type and injected into any handler that declares the
        corresponding type annotation.

        Factory forms supported (detected from return annotation):

        - ``def f(settings) -> T`` — sync, no teardown.
        - ``def f(settings) -> ContextManager[T]`` — sync context manager.
        - ``async def f(settings) -> AsyncIterator[T]`` — async generator;
          framework enters the generator and exits it on shutdown.
        - ``async def f(settings) -> AsyncContextManager[T]`` — async CM.

        Teardown runs in reverse registration order (LIFO).

        Args:
            factory: Callable returning the state object.  May optionally
                declare one parameter annotated with ``Settings`` or a
                concrete subclass — the framework passes the resolved
                settings as that type.  Zero-parameter factories are valid.

        Raises:
            TypeError: If the factory has no return type annotation.
            TypeError: If the return annotation is not a supported form.
            ValueError: If another factory already returns the same type.

        See Also:
            ADR-039 — @app.state factory.
        """
        from cosalette._state import build_state_registration

        registered_types = {reg.state_type for reg in self._state_factories}
        reg = build_state_registration(factory, registered_types)
        self._state_factories.append(reg)
        return factory

    def device(
        self,
        name: str | NameSpec | None = None,
        *,
        init: Callable[..., Any] | None = None,
        enabled: EnabledSpec = True,
        summary: str | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
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
                topics omit the device segment.  When a
                :data:`NameSpec` callable is provided, the framework
                calls it with the resolved ``Settings``.  Returning
                ``list[str]`` expands the registration into one device
                per name.  Returning ``dict[str, config]`` expands the
                registration into one device per key, and each dict
                value becomes the per-device config injected into the
                handler.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                the handler by type.
            enabled: When ``False``, registration is silently skipped.
                When a callable ``(Settings) -> bool``, the decision
                is deferred to the bootstrap phase after settings
                resolution.  Defaults to ``True``.
            summary: One-line description of the device for documentation
                and manifest output.  Informational only.  Defaults to
                ``None``.
            behavior: List of phrases describing what the device does
                (e.g. ``["polls I2C bus", "publishes state on change"]``).
                Informational only.  Defaults to ``None``.
            effects: List of side effects the device produces
                (e.g. ``["publishes {name}/state"]``).  Informational
                only.  Defaults to ``None``.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            TypeError: If any handler parameter lacks a type annotation.
        """
        # Note: inspect.iscoroutinefunction is used here rather than
        # asyncio.iscoroutinefunction (deprecated in 3.12, removed in 3.16).
        # The asyncio variant additionally checked the legacy _is_coroutine
        # marker used by older frameworks (aiohttp <3.x); that marker is not
        # supported by cosalette so the narrower inspect check is correct.
        if callable(name) and inspect.iscoroutinefunction(name):
            raise TypeError("Use @app.device(), not @app.device (parentheses required)")

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(enabled):
                self._register_deferred_device(
                    func,
                    name,
                    enabled,
                    init,
                    summary=summary,
                    behavior=behavior,
                    effects=effects,
                )
                return func
            effective_name = name if name is not None else _callable_name(func)
            self.add_device(
                effective_name,
                func,
                init=init,
                enabled=enabled,
                is_root=name is None,
                summary=summary,
                behavior=behavior,
                effects=effects,
            )
            return func

        return decorator

    def _register_deferred_device(
        self,
        func: Callable[..., Any],
        name: str | Callable[..., Any] | None,
        enabled: EnabledSpec,
        init: Callable[..., Any] | None,
        *,
        summary: str | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> None:
        """Append a deferred-enabled device registration for *func*."""
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        resolved_name = (
            _callable_qualname(func)
            if callable(name)
            else (name or _callable_name(func))
        )
        name_spec = name if callable(name) else None
        self._devices.append(
            _DeviceRegistration(
                name=resolved_name,
                func=func,
                injection_plan=plan,
                is_root=not callable(name) and name is None,
                enabled_spec=enabled,
                init=init,
                init_injection_plan=init_plan,
                name_spec=name_spec,  # ty: ignore[invalid-argument-type]
                summary=summary,
                behavior=behavior,
                effects=effects,
            ),
        )

    def add_device(
        self,
        name: str | Callable[..., Any],
        func: Callable[..., Awaitable[None]],
        *,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        is_root: bool = False,
        summary: str | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
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
            summary: One-line description of the device for documentation
                and manifest output.  Informational only.  Defaults to
                ``None``.
            behavior: List of phrases describing what the device does.
                Informational only.  Defaults to ``None``.
            effects: List of side effects the device produces.
                Informational only.  Defaults to ``None``.

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
        if not callable(name):
            check_device_name(
                name,
                registry_type="device",
                is_root=is_root,
                devices=self._devices,
                telemetry=self._telemetry,
                commands=self._commands,
            )
        plan = build_injection_plan(func)
        if callable(name):
            self._devices.append(
                _DeviceRegistration(
                    name=_callable_qualname(func),
                    func=func,
                    injection_plan=plan,
                    is_root=is_root,
                    init=init,
                    init_injection_plan=init_plan,
                    name_spec=name,  # ty: ignore[invalid-argument-type]
                    summary=summary,
                    behavior=behavior,
                    effects=effects,
                ),
            )
        else:
            self._devices.append(
                _DeviceRegistration(
                    name=name,
                    func=func,
                    injection_plan=plan,
                    is_root=is_root,
                    init=init,
                    init_injection_plan=init_plan,
                    summary=summary,
                    behavior=behavior,
                    effects=effects,
                ),
            )

    def command(
        self,
        name: str | NameSpec | None = None,
        *,
        init: Callable[..., Any] | None = None,
        enabled: EnabledSpec = True,
        sub: str | None = None,
        sub_key: str = "command",
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
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
                topics omit the device segment.  When a
                :data:`NameSpec` callable is provided, the framework
                calls it with the resolved ``Settings``.  Returning
                ``list[str]`` expands the registration into one command
                per name.  Returning ``dict[str, config]`` expands the
                registration into one command per key, and each dict
                value becomes the per-command config injected into the
                handler.
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into
                the handler by type.
            enabled: When ``False``, registration is silently skipped.
                When a callable ``(Settings) -> bool``, the decision
                is deferred to the bootstrap phase after settings
                resolution.  Defaults to ``True``.
            sub: Optional sub-command value for routing multiple handlers
                on the same topic.  When provided, the JSON payload must
                contain a field (specified by *sub_key*) with this exact
                value for the handler to be invoked.  Multiple handlers
                can share the same topic name if they have different
                *sub* values.
            sub_key: JSON field name used for sub-command routing.
                Defaults to ``"command"``.  Only meaningful when *sub*
                is provided.
            summary: Optional human-readable description of what this
                command does.  Metadata only — does not affect
                runtime behavior.
            state_model: Optional type representing the expected
                device state structure.  Metadata only — does not
                enforce runtime validation but is surfaced in
                introspection.
            payload_model: Optional type representing the expected
                command payload structure.  Metadata only — does not
                enforce runtime validation but is surfaced in
                introspection.
            behavior: Optional list of strings describing the command's
                behavior or operational steps.  Metadata only.
            effects: Optional list of strings describing the side
                effects this command produces.  Metadata only.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            TypeError: If any handler parameter lacks a type annotation.
        """
        # See device() for rationale on inspect vs asyncio.iscoroutinefunction.
        if callable(name) and inspect.iscoroutinefunction(name):
            raise TypeError(
                "Use @app.command(), not @app.command (parentheses required)"
            )

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(enabled):
                self._register_deferred_command(
                    func,
                    name,
                    enabled,
                    init,
                    sub,
                    sub_key,
                    summary,
                    state_model,
                    payload_model,
                    behavior,
                    effects,
                )
                return func
            effective_name = name if name is not None else _callable_name(func)
            self.add_command(
                effective_name,
                func,
                init=init,
                enabled=enabled,
                is_root=name is None,
                sub=sub,
                sub_key=sub_key,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            )
            return func

        return decorator

    def _register_deferred_command(
        self,
        func: Callable[..., Any],
        name: str | Callable[..., Any] | None,
        enabled: EnabledSpec,
        init: Callable[..., Any] | None,
        sub: str | None,
        sub_key: str,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
    ) -> None:
        """Append a deferred-enabled command registration for *func*."""
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func, mqtt_params={"topic", "payload"})
        sig = inspect.signature(func)
        declared_mqtt = frozenset({"topic", "payload"} & sig.parameters.keys())
        resolved_name = (
            _callable_qualname(func)
            if callable(name)
            else (name or _callable_name(func))
        )
        name_spec = name if callable(name) else None
        self._commands.append(
            _CommandRegistration(
                name=resolved_name,
                func=func,
                injection_plan=plan,
                mqtt_params=declared_mqtt,
                is_root=not callable(name) and name is None,
                enabled_spec=enabled,
                init=init,
                init_injection_plan=init_plan,
                name_spec=name_spec,  # ty: ignore[invalid-argument-type]
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
                sub=sub,
                sub_key=sub_key,
            ),
        )

    def add_command(
        self,
        name: str | Callable[..., Any],
        func: Callable[..., Awaitable[dict[str, object] | None]],
        *,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        is_root: bool = False,
        sub: str | None = None,
        sub_key: str = "command",
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
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
            sub: Optional sub-command value for routing multiple handlers
                on the same topic.  When provided, the JSON payload must
                contain a field (specified by *sub_key*) with this exact
                value for the handler to be invoked.
            sub_key: JSON field name used for sub-command routing.
                Defaults to ``"command"``.  Only meaningful when *sub*
                is provided.

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
        if not callable(name):
            check_device_name(
                name,
                registry_type="command",
                is_root=is_root,
                devices=self._devices,
                telemetry=self._telemetry,
                commands=self._commands,
                sub=sub,
                sub_key=sub_key,
            )
        plan = build_injection_plan(func, mqtt_params={"topic", "payload"})
        sig = inspect.signature(func)
        declared_mqtt = frozenset({"topic", "payload"} & sig.parameters.keys())
        if callable(name):
            self._commands.append(
                _CommandRegistration(
                    name=_callable_qualname(func),
                    func=func,
                    injection_plan=plan,
                    mqtt_params=declared_mqtt,
                    is_root=is_root,
                    init=init,
                    init_injection_plan=init_plan,
                    name_spec=name,  # ty: ignore[invalid-argument-type]
                    summary=summary,
                    state_model=state_model,
                    payload_model=payload_model,
                    behavior=behavior,
                    effects=effects,
                    sub=sub,
                    sub_key=sub_key,
                ),
            )
        else:
            self._commands.append(
                _CommandRegistration(
                    name=name,
                    func=func,
                    injection_plan=plan,
                    mqtt_params=declared_mqtt,
                    is_root=is_root,
                    init=init,
                    init_injection_plan=init_plan,
                    summary=summary,
                    state_model=state_model,
                    payload_model=payload_model,
                    behavior=behavior,
                    effects=effects,
                    sub=sub,
                    sub_key=sub_key,
                ),
            )

    def telemetry(
        self,
        name: str | NameSpec | None = None,
        *,
        interval: IntervalSpec | None = None,
        schedule: str | CronSchedule | CronSpec | None = None,
        publish: PublishStrategy | None = None,
        persist: PersistPolicy | None = None,
        init: Callable[..., Any] | None = None,
        enabled: EnabledSpec = True,
        group: str | None = None,
        retry: int = 0,
        retry_on: tuple[type[BaseException], ...] | None = None,
        backoff: BackoffStrategy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        triggerable: bool = False,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
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
                topics omit the device segment.  When a
                :data:`NameSpec` callable is provided, the framework
                calls it with the resolved ``Settings``.  Returning
                ``list[str]`` expands the registration into one
                telemetry device per name.  Returning
                ``dict[str, config]`` expands into one device per key,
                and each dict value becomes the per-device config
                injected into the handler and used for other deferred
                per-device resolution (e.g. callable ``interval=`` and
                ``schedule=``).
            interval: Polling interval in seconds, or a callable
                ``(Settings) -> float`` for deferred resolution.
                Mutually exclusive with ``schedule``.  One of
                ``interval`` or ``schedule`` is required.
            schedule: Cron expression (Quartz format, 6 or 7 fields),
                a :class:`CronSchedule` instance, or a per-device
                callable ``(config) -> str | CronSchedule`` for deferred
                per-device resolution.  The callable form requires
                ``name=callable`` (dict-based multi-device registration)
                and is mutually exclusive with ``interval=`` and
                ``group=``.  The plain expression/instance form is
                mutually exclusive with ``interval=``.  Example:
                ``"0 0/5 * * * ?"`` (every 5 minutes).
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
                When a callable ``(Settings) -> bool``, the decision
                is deferred to the bootstrap phase after settings
                resolution — constraints such as ``persist=`` requiring
                a store are validated only if the device ends up active.
                Defaults to ``True``.
            group: Optional coalescing group name.  Telemetry devices
                in the same group share a single scheduler tick so
                their readings are published together.  When ``None``
                (the default), the device runs on its own independent
                timer.
            retry: Maximum number of retry attempts after a failure.
                Defaults to ``0`` (no retry).  The retry counter
                persists across poll cycles and resets on success.
            retry_on: Exception types to retry on.  Defaults to
                ``(OSError,)`` when ``retry > 0`` and not explicitly
                set.  Exceptions not matching this tuple propagate
                immediately to the error handler.
            backoff: Backoff strategy controlling delay between retries
                (e.g. ``ExponentialBackoff()``, ``LinearBackoff()``,
                ``FixedBackoff()``).  Defaults to
                ``ExponentialBackoff(base=2.0, max_delay=60.0)`` when
                ``retry > 0`` and not explicitly set.
            circuit_breaker: Optional circuit breaker that stops
                retrying after consecutive failed cycles.  Works
                independently of ``retry`` — even with ``retry=0``,
                it tracks per-cycle failures.
            triggerable: When ``True``, the framework subscribes to
                ``{prefix}/{device}/set`` and triggers an immediate
                out-of-cycle execution when a message arrives.  The
                handler runs through the same pipeline as scheduled
                runs.  Requires a named device (not root).  Defaults
                to ``False``.
            summary: Optional human-readable description of what this
                telemetry device measures or reports.  Metadata only —
                does not affect runtime behavior.
            state_model: Optional type representing the expected
                payload structure.  Metadata only — does not enforce
                runtime validation but is surfaced in introspection.
            behavior: Optional list of strings describing the device's
                behavior or operational steps.  Metadata only.
            payload_model: Optional type representing the expected
                command payload structure.  Metadata only — does not
                enforce runtime validation but is surfaced in
                introspection.
            effects: Optional list of strings describing the side
                effects this telemetry might trigger.  Metadata only.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If a second root (unnamed) device is registered.
            ValueError: If *interval* is a float and <= 0.  For
                callable intervals, validation is deferred to
                :meth:`_run_async`.
            ValueError: If both ``interval`` and ``schedule`` are
                provided, or neither is provided.
            ValueError: If ``persist`` is set but no ``store=`` backend
                was configured on the App (when ``enabled`` is a literal
                ``True``; deferred when ``enabled`` is callable).
            ValueError: If *group* is an empty string.
            ValueError: If ``retry > 0`` and ``retry_on`` is
                explicitly empty.
            ValueError: If *schedule* is a callable but *name* is a
                static string (no per-device config available).
            ValueError: If *schedule* is a callable and *group* is set.
            TypeError: If any handler parameter lacks a type annotation.
        """
        if callable(enabled):
            return self._make_deferred_telemetry_decorator(
                name,
                interval,
                schedule,
                publish,
                persist,
                init,
                enabled,
                group,
                retry,
                retry_on,
                backoff,
                circuit_breaker,
                triggerable,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
            )

        # Skip all validation when disabled — a disabled device shouldn't raise.
        if enabled:
            if group is not None and group == "":
                msg = "group must be non-empty"
                raise ValueError(msg)
            schedule_spec, parsed_schedule, effective_interval = (
                self._prepare_schedule_spec(interval, schedule, group)
            )
            if schedule_spec is None:
                # _prepare_schedule_spec returns (None, ...) only when schedule
                # is not callable, so CronSpec is excluded at this point.
                # cast() here is zero-cost: it tells the type-checker that the
                # CronSpec branch is already ruled out by the invariant above.
                _sched = cast("str | CronSchedule | None", schedule)
                self._validate_interval_schedule(interval, _sched, group)
                parsed_schedule = self._parse_schedule(_sched)
            # (add_telemetry re-checks for the imperative path).
            if persist is not None and not self._store_configured:
                msg = (
                    "persist= requires a store= backend on the App. "
                    "Pass store=MemoryStore() (or another Store) to App()."
                )
                raise ValueError(msg)
        else:
            schedule_spec = None
            parsed_schedule = None
            effective_interval = 0.0

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            effective_name = name if name is not None else _callable_name(func)
            self.add_telemetry(
                effective_name,
                func,
                interval=effective_interval,
                schedule=parsed_schedule,
                schedule_spec=schedule_spec,
                publish=publish,
                persist=persist,
                init=init,
                enabled=enabled,
                group=group,
                is_root=name is None,
                retry=retry,
                retry_on=retry_on,
                backoff=backoff,
                circuit_breaker=circuit_breaker,
                triggerable=triggerable,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            )
            return func

        return decorator

    def _make_deferred_telemetry_decorator(
        self,
        name: str | Callable[..., Any] | None,
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        publish: PublishStrategy | None,
        persist: PersistPolicy | None,
        init: Callable[..., Any] | None,
        enabled: EnabledSpec,
        group: str | None,
        retry: int,
        retry_on: tuple[type[BaseException], ...] | None,
        backoff: BackoffStrategy | None,
        circuit_breaker: CircuitBreaker | None,
        triggerable: bool,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
    ) -> Callable[..., Any]:
        """Validate and build a decorator for callable-enabled telemetry."""
        # Defer settings-dependent validation to resolve_enabled().
        # Still validate interval/schedule structure — independent of settings.
        if group is not None and group == "":
            msg = "group must be non-empty"
            raise ValueError(msg)
        self._validate_retry_args(retry, retry_on)
        deferred_schedule_spec, parsed_schedule, effective_interval = (
            self._prepare_schedule_spec(interval, schedule, group)
        )
        if deferred_schedule_spec is None:
            # _prepare_schedule_spec returns (None, ...) only when schedule
            # is not callable, so CronSpec is excluded at this point.
            # cast() here is zero-cost: it tells the type-checker that the
            # CronSpec branch is already ruled out by the invariant above.
            _sched = cast("str | CronSchedule | None", schedule)
            self._validate_interval_schedule(interval, _sched, group)
            parsed_schedule = self._parse_schedule(_sched)
            effective_interval = interval if interval is not None else 0.0
        resolved_retry_on, resolved_backoff = self._resolve_retry_defaults(
            retry, retry_on, backoff
        )

        def decorator_deferred(func: Callable[..., Any]) -> Callable[..., Any]:
            self._build_deferred_telemetry_registration(
                func,
                name,
                effective_interval,
                parsed_schedule,
                publish,
                persist,
                init,
                enabled,
                group,
                retry,
                resolved_retry_on,
                resolved_backoff,
                circuit_breaker,
                triggerable,
                summary,
                state_model,
                payload_model,
                behavior,
                effects,
                deferred_schedule_spec,
            )
            return func

        return decorator_deferred

    def _build_deferred_telemetry_registration(
        self,
        func: Callable[..., Any],
        name: str | Callable[..., Any] | None,
        effective_interval: IntervalSpec,
        parsed_schedule: CronSchedule | None,
        publish: PublishStrategy | None,
        persist: PersistPolicy | None,
        init: Callable[..., Any] | None,
        enabled: EnabledSpec,
        group: str | None,
        retry: int,
        resolved_retry_on: tuple[type[BaseException], ...],
        resolved_backoff: BackoffStrategy | None,
        circuit_breaker: CircuitBreaker | None,
        triggerable: bool,
        summary: str | None,
        state_model: type | None,
        payload_model: type | None,
        behavior: list[str] | None,
        effects: list[str] | None,
        schedule_spec: CronSpec | None = None,
    ) -> None:
        """Append a deferred-enabled telemetry registration for *func*."""
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        resolved_name = (
            _callable_qualname(func)
            if callable(name)
            else (name or _callable_name(func))
        )
        name_spec = name if callable(name) else None
        self._telemetry.append(
            _TelemetryRegistration(
                name=resolved_name,
                func=func,
                injection_plan=plan,
                interval=effective_interval,
                is_root=not callable(name) and name is None,
                enabled_spec=enabled,
                publish_strategy=publish,
                persist_policy=persist,
                init=init,
                init_injection_plan=init_plan,
                group=group,
                name_spec=name_spec,  # ty: ignore[invalid-argument-type]
                retry=retry,
                retry_on=resolved_retry_on,
                backoff=resolved_backoff,
                circuit_breaker=circuit_breaker,
                schedule=parsed_schedule,
                schedule_spec=schedule_spec,
                triggerable=triggerable,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            ),
        )

    @staticmethod
    def _validate_triggerable(
        triggerable: bool,
        name: str | None,
        group: str | None,
        is_root: bool = False,
    ) -> None:
        """Raise ValueError for invalid triggerable combinations."""
        if not triggerable:
            return
        if name is None or is_root:
            msg = "triggerable=True requires a named device (name= must be set)"
            raise ValueError(msg)
        if group is not None:
            msg = (
                "triggerable= and group= cannot be combined"
                " (coalescing groups use a shared scheduler)"
            )
            raise ValueError(msg)

    @staticmethod
    def _parse_schedule(
        schedule: str | CronSchedule | None,
    ) -> CronSchedule | None:
        """Parse a schedule string or pass through a CronSchedule."""
        if isinstance(schedule, str):
            return CronSchedule(schedule)
        if isinstance(schedule, CronSchedule):
            return schedule
        return None

    @staticmethod
    def _prepare_schedule_spec(
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | CronSpec | None,
        group: str | None,
    ) -> tuple[CronSpec | None, CronSchedule | None, IntervalSpec]:
        """Normalise schedule arguments for callable-name telemetry.

        Returns ``(schedule_spec, parsed_schedule, effective_interval)``.
        When *schedule* is a callable (per-device spec), schedule_spec is set and
        parsed_schedule is ``None``; mutual-exclusivity with interval/group is
        validated eagerly.
        """
        if not callable(schedule):
            return None, None, interval if interval is not None else 0.0
        if interval is not None:
            msg = "interval= and schedule= are mutually exclusive"
            raise ValueError(msg)
        if group is not None:
            msg = (
                "schedule= and group= cannot be combined"
                " (coalescing groups require interval=)"
            )
            raise ValueError(msg)
        return schedule, None, 0.0  # ty: ignore[invalid-return-type]

    @staticmethod
    def _validate_interval_schedule(
        interval: IntervalSpec | None,
        schedule: str | CronSchedule | None,
        group: str | None = None,
    ) -> None:
        """Validate interval/schedule mutual exclusivity and group compat."""
        if interval is not None and schedule is not None:
            msg = "interval= and schedule= are mutually exclusive"
            raise ValueError(msg)
        if interval is None and schedule is None:
            msg = "Either interval= or schedule= is required"
            raise ValueError(msg)
        if schedule is not None and group is not None:
            msg = (
                "schedule= and group= cannot be combined"
                " (coalescing groups require interval=)"
            )
            raise ValueError(msg)

    @staticmethod
    def _validate_imperative_schedule(
        interval: IntervalSpec,
        parsed_schedule: CronSchedule | None,
        group: str | None = None,
    ) -> None:
        """Validate interval/schedule mutual exclusivity (imperative path)."""
        has_interval = interval != 0.0 or callable(interval)
        if has_interval and parsed_schedule is not None:
            msg = "interval= and schedule= are mutually exclusive"
            raise ValueError(msg)
        if not has_interval and parsed_schedule is None:
            msg = "Either interval= or schedule= is required"
            raise ValueError(msg)
        if parsed_schedule is not None and group is not None:
            msg = (
                "schedule= and group= cannot be combined"
                " (coalescing groups require interval=)"
            )
            raise ValueError(msg)

    @staticmethod
    def _resolve_retry_defaults(
        retry: int,
        retry_on: tuple[type[BaseException], ...] | None,
        backoff: BackoffStrategy | None,
    ) -> tuple[tuple[type[BaseException], ...], BackoffStrategy | None]:
        """Apply default retry_on and backoff when retry > 0."""
        if retry > 0:
            if retry_on is None:
                retry_on = _DEFAULT_RETRY_ON
            if backoff is None:
                backoff = _DEFAULT_BACKOFF
        return retry_on if retry_on is not None else (), backoff

    def _validate_telemetry_args(
        self,
        name: str | Callable[..., Any],
        interval: IntervalSpec,
        persist: PersistPolicy | None,
        init: Callable[..., Any] | None,
        group: str | None,
        retry: int = 0,
        retry_on: tuple[type[BaseException], ...] | None = None,
        schedule: CronSchedule | None = None,
        schedule_spec: CronSpec | None = None,
    ) -> None:
        if group is not None and group == "":
            msg = "group must be non-empty"
            raise ValueError(msg)
        if persist is not None and not self._store_configured:
            msg = (
                "persist= requires a store= backend on the App. "
                "Pass store=MemoryStore() (or another Store) to App()."
            )
            raise ValueError(msg)
        if init is not None:
            _validate_init(init)
        self._validate_schedule_spec_combinations(schedule_spec, name, group)
        # Skip interval validation when schedule is set (interval is sentinel 0.0)
        if self._interval_is_invalid(schedule, schedule_spec, name, interval):
            msg = f"Telemetry interval must be positive, got {interval}"
            raise ValueError(msg)
        self._validate_retry_args(retry, retry_on)

    @staticmethod
    def _interval_is_invalid(
        schedule: CronSchedule | None,
        schedule_spec: CronSpec | None,
        name: str | Callable[..., Any],
        interval: IntervalSpec,
    ) -> bool:
        has_schedule = schedule is not None or schedule_spec is not None
        is_static_name = not callable(name)
        is_static_interval = not callable(interval)
        return (
            not has_schedule and is_static_name and is_static_interval and interval <= 0  # ty: ignore[unsupported-operator]
        )

    @staticmethod
    def _validate_schedule_spec_combinations(
        schedule_spec: CronSpec | None,
        name: str | Callable[..., Any],
        group: str | None,
        parsed_schedule: CronSchedule | None = None,
    ) -> None:
        if schedule_spec is None:
            return
        if not callable(name):
            msg = (
                "schedule= callable requires name= to be a callable "
                "(per-device dict/list spec).  "
                "Static names have no per-device config to pass to the callable."
            )
            raise ValueError(msg)
        if group is not None:
            msg = "schedule= callable cannot be combined with group="
            raise ValueError(msg)
        if parsed_schedule is not None:
            msg = "schedule_spec= cannot be combined with schedule="
            raise ValueError(msg)

    @staticmethod
    def _validate_retry_args(
        retry: int,
        retry_on: tuple[type[BaseException], ...] | None,
    ) -> None:
        if not isinstance(retry, int) or retry < 0:
            msg = f"retry must be a non-negative integer, got {retry!r}"
            raise ValueError(msg)
        if retry > 0 and retry_on is not None and retry_on == ():
            msg = "retry > 0 with retry_on=() is invalid (nothing would be retried)"
            raise ValueError(msg)
        if retry_on is not None:
            for exc_type in retry_on:
                if not isinstance(exc_type, type) or not issubclass(
                    exc_type, BaseException
                ):
                    msg = f"retry_on elements must be exception types, got {exc_type!r}"
                    raise TypeError(msg)

    def add_telemetry(
        self,
        name: str | Callable[..., Any],
        func: Callable[..., Awaitable[dict[str, object] | None]],
        *,
        interval: IntervalSpec = 0.0,
        schedule: str | CronSchedule | None = None,
        schedule_spec: CronSpec | None = None,
        publish: PublishStrategy | None = None,
        persist: PersistPolicy | None = None,
        init: Callable[..., Any] | None = None,
        enabled: bool = True,
        group: str | None = None,
        is_root: bool = False,
        retry: int = 0,
        retry_on: tuple[type[BaseException], ...] | None = None,
        backoff: BackoffStrategy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        triggerable: bool = False,
        summary: str | None = None,
        state_model: type | None = None,
        payload_model: type | None = None,
        behavior: list[str] | None = None,
        effects: list[str] | None = None,
    ) -> None:
        """Register a telemetry device imperatively.

        This is the imperative counterpart to :meth:`telemetry`.  It
        always creates a *named* (non-root) registration by default.

        Either ``interval`` or ``schedule`` must be provided.  They
        are mutually exclusive.

        Args:
            name: Device name for MQTT topics and logging.
            func: Async callable returning a ``dict`` (published as
                state) or ``None`` (suppresses that cycle).
            interval: Polling interval in seconds, or a callable
                ``(Settings) -> float`` for deferred resolution.
                Mutually exclusive with ``schedule``.
            schedule: Cron expression (Quartz format, 6 or 7 fields)
                or a :class:`CronSchedule` instance.  The handler
                fires at times matching the expression.  Mutually
                exclusive with ``interval``.
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
            schedule_spec: Per-device callable ``(config) -> str | CronSchedule``
                for deferred schedule resolution.  **Internal** — set by the
                :meth:`telemetry` decorator when ``schedule=callable`` is used
                with ``name=callable``; not intended for direct use.  Requires
                ``name=callable``, incompatible with ``schedule=`` and
                ``group=``.
            triggerable: When ``True``, the framework subscribes to
                ``{prefix}/{device}/set`` and triggers an immediate
                out-of-cycle execution when a message arrives.  The
                handler runs through the same pipeline as scheduled
                runs.  Requires a named device (not root).  Defaults
                to ``False``.

        Raises:
            ValueError: If a device with this name is already registered.
            ValueError: If *interval* is a float and <= 0 (when
                ``schedule`` is not set).
            ValueError: If both ``interval`` and ``schedule`` are
                provided (and interval is not the sentinel 0.0), or
                neither is provided.
            ValueError: If *persist* is set but no ``store=`` backend
                was configured on the App.
            ValueError: If *group* is an empty string.
            ValueError: If ``schedule_spec`` is set but ``name`` is a
                static string, or combined with ``group=`` or
                ``schedule=``.
            TypeError: If *init* is async or has un-annotated parameters.
            TypeError: If *func* has un-annotated parameters.

        See Also:
            :meth:`telemetry` — decorator equivalent.
        """
        if not enabled:
            return

        if triggerable and is_root:
            msg = (
                "triggerable= and is_root=True cannot be combined"
                " (no named topic to subscribe to)"
            )
            raise ValueError(msg)
        if not callable(name):
            self._validate_triggerable(triggerable, str(name), group)
        # else: deferred — validated per resolved device in expand_name_specs

        parsed_schedule = self._parse_schedule(schedule)
        if schedule_spec is not None:
            self._validate_schedule_spec_combinations(
                schedule_spec, name, group, parsed_schedule
            )
            # Per-device callable schedule: skip imperative validation;
            # the schedule is resolved during name expansion.
            parsed_schedule = None
        else:
            self._validate_imperative_schedule(interval, parsed_schedule, group)

        self._validate_telemetry_args(
            name,
            interval,
            persist,
            init,
            group,
            retry=retry,
            retry_on=retry_on,
            schedule=parsed_schedule,
            schedule_spec=schedule_spec,
        )
        init_plan = build_injection_plan(init) if init is not None else None
        if not callable(name):
            check_device_name(
                name,
                registry_type="telemetry",
                is_root=is_root,
                devices=self._devices,
                telemetry=self._telemetry,
                commands=self._commands,
            )
        plan = build_injection_plan(func)
        resolved_name = _callable_qualname(func) if callable(name) else name
        name_spec = name if callable(name) else None

        resolved_retry_on, resolved_backoff = self._resolve_retry_defaults(
            retry,
            retry_on,
            backoff,
        )

        self._telemetry.append(
            _TelemetryRegistration(
                name=resolved_name,
                func=func,
                injection_plan=plan,
                interval=interval,
                is_root=is_root,
                publish_strategy=publish,
                persist_policy=persist,
                init=init,
                init_injection_plan=init_plan,
                group=group,
                name_spec=name_spec,  # ty: ignore[invalid-argument-type]
                retry=retry,
                retry_on=resolved_retry_on,
                backoff=resolved_backoff,
                circuit_breaker=circuit_breaker,
                schedule=parsed_schedule,
                schedule_spec=schedule_spec,
                triggerable=triggerable,
                summary=summary,
                state_model=state_model,
                payload_model=payload_model,
                behavior=behavior,
                effects=effects,
            ),
        )

    def periodic(
        self,
        name: str | None = None,
        *,
        interval: IntervalSpec | datetime.timedelta,
        enabled: EnabledSpec = True,
        init: Callable[..., Any] | None = None,
        summary: str | None = None,
        behavior: list[str] | None = None,
    ) -> Callable[..., Any]:
        """Register a background periodic task.

        The decorated coroutine is called at the specified *interval*.
        It runs purely for side-effects — no return value is published.
        Exceptions are logged at ERROR level and the loop continues.

        Parameters are injected by type annotation (Settings, adapter
        ports, Logger, ClockPort, ``@app.state`` instances).

        Args:
            name: Task name for logging.  When ``None``, the function
                name is used.  Must be unique across all registrations.
            interval: Polling interval in seconds, a
                :class:`datetime.timedelta`, or a callable
                ``(Settings) -> float`` for deferred resolution.
                Must be positive.
            enabled: When ``False``, registration is silently skipped.
                Accepts a callable ``(Settings) -> bool`` for deferred
                resolution (same as ``@app.telemetry``).
            init: Optional synchronous factory called once before the
                handler loop.  Its return value is injected into the
                handler by type.
            summary: One-line description for documentation.
            behavior: Phrases describing what the task does.

        Raises:
            ValueError: If a registration with this name already exists.
            ValueError: If *interval* is a literal float/timedelta and
                not positive.
            TypeError: If any handler parameter lacks a type annotation.

        Example::

            @app.periodic("cache-refresh", interval=60)
            async def refresh_cache(cache: CachePort) -> None:
                await cache.refresh()
        """
        # Normalise timedelta to float immediately
        if isinstance(interval, datetime.timedelta):
            interval = interval.total_seconds()

        if callable(enabled):
            # Deferred: store spec, resolve at bootstrap
            def _deferred_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                effective_name = name if name is not None else _callable_name(func)
                _validate_periodic_early(
                    effective_name, self.registered_names(), interval
                )
                if init is not None:
                    _validate_init(init)
                init_plan = build_injection_plan(init) if init is not None else None
                plan = build_injection_plan(func)
                self._periodic.append(
                    _PeriodicRegistration(
                        name=effective_name,
                        func=func,
                        injection_plan=plan,
                        interval=interval,
                        enabled_spec=enabled,
                        init=init,
                        init_injection_plan=init_plan,
                        summary=summary,
                        behavior=behavior,
                    )
                )
                return func

            return _deferred_decorator

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            effective_name = name if name is not None else _callable_name(func)
            self.add_periodic(
                effective_name,
                func,
                interval=interval,
                enabled=enabled,
                init=init,
                summary=summary,
                behavior=behavior,
            )
            return func

        return decorator

    def add_periodic(
        self,
        name: str,
        func: Callable[..., Awaitable[None]],
        *,
        interval: IntervalSpec | datetime.timedelta,
        enabled: bool = True,
        init: Callable[..., Any] | None = None,
        summary: str | None = None,
        behavior: list[str] | None = None,
    ) -> None:
        """Register a background periodic task imperatively.

        Imperative counterpart to :meth:`periodic`.

        Args:
            name: Task name for logging.
            func: Async callable — the periodic handler.
            interval: Polling interval in seconds, a
                :class:`datetime.timedelta`, or a callable
                ``(Settings) -> float`` for deferred resolution.
            enabled: When ``False``, registration is silently skipped.
            init: Optional synchronous init factory.
            summary: One-line description.
            behavior: Phrases describing what the task does.

        Raises:
            ValueError: If a registration with this name already exists.
            ValueError: If *interval* is a literal float/timedelta and
                not positive.
        """
        if isinstance(interval, datetime.timedelta):
            interval = interval.total_seconds()
        if not enabled:
            return
        _validate_periodic_early(name, self.registered_names(), interval)
        if init is not None:
            _validate_init(init)
        init_plan = build_injection_plan(init) if init is not None else None
        plan = build_injection_plan(func)
        self._periodic.append(
            _PeriodicRegistration(
                name=name,
                func=func,
                injection_plan=plan,
                interval=interval,
                init=init,
                init_injection_plan=init_plan,
                summary=summary,
                behavior=behavior,
            )
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

        if self._store_factory is not None:
            self._store = _wiring.resolve_store_factory(
                self._store_factory, resolved_settings, resolved_adapters
            )

        await _wiring.run_configure_hooks(
            self._configure_hooks,
            resolved_settings,
            resolved_adapters,
            resolved_clock,
        )
        _wiring.expand_name_specs(
            self._telemetry, self._devices, self._commands, resolved_settings
        )
        _wiring.resolve_intervals(self._telemetry, resolved_settings)
        _wiring.resolve_intervals_periodic(self._periodic, resolved_settings)
        _wiring.resolve_enabled(
            self._telemetry,
            self._devices,
            self._commands,
            resolved_settings,
            self._store,
            periodic_list=self._periodic,
        )
        _wiring._check_expanded_duplicates(
            self._devices, self._telemetry, self._commands
        )

        # Schema enforcement: validate registrations before MQTT
        schema_registry = await _schema_enforcement.load_and_validate_schema(
            self.registered_names(), resolved_settings, prefix
        )

        mqtt_client = _wiring.create_mqtt(mqtt, resolved_settings, prefix, self._name)

        # Wrap with ValidatingMqttPort if schema enforcement is active
        mqtt_client, _validating_port = _apply_schema_enforcement(
            mqtt_client, schema_registry, prefix, self.registered_names()
        )
        health_reporter, error_publisher = _wiring.create_services(
            mqtt_client, prefix, self._version, resolved_clock
        )

        if isinstance(mqtt_client, MqttLifecycle):
            await mqtt_client.start()

        # Publish initial schema status if validation is active
        await _publish_schema_status(
            mqtt_client, _validating_port, schema_registry, prefix
        )

        shutdown_event = _wiring.install_signal_handlers(shutdown_event)

        try:
            # Detect restartable adapters and manage them outside the stack
            restartable = _adapter_lifecycle.detect_restartable_adapters(
                resolved_adapters
            )
            restartable_ids = {id(a) for a in restartable.values()}
            restartable_adapters = list(
                {id(a): a for a in restartable.values()}.values()
            )

            async with _wiring.enter_state_factories(
                self._state_factories,
                resolved_settings,
                overrides=self._state_overrides,
            ) as state_objects:
                resolved_adapters.update(state_objects)

                async with _adapter_lifecycle.enter_lifecycle_adapters(
                    resolved_adapters, shutdown_event, skip_ids=restartable_ids
                ):
                    entered_restartable = (
                        await _adapter_lifecycle.enter_restartable_adapters(
                            restartable_adapters, shutdown_event
                        )
                    )

                    health_checkables = _adapter_lifecycle.detect_health_checkable(
                        resolved_adapters
                    )

                    # --- Phase 2: Wire ---
                    await _wiring.publish_device_availability(
                        self._all_registrations, health_reporter
                    )

                    await _wiring.publish_registry_snapshot(self, mqtt_client, prefix)

                    contexts = _wiring.build_contexts(
                        self._all_registrations,
                        resolved_settings,
                        mqtt_client,
                        prefix,
                        shutdown_event,
                        resolved_adapters,
                        resolved_clock,
                    )

                    adapter_device_map = _wiring.build_adapter_device_map(
                        self._all_registrations, resolved_adapters
                    )

                    health_check_runner = None
                    if health_checkables and self._health_check_interval is not None:
                        from cosalette._health import HealthCheckRunner

                        health_check_runner = HealthCheckRunner(
                            health_checkables=health_checkables,
                            adapter_device_map=adapter_device_map,
                            health_reporter=health_reporter,
                            clock=resolved_clock,
                            interval=self._health_check_interval,
                            shutdown_event=shutdown_event,
                            restart_after_failures=self._restart_after_failures,
                            max_restarts=self._max_restarts,
                            restart_cooldown=self._restart_cooldown,
                            sustained_health_reset=self._sustained_health_reset,
                        )

                    # Create trigger slots for triggerable telemetry
                    trigger_slots = _wiring.create_trigger_slots(self._telemetry)

                    router = await _wiring.wire_router(
                        self._devices,
                        self._commands,
                        self._store,
                        contexts,
                        prefix,
                        error_publisher,
                        trigger_slots=trigger_slots,
                        telemetry=self._telemetry,
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
                        health_check_runner=health_check_runner,
                        restart_cooldown=self._restart_cooldown,
                        adapter_device_map=adapter_device_map,
                        resolved_clock=resolved_clock,
                        restartable_adapters=entered_restartable,
                        trigger_slots=trigger_slots,
                        periodic=self._periodic,
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
