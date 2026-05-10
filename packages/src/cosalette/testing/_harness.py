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
from typing import TYPE_CHECKING, Any, Self, get_origin

from cosalette._app import App
from cosalette._clock import ClockPort
from cosalette._commands._runner import CommandRunner
from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._json import dumps as _json_dumps
from cosalette._mqtt import MockMqttClient
from cosalette._persistence._stores import DeviceStore, Store
from cosalette._runners._runner_utils import (
    async_create_device_store,
    async_save_store_on_shutdown,
)
from cosalette._runners._stream_primitives import Stream, StreamablePort
from cosalette._runners._stream_runner import _run_stream_handler
from cosalette._settings import Settings
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

    def _make_stream_ctx(
        self,
        name: str,
        reg: Any,
        resolved_adapters: dict[type, object],
        ctx: DeviceContext | None,
    ) -> DeviceContext:
        if ctx is not None:
            return ctx
        topic_prefix = self.settings.mqtt.topic_prefix or self.app._name
        # Exclude stream-source port types so test handlers cannot retrieve
        # the lifecycle-owned port via ctx.adapter(StreamablePort[T]).
        _stream_port_origins = (StreamablePort,)
        filtered = {
            k: v
            for k, v in resolved_adapters.items()
            if get_origin(k) not in _stream_port_origins
        }
        return DeviceContext(
            name=name,
            settings=self.settings,
            mqtt=self.mqtt,
            topic_prefix=topic_prefix,
            shutdown_event=self.shutdown_event,
            adapters=filtered,
            clock=self.clock,
            is_root=reg.is_root,
        )

    async def _make_device_store(
        self,
        name: str,
        store: Store | None,
        providers: dict[type, Any] | None,
    ) -> DeviceStore | None:
        """Create and load a DeviceStore for the stream handler under test.

        Returns ``None`` in two distinct cases:

        - **No store configured**: neither *store* nor ``app._store`` is set,
          so persistence is disabled for this handler.
        - **Store pre-supplied via providers**: *providers* already contains a
          :class:`DeviceStore` key; the caller is responsible for injecting it,
          and this helper opts out to avoid creating a duplicate.
        """
        if providers is not None and DeviceStore in providers:
            return None
        effective = store if store is not None else self.app._store
        if effective is None:
            return None
        return await async_create_device_store(effective, name)

    def _build_inject_providers(
        self,
        name: str,
        ctx: DeviceContext,
        resolved_adapters: dict[type, object],
        device_store: DeviceStore | None,
        providers: dict[type, Any] | None,
    ) -> dict[type, Any]:
        base = _build_stream_providers(
            self.settings, self.app._state_overrides, self.clock, name
        )
        base[DeviceContext] = ctx
        for concrete_type, instance in resolved_adapters.items():
            base[concrete_type] = instance
        if device_store is not None:
            base[DeviceStore] = device_store
        if providers is not None:
            base.update(providers)
        return base

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
        ctx = self._make_stream_ctx(name, reg, resolved_adapters, ctx)
        device_store = await self._make_device_store(name, store, providers)
        base_providers = self._build_inject_providers(
            name, ctx, resolved_adapters, device_store, providers
        )

        try:
            await _run_stream_handler(reg, stream, base_providers, self.app._reactors)
        finally:
            # Retrieve the DeviceStore from final providers — _make_device_store
            # returns None when a pre-supplied store was passed via providers,
            # but in that case base_providers.get(DeviceStore) still returns it.
            await async_save_store_on_shutdown(base_providers.get(DeviceStore), name)

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
        from cosalette._injection import resolve_request_kwargs

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
        kwargs = resolve_request_kwargs(reg.injection_plan, providers)
        await reg.func(**kwargs)

    # -- Convenience API for testing (cos-zo3.5) -------------------------------

    def published(self) -> list[tuple[str, str, bool, int]]:
        """Return a snapshot of all MQTT messages published so far.

        Returns:
            Snapshot list of ``(topic, payload, retain, qos)`` tuples. This
            is a copy — mutating the returned list does not affect the
            :class:`MockMqttClient` internal state.
        """
        return list(self.mqtt.published)

    def messages_for(self, topic: str) -> list[tuple[str, bool, int]]:
        """Return all messages published to *topic*.

        Args:
            topic: MQTT topic filter (exact match only).

        Returns:
            List of ``(payload, retain, qos)`` tuples for the given *topic*.
        """
        return self.mqtt.get_messages_for(topic)

    def last_published(self) -> tuple[str, str, bool, int] | None:
        """Return the most recent MQTT publish, or ``None`` if no publishes.

        Returns:
            ``(topic, payload, retain, qos)`` tuple or ``None``.
        """
        return self.mqtt.published[-1] if self.mqtt.published else None

    def assert_published(
        self,
        topic: str,
        *,
        contains: str | None = None,
        count: int | None = None,
    ) -> None:
        """Assert that *topic* has published messages matching criteria.

        Args:
            topic: MQTT topic to check (exact match).
            contains: Optional substring that must appear in at least one
                payload for *topic*.
            count: Optional exact number of messages that must have been
                published to *topic*.

        Raises:
            AssertionError: If no messages for *topic*, or if *contains* is
                not found in any payload, or if message count doesn't match
                *count*.
        """
        messages = self.messages_for(topic)
        if not messages:
            raise AssertionError(f"No messages published to {topic!r}")
        if count is not None and len(messages) != count:
            raise AssertionError(
                f"Expected {count} message(s) to {topic!r}, got {len(messages)}"
            )
        if contains is not None and not any(
            contains in payload for payload, _, _ in messages
        ):
            raise AssertionError(f"No message to {topic!r} contains {contains!r}")

    async def inject_command(
        self, device: str | None, payload: str, *, topic: str | None = None
    ) -> None:
        """Simulate an inbound MQTT command to *device*.

        Delivers a message to ``{topic_prefix}/{device}/set`` (or
        ``{topic_prefix}/set`` for root commands) via the
        :class:`MockMqttClient`, triggering registered command callbacks.

        This is an MQTT-delivery helper — the app must be running and
        callbacks must be registered for the command to be processed.

        Args:
            device: Device name as registered with ``@app.command``, or
                ``None`` for root commands (matching ``@app.command(None)``
                registration semantics). ``None`` constructs the topic as
                ``{prefix}/set``; any non-empty string constructs
                ``{prefix}/{device}/set``.
            payload: MQTT payload string.
            topic: Optional explicit topic override. When ``None`` (default),
                the topic is constructed from *device*.

        See Also:
            :meth:`call_command` for direct command handler invocation without
            requiring the app to be running.
        """
        if topic is None:
            topic_prefix = self.settings.mqtt.topic_prefix or self.app._name
            topic = f"{topic_prefix}/{device}/set" if device else f"{topic_prefix}/set"
        await self.mqtt.deliver(topic, payload)

    async def call_command(
        self,
        name: str,
        payload: str | dict[str, object],
        *,
        topic: str | None = None,
    ) -> None:
        """Directly invoke a registered ``@app.command`` handler.

        Resolves the handler by *name*, injects dependencies, calls it with
        the deserialized *payload*, and publishes any returned state to
        ``harness.mqtt`` — mirroring production execution without requiring
        the app to be running.

        Supports production request binding including typed Pydantic payloads
        (``Annotated[Model, Payload()]``), ``payload``/``topic``/``message``
        parameters, ``DeviceContext``, and simple DI providers available to
        ``CommandRunner``. Does NOT run adapter lifecycle, state factory
        lifecycle, or reactors.

        Args:
            name: Command handler name as registered with ``@app.command``.
                Supports router-prefixed names like ``"router/sub"``. For
                root commands registered with ``@app.command(None)``, pass
                the function name.
            payload: MQTT payload — either a JSON string or a dict that will
                be serialized to JSON.
            topic: Optional MQTT topic string. When ``None`` (default),
                constructs ``{prefix}/{name}/set`` or ``{prefix}/set`` for
                root commands.

        Raises:
            ValueError: If no command handler with *name* is registered.
            Exception: Any exception raised by the handler is propagated.

        Note:
            For tests requiring adapter lifecycle, state factory lifecycle,
            or reactor dispatch, use :meth:`inject_command` with the app
            running. ``init=`` command callbacks are NOT run; handlers that
            cache ``init`` results will receive ``None`` for those
            dependencies. Reactor dispatch is disabled; if the handler
            triggers side-effects via reactors, use :meth:`inject_command`
            with the app running instead.

        See Also:
            :meth:`inject_command` for MQTT-delivery simulation requiring the
            app to be running.
        """
        topic_prefix = self.settings.mqtt.topic_prefix or self.app._name

        # Find the command registration
        try:
            reg = next(r for r in self.app._commands if r.name == name)
        except StopIteration:
            msg = f"No command handler named '{name}' found"
            raise ValueError(msg) from None

        # Construct topic if not provided
        if topic is None:
            if reg.is_root:
                topic = f"{topic_prefix}/set"
            else:
                topic = f"{topic_prefix}/{name}/set"

        # Serialize payload using the project's JSON backend (orjson) for
        # consistency with production encoding behaviour.
        payload_str = _json_dumps(payload) if isinstance(payload, dict) else payload

        # Build DeviceContext for command execution
        ctx = DeviceContext(
            name=name,
            settings=self.settings,
            mqtt=self.mqtt,
            topic_prefix=topic_prefix,
            shutdown_event=self.shutdown_event,
            adapters={},
            clock=self.clock,
            is_root=reg.is_root,
        )

        # Create CommandRunner and execute (reactors=None skips reactor dispatch)
        cmd_runner = CommandRunner(store=self.app._store)
        error_publisher = ErrorPublisher(
            mqtt=self.mqtt,
            topic_prefix=topic_prefix,
        )

        await cmd_runner.run_command(
            reg=reg,
            ctx=ctx,
            topic=topic,
            payload=payload_str,
            error_publisher=error_publisher,
            reactors=None,
        )

    async def advance_time(self, seconds: float) -> None:
        """Advance test clock by *seconds*, yielding to event loop.

        Convenience wrapper over ``await harness.clock.sleep(seconds)``.

        Args:
            seconds: Time delta to advance.
        """
        await self.clock.sleep(seconds)

    async def run_stream(
        self,
        func: Any,
        adapters: dict[type, Any],
        *,
        shutdown: asyncio.Event | None = None,
    ) -> None:
        """Run a stream handler's full lifecycle (open → scan → close).

        Constructs a minimal :class:`_StreamRegistration` from *func*, then
        calls :func:`run_stream` with the provided *adapters*.  Useful for
        testing stream handler behaviour without wiring a full app.

        Args:
            func: The async-generator stream handler to run.
            adapters: Resolved adapter map keyed by port type
                (e.g. ``{StreamablePort[Item]: my_port_instance}``).
            shutdown: Optional :class:`asyncio.Event` to trigger graceful
                shutdown.  Defaults to :attr:`shutdown_event`.
        """
        from cosalette._injection import build_injection_plan
        from cosalette._registration import _StreamRegistration
        from cosalette._runners._stream_runner import run_stream as _run_stream

        plan = build_injection_plan(func)
        reg = _StreamRegistration(
            name="test_stream",
            func=func,
            injection_plan=plan,
            enabled_spec=True,
            summary=None,
            behavior=None,
            effects=None,
        )
        await _run_stream(
            reg, adapters, {}, shutdown if shutdown is not None else self.shutdown_event
        )
