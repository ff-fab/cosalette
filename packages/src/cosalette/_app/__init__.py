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
    async def sensor(ctx: cosalette.DeviceContext):
        while not ctx.shutdown_requested:
            await ctx.publish_state({"value": read_sensor()})
            yield  # reaction boundary
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
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast, override

from pydantic import ValidationError

from cosalette._app._adapter import _AdapterMixin
from cosalette._app._asyncapi import _AsyncapiMixin
from cosalette._app._command import _CommandMixin
from cosalette._app._configure import _ConfigureMixin
from cosalette._app._device import _DeviceMixin
from cosalette._app._discovery import _DiscoveryMixin
from cosalette._app._helpers import _validate_positive_interval
from cosalette._app._lifecycle import _LifecycleMixin
from cosalette._app._periodic import _PeriodicMixin
from cosalette._app._store_defaults import (
    _create_default_store,
    _resolve_default_store_path,
)
from cosalette._app._stream import _StreamMixin
from cosalette._app._telemetry import _TelemetryMixin
from cosalette._context import DeviceContext as DeviceContext
from cosalette._persistence._state import StateRegistration
from cosalette._persistence._stores import Store
from cosalette._registration import (
    _UNSET,
    _CommandRegistration,
    _DeviceRegistration,
    _noop_lifespan,
    _ReactorRegistration,
    _StreamRegistration,
    _TelemetryRegistration,
    _Unset,
    process_adapters_dict,
    validate_mqtt_name,
)
from cosalette._registration import (
    LifespanFunc as LifespanFunc,
)
from cosalette._registration_views import _RegistrationViewsMixin
from cosalette._runners._periodic import _PeriodicRegistration
from cosalette._runners._telemetry_runner import _to_ms as _to_ms
from cosalette._settings import Settings
from cosalette._settings._config_file import SettingsLoadError
from cosalette._wiring._adapter_lifecycle import _AdapterEntry
from cosalette._wiring._discovery import DiscoveryConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from cosalette._router import Router


def _validate_error_type_map(
    error_type_map: dict[type[Exception], str] | None,
) -> dict[type[Exception], str]:
    """Validate and copy the app-provided ``error_type_map``.

    Keys must be :class:`Exception` classes and values ``error_type`` strings.
    A wrong key (an instance instead of a class, or a non-exception type) would
    silently never match at publish time — exactly the kind of quiet degradation
    LEAK-01 guards against — so reject it loudly at construction.  The check is
    ``Exception`` (not ``BaseException``) to match the declared type and the
    publisher, which only ever handles ``except Exception``; a
    ``BaseException``-only key (e.g. ``KeyboardInterrupt``) could never match and
    would be dead config.
    """
    if not error_type_map:
        return {}
    for key, value in error_type_map.items():
        if not (isinstance(key, type) and issubclass(key, Exception)):
            msg = f"error_type_map keys must be exception classes, got {key!r}"
            raise TypeError(msg)
        if not isinstance(value, str):
            msg = f"error_type_map values must be strings, got {value!r}"
            raise TypeError(msg)
    return dict(error_type_map)


class App(
    _RegistrationViewsMixin,
    _ConfigureMixin,
    _DeviceMixin,
    _CommandMixin,
    _TelemetryMixin,
    _StreamMixin,
    _PeriodicMixin,
    _AdapterMixin,
    _LifecycleMixin,
    _AsyncapiMixin,
    _DiscoveryMixin,
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
        store: Store | Callable[..., Store] | None | _Unset = _UNSET,
        retained_cleanup: bool | None = None,
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
        error_type_map: dict[type[Exception], str] | None = None,
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
            store: Persistence backend for device state.  When omitted,
                the framework auto-creates a :class:`JsonFileStore` at
                a default path derived from *name* — see ADR-049 for
                the full resolution order (``<NAME>_STORE_PATH`` env,
                then ``$XDG_STATE_HOME``, then ``~/.local/state``).
                Pass ``store=None`` to opt out of all persistence.
                Pass an explicit :class:`Store` instance or a
                ``Callable[..., Store]`` factory to override.
            retained_cleanup: Tri-state override for ADR-048 retained-topic
                cleanup.  ``None`` (default) leaves the auto-heuristic in
                charge: cleanup runs when the entity set may vary by config
                or an explicit ``store=`` was passed (see
                :attr:`has_dynamic_entities`).  ``False`` disables cleanup
                entirely — the cleanup store resolves to ``None`` so no
                snapshot I/O runs — and suppresses the ephemeral-store
                warning for an auto-default store on an ephemeral filesystem.
                ``True`` forces cleanup on and the ephemeral-store warning
                fires for an auto-default store on an ephemeral filesystem,
                even for provably-static apps.  When combined with
                ``store=None``, ``True`` is a graceful no-op (there is no
                store to hold the ADR-048 snapshot).
                See ADR-048 (orphaned retained-topic cleanup) and ADR-049
                (default store path resolution and the opt-out).
            adapters: Optional mapping of port types to adapter
                implementations.  Each key is a Protocol type; each
                value is either a single implementation (class,
                lazy-import string, or factory callable) or a
                ``(impl, dry_run)`` tuple.  Entries are registered via
                :meth:`adapter` and coexist with later imperative calls.
            error_type_map: Optional mapping from app-owned exception types to
                machine-readable ``error_type`` strings.  Registering a type
                opts it back into full-message error publishing under the
                LEAK-01 default-deny: an exception whose type is in the map has
                its ``str(error)`` published on the broker-visible error topic,
                while unlisted (downstream/unexpected) exceptions keep having
                their message redacted to the class name.  Framework command
                exceptions remain authoritative and cannot be overridden.  See
                ADR-011.

                Security note: mapping a type is a **message-disclosure
                decision**, not just labeling.  Only register exceptions whose
                messages are guaranteed free of secrets, filesystem paths,
                hostnames, or credentials — exception text frequently embeds
                URLs with userinfo or absolute paths.
        """
        validate_mqtt_name(name)
        if not name.strip():
            # The app name is the MQTT topic root prefix and is emitted as the
            # channel-level ``x-cosalette-app`` tag (ADR-033).  An empty/blank
            # name would yield ``/device/state`` topics and an ``x-cosalette-app:
            # ''`` that the schema loader rejects on read-back — breaking schema
            # enforcement closed.  Reject it at the source.
            msg = f"App name must be a non-empty, non-blank string, got {name!r}"
            raise ValueError(msg)
        self._name = name
        self._version = version
        self._description = description
        self._settings_class = settings_class
        try:
            self._settings: Settings | None = settings_class()
        except ValidationError, SettingsLoadError:
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
        self._reactors: list[_ReactorRegistration] = []
        self._state_factories: list[StateRegistration] = []
        self._state_overrides: dict[type, Any] = {}  # for tests
        self._adapters: dict[type, _AdapterEntry] = {}
        self._store_factory: Callable[..., Store] | None = None
        self._store: Store | None = None
        self._store_is_default = False
        self._entity_set_is_dynamic: bool | None = None
        self._retained_cleanup = retained_cleanup
        self._discovery: DiscoveryConfig | None = None
        self._error_type_map = _validate_error_type_map(error_type_map)
        self._apply_store_arg(store)
        self._configure_hooks: list[Callable[..., Any]] = []

        def _register(
            pt: type,
            impl: type | str | Callable[..., object],
            dry_run: type | str | Callable[..., object] | None,
        ) -> None:
            self.adapter(pt, impl, dry_run=dry_run)

        process_adapters_dict(adapters, _register)

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

    @override
    @property
    def _store_configured(self) -> bool:
        """True when a concrete store or factory is wired (including the
        auto-resolved default). False only when *store=None* was passed
        explicitly.

        Note: returns True even when only a callable factory is wired
        (pre-bootstrap, before the factory is resolved).  Use
        ``store is not None`` for external callers that need a concrete
        instance."""
        return self._store is not None or self._store_factory is not None

    @property
    def store(self) -> Store | None:
        """The configured store backend, or ``None`` when explicitly opted out.

        Returns the concrete :class:`~cosalette.Store` instance used for
        ADR-048 retained-topic cleanup and ``DeviceStore`` persistence.

        Returns ``None`` when ``store=None`` was passed explicitly to
        :class:`App`.  For apps using the auto-resolved default (``store=``
        omitted), returns the :class:`~cosalette.JsonFileStore` instance
        created at the default XDG path.

        Note:
            When a callable factory is passed as ``store=``, the concrete
            instance is only available after :meth:`run` or :meth:`cli` is
            called (the factory is resolved at bootstrap, not at construction
            time).  Before that, this property returns ``None``.

        See Also:
            :attr:`store_is_default` — whether the store was auto-resolved.
            ADR-049 — default store path resolution.
        """
        return self._store

    @property
    def store_is_default(self) -> bool:
        """``True`` when the store was auto-resolved by the framework.

        Returns ``False`` when the caller explicitly passed ``store=None``,
        ``store=<instance>``, or ``store=<factory>`` to :class:`App`.
        Returns ``True`` only when ``store=`` was omitted and the framework
        created a default :class:`~cosalette.JsonFileStore` at the
        ``$XDG_STATE_HOME/<name>/store.json`` path (ADR-049).

        Use this to branch on whether the app has an explicitly configured
        store versus the framework default — useful in both production
        conditional logic and test assertions.

        See Also:
            :attr:`store` — the store instance itself.
            ADR-049 — default store path resolution.
        """
        return self._store_is_default

    @property
    def retained_cleanup(self) -> bool | None:
        """The explicit ADR-048 retained-topic cleanup override, or ``None``.

        Returns the value passed as ``retained_cleanup=`` to :class:`App`:
        ``True`` forces cleanup on, ``False`` opts out (and suppresses the
        ephemeral-store warning), ``None`` (default) leaves the framework's
        auto-heuristic in charge (see :attr:`has_dynamic_entities`).

        See Also:
            :attr:`store_is_default` — whether the store was auto-resolved.
            ADR-048 — orphaned retained-topic cleanup.
            ADR-049 — default store path resolution and the opt-out.
        """
        return self._retained_cleanup

    @property
    def has_dynamic_entities(self) -> bool:
        """``True`` when the app's entity set can vary between runs.

        Returns ``True`` when any handler uses a callable ``name=``, callable
        ``enabled=``, or an ``@app.on_configure`` hook is registered — the
        entity set is config-driven and may shrink, so ADR-048 retained-topic
        cleanup is meaningful.

        Returns ``False`` for apps whose entity set is fixed in code (static
        string ``name=``, literal ``enabled=True/False``, no configure hooks).

        This predicate is computed from registration metadata and is stable
        from construction time through the full app lifecycle.

        Note:
            Import-time config-derived names (e.g. a device name read from an
            env-var at module level rather than via a callable) are
            indistinguishable from static names and will return ``False``.

        See Also:
            ADR-049 — entity-set classification and its effects on store I/O.
            :attr:`store` — the store used for ADR-048 cleanup when dynamic.
            :attr:`retained_cleanup` — override that can suppress cleanup
            regardless of this structural predicate.
        """
        return self._has_dynamic_entity_set()

    def _apply_store_arg(
        self, store: Store | Callable[..., Store] | None | _Unset
    ) -> None:
        """Apply the *store=* constructor argument.

        ``_UNSET`` triggers default-path resolution; ``None`` opts out;
        a concrete :class:`Store` instance is used directly; a callable
        is stored as a deferred factory (resolved at bootstrap).
        """
        if store is _UNSET:
            self._store = _create_default_store(_resolve_default_store_path(self._name))
            self._store_is_default = True
            return
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
    def settings_class(self) -> type[Settings]:
        """The Settings subclass used to instantiate this App's settings.

        Returns the concrete type passed (or defaulted) at construction time,
        available before the App is started — useful for structural wiring tests.
        """
        return self._settings_class

    @property
    def state_factories(self) -> tuple[StateRegistration, ...]:
        """Registered @app.state factory descriptors (read-only snapshot)."""
        return tuple(self._state_factories)

    @property
    def error_type_map(self) -> dict[type[Exception], str]:
        """App-registered exception → ``error_type`` map (read-only copy).

        The framework merges this with its own (authoritative) command-exception
        map when building the ErrorPublisher; this property returns only the
        app-provided entries.  See ADR-011.
        """
        return dict(self._error_type_map)

    @override
    @property
    def registered_names(self) -> frozenset[str]:
        """All registered device/telemetry/command/periodic/stream names."""
        all_regs = (
            self._devices,
            self._telemetry,
            self._commands,
            self._periodic,
            self._streams,
        )
        return frozenset(r.name for regs in all_regs for r in regs)

    def _accumulate_tags(
        self,
        router_tags: list[str],
        include_tags: list[str],
        operation_tags: tuple[str, ...],
    ) -> list[str]:
        """Accumulate tags from router, include_router, and operation.

        Order: router constructor → include_router → operation.
        Deduplicate while preserving first occurrence.
        """
        return list(dict.fromkeys([*router_tags, *include_tags, *operation_tags]))

    def _merge_include_adapters(
        self,
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
        | None,
    ) -> None:
        """Merge adapters passed to include_router into app registry."""

        def _register(
            pt: type,
            impl: type | str | Callable[..., object],
            dry_run: type | str | Callable[..., object] | None,
        ) -> None:
            self.adapter(pt, impl, dry_run=dry_run)

        process_adapters_dict(adapters, _register)

    def _merge_router_adapters(self, router: Router) -> None:
        """Merge router's own adapters into app registry."""
        for port_type, entry in router._adapters.items():
            if port_type in self._adapters:
                msg = (
                    f"Adapter conflict: port type {port_type!r} is already "
                    f"registered on the app"
                )
                raise ValueError(msg)
            self._adapters[port_type] = entry

    def _copy_standard_registrations(
        self,
        router: Router,
        combined_prefix: str | None,
        router_tags: list[str],
        include_tags: list[str],
        existing_names: set[str],
    ) -> None:
        """Copy standard registrations (devices, telemetry, commands, streams)."""
        for reg in router._devices:
            transformed = self._transform_registration(
                reg, combined_prefix, router_tags, include_tags
            )
            if transformed.name in existing_names:
                msg = f"Name {transformed.name!r} is already registered on the app"
                raise ValueError(msg)
            existing_names.add(transformed.name)
            self._devices.append(cast(_DeviceRegistration, transformed))

        for reg in router._telemetry:
            transformed = self._transform_registration(
                reg, combined_prefix, router_tags, include_tags
            )
            if transformed.name in existing_names:
                msg = f"Name {transformed.name!r} is already registered on the app"
                raise ValueError(msg)
            existing_names.add(transformed.name)
            self._telemetry.append(cast(_TelemetryRegistration, transformed))

        for reg in router._commands:
            transformed = self._transform_registration(
                reg, combined_prefix, router_tags, include_tags
            )
            if transformed.name in existing_names:
                msg = f"Name {transformed.name!r} is already registered on the app"
                raise ValueError(msg)
            existing_names.add(transformed.name)
            self._commands.append(cast(_CommandRegistration, transformed))

        for reg in router._streams:
            transformed = self._transform_registration(
                reg, combined_prefix, router_tags, include_tags
            )
            if transformed.name in existing_names:
                msg = f"Name {transformed.name!r} is already registered on the app"
                raise ValueError(msg)
            existing_names.add(transformed.name)
            self._streams.append(cast(_StreamRegistration, transformed))

    def _merge_reactors(self, router: Router) -> None:
        """Merge reactors with validation that state_type is registered."""
        registered_types = {r.state_type for r in self._state_factories}
        for reg in router._reactors:
            if reg.state_type not in registered_types:
                msg = (
                    f"Router reactor for state type "
                    f"{reg.state_type.__qualname__!r} cannot be included: "
                    f"type is not registered via @app.state. "
                    f"Register the state factory on the app before calling "
                    f"include_router."
                )
                raise ValueError(msg)
            self._reactors.append(reg)

    def include_router(
        self,
        router: Router,
        *,
        prefix: str | None = None,
        tags: list[str] | None = None,
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
        """Include a router's registrations in this application.

        Applies snapshot semantics: registrations are captured at call time.
        Later mutations to the router do not affect prior inclusions.
        Multiple inclusions with different prefixes are allowed.

        Args:
            router: Router instance to include.
            prefix: Optional single MQTT topic segment prepended to all
                router operation names.  Must not contain ``/``, ``+``,
                ``#``, or NUL.  Combined with router's own prefix.
            tags: Additional tags applied to all router operations.
                Accumulates in order: router constructor → include_router → operation.
            adapters: Adapter declarations merged into the app's registry.
                Same shape as ``App(adapters=...)``.  Conflicts (same port
                type already registered) raise ValueError at include time.

        Raises:
            ValueError: If *prefix* contains MQTT special characters.
            ValueError: If an adapter port type conflict is detected.

        See Also:
            ADR-044 — Public Router and composition API.

        Example::

            # sensors.py
            router = cosalette.Router(prefix="sensors")

            @router.telemetry("temperature", interval=30)
            async def read_temperature() -> dict:
                return {"celsius": 22.5}

            # main.py
            app = cosalette.App("bridge")
            app.include_router(router, tags=["production"])
            # → publishes to: bridge/sensors/temperature/state
        """
        if prefix is not None:
            validate_mqtt_name(prefix)

        # Compute combined prefix
        combined_prefix = self._compute_combined_prefix(router._prefix, prefix)

        # Merge adapters first (fail fast on conflicts)
        self._merge_include_adapters(adapters)
        self._merge_router_adapters(router)

        # Include tags: accumulate router constructor → include_router → operation
        include_tags = list(tags) if tags is not None else []

        # Snapshot existing names for collision detection across all types
        existing_names: set[str] = set(self.registered_names)

        # Copy standard registrations with prefix/tag transformations
        self._copy_standard_registrations(
            router, combined_prefix, router._tags, include_tags, existing_names
        )

        # Handle periodic registrations: apply prefix, accumulate tags, check collisions
        for reg in router._periodic:
            accumulated_tags = self._accumulate_tags(
                router._tags, include_tags, reg.tags
            )
            new_name = self._apply_prefix(reg.name, combined_prefix)
            if new_name in existing_names:
                msg = f"Name {new_name!r} is already registered on the app"
                raise ValueError(msg)
            existing_names.add(new_name)
            new_reg = replace(reg, name=new_name, tags=tuple(accumulated_tags))
            self._periodic.append(new_reg)

        # Merge reactors with validation
        self._merge_reactors(router)

    def _compute_combined_prefix(
        self, router_prefix: str | None, include_prefix: str | None
    ) -> str | None:
        """Compute the combined prefix from router and include_router."""
        if router_prefix is None and include_prefix is None:
            return None
        if router_prefix is None:
            return include_prefix
        if include_prefix is None:
            return router_prefix
        # Both present: combine with slash
        return f"{include_prefix}/{router_prefix}"

    def _transform_registration(
        self,
        reg: (
            _DeviceRegistration
            | _TelemetryRegistration
            | _CommandRegistration
            | _StreamRegistration
        ),
        combined_prefix: str | None,
        router_tags: list[str],
        include_tags: list[str],
    ) -> (
        _DeviceRegistration
        | _TelemetryRegistration
        | _CommandRegistration
        | _StreamRegistration
    ):
        """Transform a registration with prefix and tag accumulation.

        Returns a new registration with transformed name and tags.
        """
        # Apply prefix transformation to name
        new_name = self._apply_prefix(reg.name, combined_prefix)

        # Accumulate tags: router constructor → include_router → operation
        accumulated_tags = self._accumulate_tags(router_tags, include_tags, reg.tags)

        # Create new registration with transformed name and accumulated tags
        # Use dataclass replace to preserve all other fields
        new_reg = replace(reg, name=new_name, tags=tuple(accumulated_tags))

        return new_reg

    def _apply_prefix(self, name: str, prefix: str | None) -> str:
        """Apply prefix to an operation name."""
        if prefix is None:
            return name
        # For non-root operations, prefix the name
        return f"{prefix}/{name}"
