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

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from cosalette._adapter_lifecycle import _AdapterEntry
from cosalette._app._adapter import _AdapterMixin
from cosalette._app._command import _CommandMixin
from cosalette._app._configure import _ConfigureMixin
from cosalette._app._device import _DeviceMixin
from cosalette._app._helpers import _validate_positive_interval
from cosalette._app._lifecycle import _LifecycleMixin
from cosalette._app._periodic import _PeriodicMixin
from cosalette._app._stream import _StreamMixin
from cosalette._app._telemetry import _TelemetryMixin
from cosalette._context import DeviceContext as DeviceContext
from cosalette._periodic import _PeriodicRegistration
from cosalette._registration import (
    LifespanFunc as LifespanFunc,
)
from cosalette._registration import (
    _CommandRegistration,
    _DeviceRegistration,
    _noop_lifespan,
    _StreamRegistration,
    _TelemetryRegistration,
    validate_mqtt_name,
)
from cosalette._settings import Settings
from cosalette._state import StateRegistration
from cosalette._stores import Store
from cosalette._telemetry_runner import _to_ms as _to_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable


class App(
    _ConfigureMixin,
    _DeviceMixin,
    _CommandMixin,
    _TelemetryMixin,
    _StreamMixin,
    _PeriodicMixin,
    _AdapterMixin,
    _LifecycleMixin,
):
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
        self._streams: list[_StreamRegistration] = []
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
        settings = self._settings
        if settings is None:
            msg = (
                "Settings could not be instantiated at construction time "
                "(missing required fields?). Ensure required environment "
                "variables are set, or use app.cli() with --env-file."
            )
            raise RuntimeError(msg)
        return settings

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
        all_regs = (
            self._devices,
            self._telemetry,
            self._commands,
            self._periodic,
            self._streams,
        )
        return frozenset(r.name for regs in all_regs for r in regs)
