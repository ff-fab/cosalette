"""Lifecycle mixin for the App class."""

from __future__ import annotations

import abc
import asyncio
import collections.abc
import contextlib
import itertools
import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from cosalette._app import App
    from cosalette._registration import _ReactorRegistration

from cosalette import _wiring
from cosalette._app._helpers import _apply_schema_enforcement, _publish_schema_status
from cosalette._app._store_defaults import (
    _default_store_is_ephemeral,
    _normalize_env_name,
    _resolve_default_store_path,
)
from cosalette._clock import ClockPort, SystemClock
from cosalette._context import DeviceContext
from cosalette._health import HealthReporter
from cosalette._logging import configure_logging
from cosalette._mqtt import MqttLifecycle, MqttPort
from cosalette._persistence._stores import Store
from cosalette._registration import LifespanFunc
from cosalette._schema import _enforcement as _schema_enforcement
from cosalette._settings import Settings
from cosalette._wiring import _adapter_lifecycle

logger = logging.getLogger(__name__)


class _LifecycleMixin:
    """Mixin for lifecycle-related App methods."""

    # Attributes injected by App.__init__
    _name: str
    _version: str
    _dry_run: bool
    _heartbeat_interval: float | None
    _health_check_interval: float | None
    _restart_after_failures: int
    _max_restarts: int
    _restart_cooldown: float
    _sustained_health_reset: float
    _devices: list
    _telemetry: list
    _commands: list
    _streams: list
    _periodic: list
    _reactors: list[_ReactorRegistration]
    _state_factories: list
    _state_overrides: dict
    _adapters: dict
    _configure_hooks: list
    _store_factory: collections.abc.Callable[..., Store] | None
    _store: Store | None
    _store_is_default: bool
    _retained_cleanup: bool | None
    _entity_set_is_dynamic: bool | None
    _settings: Settings | None
    _settings_class: type[Settings]
    _lifespan: LifespanFunc

    @property
    @abc.abstractmethod
    def registered_names(self) -> frozenset[str]: ...

    @property
    @abc.abstractmethod
    def _all_registrations(self) -> list: ...

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

        cli = build_cli(cast("App", self))
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

        # Must be called before run_configure_hooks / expand_name_specs:
        # _resolve_cleanup_store scans and caches the dynamism predicate so
        # any later call to _has_dynamic_entity_set returns the correct result
        # even after expand_name_specs clears name_spec fields.
        self._warn_if_ephemeral_default_store()
        _cleanup_store = self._resolve_cleanup_store()

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
        _wiring.resolve_timeouts(self._telemetry, resolved_settings)
        _wiring.resolve_intervals_periodic(self._periodic, resolved_settings)
        _wiring.resolve_enabled(
            self._telemetry,
            self._devices,
            self._commands,
            resolved_settings,
            self._store,
            periodic_list=self._periodic,
            stream_list=self._streams,
        )
        _wiring._check_expanded_duplicates(
            self._devices, self._telemetry, self._commands
        )

        # Schema enforcement: validate registrations before MQTT
        schema_registry = await _schema_enforcement.load_and_validate_schema(
            self.registered_names, resolved_settings, prefix
        )

        mqtt_client = _wiring.create_mqtt(mqtt, resolved_settings, prefix, self._name)

        # Wrap with ValidatingMqttPort if schema enforcement is active
        mqtt_client, _validating_port = _apply_schema_enforcement(
            mqtt_client, schema_registry, prefix, self.registered_names
        )
        health_reporter, error_publisher = _wiring.create_services(
            mqtt_client,
            prefix,
            self._version,
            resolved_clock,
            error_publish_verbose=resolved_settings.mqtt.error_publish_verbose,
        )

        connect_aware = _wiring.register_connect_reannounce(
            mqtt_client,
            cast("App", self),
            health_reporter,
            self._all_registrations,
            prefix,
            _cleanup_store,
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
                    await _wiring.publish_startup_snapshot(
                        cast("App", self),
                        mqtt_client,
                        health_reporter,
                        self._all_registrations,
                        prefix,
                        _cleanup_store,
                        connect_aware=connect_aware,
                    )

                    contexts = _wiring.build_contexts(
                        self._all_registrations,
                        resolved_settings,
                        mqtt_client,
                        prefix,
                        shutdown_event,
                        resolved_adapters,
                        resolved_clock,
                        health_reporter=health_reporter,
                    )

                    stream_contexts = _wiring.build_stream_contexts(
                        self._streams,
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

                    # Build trigger config snapshot for triggerable telemetry
                    trigger_config = _wiring.TriggerConfig.build(self._telemetry)

                    # self._store — persist= paths always use the full store;
                    # unrelated to ADR-049 static-app gate
                    router = await _wiring.wire_router(
                        self._devices,
                        self._commands,
                        self._store,
                        contexts,
                        prefix,
                        error_publisher,
                        trigger_config=trigger_config,
                        reactors=self._reactors,
                    )

                    await _wiring.subscribe_and_connect(mqtt_client, router)

                    # --- Phase 3: Run ---
                    eager_startup = not connect_aware
                    # self._store — persist= paths always use the full store;
                    # unrelated to ADR-049 static-app gate
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
                        trigger_slots=trigger_config.slots,
                        periodic=self._periodic,
                        stream_list=self._streams,
                        stream_contexts=stream_contexts,
                        reactors=self._reactors,
                        publish_initial_heartbeat=eager_startup,
                    )
        finally:
            await health_reporter.shutdown()

            if isinstance(mqtt_client, MqttLifecycle):
                await mqtt_client.stop()

        logger.info("Shutdown complete")

    def _has_dynamic_entity_set(self) -> bool:
        """True when this app's entity set may vary by config across restarts.

        Must be called before :func:`_wiring.expand_name_specs` for a correct
        result — after that step ``name_spec`` fields are cleared.  The result
        is cached on first call, so post-expand callers automatically receive
        the pre-expand value.

        ``True`` when any registration uses a callable ``name=`` or
        callable ``enabled=``, or any ``@app.on_configure`` hook is
        registered.  Conservative: any configure hook returns ``True``
        so config-driven apps are never silently left un-warned.

        .. note::
            Import-time config-derived registrations (e.g. a device
            name resolved from an env-var at module level, outside any
            callable) are indistinguishable from static names — they
            will be classified as static.  Use a callable ``name=`` or
            ``@app.on_configure`` to ensure dynamic classification.
        """
        # Return cached result on subsequent calls (cache is populated below
        # on first evaluation, before expand_name_specs clears name_spec fields).
        if self._entity_set_is_dynamic is not None:
            return self._entity_set_is_dynamic
        if self._configure_hooks:
            self._entity_set_is_dynamic = True
            return True
        # _all_registrations covers devices/telemetry/commands only;
        # streams/periodic carry no config-removable retained topics (ADR-048).
        result = any(
            # callable(True/False) is False — bool has no __call__
            reg.name_spec is not None or callable(reg.enabled_spec)
            for reg in itertools.chain(self._devices, self._telemetry, self._commands)
        )
        self._entity_set_is_dynamic = result
        return result

    def _cleanup_enabled(self) -> bool:
        """Whether ADR-048 retained-topic cleanup should run for this app.

        Honors an explicit ``retained_cleanup=`` override; otherwise falls
        back to the auto-heuristic: cleanup runs when the entity set may vary
        by config (see :meth:`_has_dynamic_entity_set`) or when the store was
        explicitly chosen by the author (not the auto-resolved default).
        Returns ``False`` when no store is configured (``store=None``
        opt-out) — there is no store to hold the ADR-048 snapshot.
        """
        if self._retained_cleanup is not None:
            return self._retained_cleanup
        if self._store is None and self._store_factory is None:
            return False
        return self._has_dynamic_entity_set() or not self._store_is_default

    def _resolve_cleanup_store(self) -> Store | None:
        """Return the store used for ADR-048 retained-topic cleanup, or None.

        Returns ``self._store`` when cleanup is enabled for this app
        (:meth:`_cleanup_enabled`); ``None`` when it is skipped — a static
        auto-default app, or an explicit ``retained_cleanup=False`` opt-out —
        so no snapshot I/O runs and no ``store.json`` is created unless
        ``persist=`` is also used.
        """
        # Ensure the structural heuristic is evaluated and cached now (pre-expand)
        # so post-expand callers of has_dynamic_entities get the correct value
        # regardless of any retained_cleanup= override.  _cleanup_enabled() may
        # short-circuit without calling _has_dynamic_entity_set() when an explicit
        # override is set, so we call it here unconditionally.
        self._has_dynamic_entity_set()
        return self._store if self._cleanup_enabled() else None

    def _warn_if_ephemeral_default_store(self) -> None:
        """Warn once at startup if the auto-resolved default store is ephemeral.

        See ADR-049: an auto-default store on a container's ephemeral filesystem
        (no <NAME>_STORE_PATH) will not survive restarts.  Fires only when
        retained-topic cleanup is enabled for the app (:meth:`_cleanup_enabled`)
        — the auto-heuristic spares provably-static apps, and an explicit
        ``retained_cleanup=False`` suppresses it; ``retained_cleanup=True``
        forces it on an ephemeral default store.
        """
        if not (self._store_is_default and _default_store_is_ephemeral(self._name)):
            return
        if not self._cleanup_enabled():
            return
        logger.warning(
            "Using an auto-resolved default store at %s, which is ephemeral "
            "inside a container - retained-topic cleanup (ADR-048) will not "
            "survive restarts. Set %s_STORE_PATH to a path on a mounted volume "
            "for durable persistence.",
            _resolve_default_store_path(self._name),
            _normalize_env_name(self._name),
        )

    def _resolve_intervals(self, settings: Settings) -> None:
        """Resolve any callable intervals to concrete floats.

        Delegates to :func:`_wiring.resolve_intervals`.
        """
        _wiring.resolve_intervals(self._telemetry, settings)

    def _resolve_timeouts(self, settings: Settings) -> None:
        """Resolve callable timeouts and apply auto-defaults.

        Must be called after :meth:`_resolve_intervals` so that concrete
        interval values are available for the auto-default computation.
        Delegates to :func:`_wiring.resolve_timeouts`.
        """
        _wiring.resolve_timeouts(self._telemetry, settings)

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
