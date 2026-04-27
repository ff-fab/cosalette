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
from cosalette._mqtt import MockMqttClient
from cosalette._settings import Settings
from cosalette._stores import Store
from cosalette.testing._clock import FakeClock
from cosalette.testing._settings import make_settings

if TYPE_CHECKING:
    from cosalette._app import LifespanFunc


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
        self, name: str, *items: Any, shutdown: bool = True
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

        Raises:
            ValueError: If no stream handler with *name* is registered.
        """
        from typing import get_origin

        from cosalette._injection import resolve_kwargs
        from cosalette._stream import Stream

        try:
            reg = next(r for r in self.app._streams if r.name == name)
        except StopIteration:
            msg = f"No stream handler named '{name}' found"
            raise ValueError(msg) from None

        stream: Stream[Any] = Stream()
        for item in items:
            stream.put(item)
        if shutdown:
            stream.shutdown()

        # Build providers
        providers: dict[type, Any] = {}
        for cls in type(self.settings).__mro__:
            if isinstance(cls, type) and issubclass(cls, Settings):
                providers[cls] = self.settings
        providers.update(self.app._state_overrides)

        # Build kwargs: stream param directly, everything else from providers
        stream_kwargs: dict[str, Any] = {}
        for param_name, annotation in reg.injection_plan:
            if get_origin(annotation) is Stream:
                stream_kwargs[param_name] = stream
                break
        non_stream_plan = [
            (n, a) for n, a in reg.injection_plan if get_origin(a) is not Stream
        ]
        other_kwargs = resolve_kwargs(non_stream_plan, providers)
        kwargs = {**other_kwargs, **stream_kwargs}
        await reg.func(**kwargs)

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
