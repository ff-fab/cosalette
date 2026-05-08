"""Test harness wrapping App with pre-configured test doubles.

Provides :class:`AppHarness` — a one-liner setup for integration-style
tests that eliminates the boilerplate of creating App, MockMqttClient,
FakeClock, Settings, and an ``asyncio.Event`` individually.

See Also:
    ADR-007 for testing strategy decisions.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from cosalette._app import App
from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._mqtt import MockMqttClient
from cosalette._persistence._stores import DeviceStore, Store
from cosalette._runners._runner_utils import create_device_store, save_store_on_shutdown
from cosalette._runners._stream_runner import _run_stream_handler
from cosalette._settings import Settings
from cosalette._stream import Stream
from cosalette.testing._clock import FakeClock
from cosalette.testing._settings import make_settings

if TYPE_CHECKING:
    from cosalette._app import LifespanFunc


async def _stream_auto_shutdown(stream: Stream[Any]) -> None:
    """Background task: drain queue then signal stream done."""
    while not stream._queue.empty():
        await asyncio.sleep(0)
    await asyncio.sleep(0)  # let asyncio.wait return last item
    stream.shutdown()


def _build_stream_providers(
    settings: Settings,
    state_overrides: dict[type, Any],
    clock: ClockPort,
    stream_name: str,
) -> dict[type, Any]:
    """Build the DI providers dict for a stream handler invocation."""
    providers: dict[type, Any] = {}
    for cls in type(settings).__mro__:
        if isinstance(cls, type) and issubclass(cls, Settings):
            providers[cls] = settings
    providers.update(state_overrides)
    providers[ClockPort] = clock
    providers[logging.Logger] = logging.getLogger(f"cosalette.stream.{stream_name}")
    return providers


@dataclass
class AppHarness:
    """Test harness wrapping App with pre-configured test doubles.

    Provides unified access to App, MockMqttClient, FakeClock,
    Settings, and a shutdown Event — eliminating boilerplate in
    integration-style tests.

    Usage::

        harness = AppHarness.create()

        @harness.app.device("sensor")
        async def sensor(ctx):
            ...

        # Run with auto-shutdown after device_called event:
        await harness.run()

    See Also:
        ADR-007 for testing strategy decisions.
    """

    app: App
    mqtt: MockMqttClient
    clock: FakeClock
    settings: Settings
    shutdown_event: asyncio.Event
    run_periodic: bool = False

    @classmethod
    def create(
        cls,
        *,
        name: str = "testapp",
        version: str = "1.0.0",
        dry_run: bool = False,
        lifespan: LifespanFunc | None = None,
        store: Store | None = None,
        run_periodic: bool = False,
        **settings_overrides: Any,
    ) -> Self:
        """Create a harness with fresh test doubles.

        Args:
            name: App name.
            version: App version.
            dry_run: When True, forward to App for dry-run adapter variants.
            lifespan: Optional lifespan context manager forwarded to
                :class:`App`.
            store: Optional :class:`Store` backend for device persistence.
            run_periodic: When True, periodic tasks will be started; when False,
                they will be suppressed for testing.
            **settings_overrides: Forwarded to :func:`make_settings`.

        Returns:
            A fully wired :class:`AppHarness` ready for test use.
        """
        return cls(
            app=App(
                name=name,
                version=version,
                dry_run=dry_run,
                lifespan=lifespan,
                store=store,
            ),
            mqtt=MockMqttClient(),
            clock=FakeClock(),
            settings=make_settings(**settings_overrides),
            shutdown_event=asyncio.Event(),
            run_periodic=run_periodic,
        )

    async def run(self) -> None:
        """Run ``_run_async`` with the harness's test doubles."""
        periodic_backup = list(self.app._periodic)
        streams_backup = list(self.app._streams)
        if not self.run_periodic:
            self.app._periodic = []
        # Always suppress streams in harness.run() — use inject_stream() instead
        self.app._streams = []
        try:
            await self.app._run_async(
                settings=self.settings,
                shutdown_event=self.shutdown_event,
                mqtt=self.mqtt,
                clock=self.clock,
            )
        finally:
            self.app._periodic = periodic_backup
            self.app._streams = streams_backup

    def trigger_shutdown(self) -> None:
        """Signal the shutdown event."""
        self.shutdown_event.set()

    async def inject_stream(
        self,
        name: str,
        *items: Any,
        shutdown: bool = True,
        ctx: DeviceContext | None = None,
        store: Store | None = None,
        providers: dict[type, Any] | None = None,
        adapters: dict[type, object] | None = None,
    ) -> None:
        """Push items into a named stream handler for testing.

        Finds the registered @app.stream handler by name, creates a Stream,
        pushes the provided items, optionally signals shutdown, and runs the
        handler directly (bypassing adapter lifecycle).

        Args:
            name: Stream handler name as registered with @app.stream.
            *items: Items to push into the stream.
            shutdown: When True (default), call stream.shutdown() after all
                items are pushed so the handler's async for loop terminates.
            ctx: Optional :class:`DeviceContext` override.  When ``None``
                (default), a context is constructed from the harness doubles
                (mqtt, settings, clock, shutdown_event) so handlers can call
                ``ctx.publish_state`` etc. and assertions use
                ``harness.mqtt.published``.
            store: Optional :class:`Store` backend for persistence.  When
                ``None``, falls back to ``app._store`` if configured.  The
                harness creates a :class:`DeviceStore` keyed by *name*,
                loads it before the handler runs, and saves it afterward.
            providers: Extra DI providers merged into the provider map with
                the highest priority (override everything else).
            adapters: Concrete adapter instances injected by their concrete
                type into both the DI provider map and the
                :class:`DeviceContext` adapters dict.  Allows stream handlers
                to access adapters for non-lifecycle operations without
                running the hardware lifecycle (open/start_scan).

        Note:
            When *ctx* is supplied it replaces the entire :class:`DeviceContext`
            — harness doubles (mqtt, clock, shutdown_event) are not merged in.
            *adapters* are added to the DI providers map but **not** injected
            into the explicitly supplied *ctx*.  If you need both a custom
            context and adapter injection, build the context with the adapters
            you need and pass both *ctx* and *adapters*.

        Raises:
            ValueError: If no stream handler with *name* is registered.
            TypeError: If a required dependency (e.g. :class:`DeviceStore`)
                cannot be resolved from the provider map.
        """
        try:
            reg = next(r for r in self.app._streams if r.name == name)
        except StopIteration:
            msg = f"No stream handler named '{name}' found"
            raise ValueError(msg) from None

        stream: Stream[Any] = Stream()
        for item in items:
            stream.put(item)

        if shutdown:
            asyncio.create_task(
                _stream_auto_shutdown(stream), name=f"inject-shutdown:{name}"
            )

        resolved_adapters: dict[type, object] = dict(adapters) if adapters else {}

        if ctx is None:
            topic_prefix = self.settings.mqtt.topic_prefix or self.app._name
            ctx = DeviceContext(
                name=name,
                settings=self.settings,
                mqtt=self.mqtt,
                topic_prefix=topic_prefix,
                shutdown_event=self.shutdown_event,
                adapters=resolved_adapters,
                clock=self.clock,
                is_root=reg.is_root,
            )

        # Only auto-create a DeviceStore if providers won't supply one.
        # This avoids saving a stale store that the handler never receives.
        providers_has_store = providers is not None and DeviceStore in providers
        effective_store_backend = store if store is not None else self.app._store
        device_store: DeviceStore | None = None
        if effective_store_backend is not None and not providers_has_store:
            device_store = create_device_store(effective_store_backend, name)

        base_providers = _build_stream_providers(
            self.settings, self.app._state_overrides, self.clock, name
        )
        base_providers[DeviceContext] = ctx
        for concrete_type, instance in resolved_adapters.items():
            base_providers[concrete_type] = instance
        if device_store is not None:
            base_providers[DeviceStore] = device_store
        if providers is not None:
            base_providers.update(providers)

        # After all merges, save whatever DeviceStore is in the final map.
        final_device_store: DeviceStore | None = base_providers.get(DeviceStore)

        try:
            await _run_stream_handler(reg, stream, base_providers, self.app._reactors)
        finally:
            save_store_on_shutdown(final_device_store, name)

    def override_state(self, state_type: type, instance: Any) -> None:
        """Override a @app.state factory with a pre-built test double.

        Bypasses the factory entirely; *instance* is injected directly
        into the DI container at bootstrap.  Call before :meth:`run`.

        Args:
            state_type: The type returned by the factory (the DI key).
            instance: The test double to inject.

        Raises:
            TypeError: If *instance* is not an instance of *state_type*.
        """
        if not isinstance(instance, state_type):
            raise TypeError(
                f"override_state: expected an instance of {state_type.__name__!r}, "
                f"got {type(instance).__name__!r}"
            )
        self.app._state_overrides[state_type] = instance

    async def tick_periodic(self, name: str) -> None:
        """Invoke one cycle of the named periodic handler (bypasses interval).

        Directly calls the handler's function with injected arguments —
        skips the asyncio sleep so you can test the handler logic
        without waiting for the interval.

        Args:
            name: The periodic task name as registered with ``@app.periodic``.

        Raises:
            ValueError: if no periodic task with *name* exists.
        """
        from cosalette._injection import resolve_kwargs

        try:
            reg = next(r for r in self.app._periodic if r.name == name)
        except StopIteration:
            msg = f"No periodic task named '{name}' found"
            raise ValueError(msg) from None

        # Build a provider map matching production _build_periodic_providers:
        # settings under every Settings base class, clock, logger, state overrides
        providers: dict[type, Any] = {}
        settings = self.settings
        for cls in type(settings).__mro__:
            if isinstance(cls, type) and issubclass(cls, Settings):
                providers[cls] = settings
        providers[ClockPort] = self.clock
        providers[logging.Logger] = logging.getLogger(f"cosalette.periodic.{name}")
        providers.update(self.app._state_overrides)
        kwargs = resolve_kwargs(reg.injection_plan, providers)
        await reg.func(**kwargs)
