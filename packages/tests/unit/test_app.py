"""Tests for cosalette._app — App orchestrator.

Test Techniques Used:
    - Decorator-based Registration: Verify device/telemetry/hook decorators
    - Specification-based Testing: Duplicate names, invalid intervals
    - Integration Testing: Full _run_async lifecycle with injected mocks
    - Async Coordination: asyncio.Event for deterministic test control
    - Mock-based Isolation: MockMqttClient + FakeClock avoid real I/O
    - Error Isolation: Verify crashed devices don't crash the App
    - Command Routing: Simulate MQTT messages via MockMqttClient.deliver()
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

import pytest

from cosalette._app import App
from cosalette._context import AppContext, DeviceContext
from cosalette._mqtt import MqttClient, MqttPort
from cosalette._settings import MqttSettings, Settings
from cosalette.testing import FakeClock, MockMqttClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> App:
    """Minimal App instance for registration tests."""
    return App(name="testapp", version="1.0.0")


# mock_mqtt and fake_clock fixtures provided by cosalette.testing._plugin


# ---------------------------------------------------------------------------
# TestDeviceDecorator
# ---------------------------------------------------------------------------


class TestDeviceDecorator:
    """@app.device decorator registration tests.

    Technique: Specification-based Testing — verifying that the
    decorator records registrations and rejects duplicates.
    """

    async def test_registers_device_function(self, app: App) -> None:
        """@app.device('name') stores a _DeviceRegistration internally."""

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None: ...

        assert len(app._devices) == 1
        assert app._devices[0].name == "sensor"
        assert app._devices[0].func is sensor

    async def test_returns_original_function(self, app: App) -> None:
        """Decorator returns the original function unchanged (transparent)."""

        async def sensor(ctx: DeviceContext) -> None: ...

        result = app.device("sensor")(sensor)
        assert result is sensor

    async def test_duplicate_device_name_raises(self, app: App) -> None:
        """Registering two devices with the same name raises ValueError."""

        @app.device("blind")
        async def blind1(ctx: DeviceContext) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.device("blind")
            async def blind2(ctx: DeviceContext) -> None: ...

    async def test_duplicate_across_device_and_telemetry_raises(self, app: App) -> None:
        """A device name can't collide with an existing telemetry name."""

        @app.telemetry("sensor", interval=10)
        async def sensor_telem(ctx: DeviceContext) -> dict:
            return {}

        with pytest.raises(ValueError, match="already registered"):

            @app.device("sensor")
            async def sensor_dev(ctx: DeviceContext) -> None: ...

    async def test_multiple_distinct_devices(self, app: App) -> None:
        """Multiple devices with distinct names all register successfully."""

        @app.device("blind")
        async def blind(ctx: DeviceContext) -> None: ...

        @app.device("window")
        async def window(ctx: DeviceContext) -> None: ...

        assert len(app._devices) == 2
        names = {d.name for d in app._devices}
        assert names == {"blind", "window"}


# ---------------------------------------------------------------------------
# TestTelemetryDecorator
# ---------------------------------------------------------------------------


class TestTelemetryDecorator:
    """@app.telemetry decorator registration tests.

    Technique: Specification-based Testing — verifying registration
    storage, interval validation, and duplicate detection.
    """

    async def test_registers_telemetry_function(self, app: App) -> None:
        """@app.telemetry stores a _TelemetryRegistration with interval."""

        @app.telemetry("temp", interval=30)
        async def temp(ctx: DeviceContext) -> dict:
            return {"celsius": 22.5}

        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "temp"
        assert app._telemetry[0].interval == 30
        assert app._telemetry[0].func is temp

    async def test_returns_original_function(self, app: App) -> None:
        """Decorator returns the original function unchanged."""

        async def temp(ctx: DeviceContext) -> dict:
            return {}

        result = app.telemetry("temp", interval=5)(temp)
        assert result is temp

    async def test_duplicate_name_raises(self, app: App) -> None:
        """Duplicate telemetry name raises ValueError."""

        @app.telemetry("temp", interval=10)
        async def temp1(ctx: DeviceContext) -> dict:
            return {}

        with pytest.raises(ValueError, match="already registered"):

            @app.telemetry("temp", interval=20)
            async def temp2(ctx: DeviceContext) -> dict:
                return {}

    async def test_zero_interval_raises(self, app: App) -> None:
        """Interval of zero raises ValueError at decoration time."""
        with pytest.raises(ValueError, match="positive"):

            @app.telemetry("temp", interval=0)
            async def temp(ctx: DeviceContext) -> dict:
                return {}

    async def test_negative_interval_raises(self, app: App) -> None:
        """Negative interval raises ValueError at decoration time."""
        with pytest.raises(ValueError, match="positive"):

            @app.telemetry("temp", interval=-5)
            async def temp(ctx: DeviceContext) -> dict:
                return {}


# ---------------------------------------------------------------------------
# TestLifecycleHooks
# ---------------------------------------------------------------------------


class TestLifecycleHooks:
    """@app.on_startup / @app.on_shutdown registration tests.

    Technique: Specification-based Testing — verifying that hooks are
    collected and the original function is returned.
    """

    async def test_startup_hook_registered(self, app: App) -> None:
        """@app.on_startup appends to the startup hooks list."""

        @app.on_startup
        async def setup(ctx: AppContext) -> None: ...

        assert len(app._startup_hooks) == 1
        assert app._startup_hooks[0] is setup

    async def test_shutdown_hook_registered(self, app: App) -> None:
        """@app.on_shutdown appends to the shutdown hooks list."""

        @app.on_shutdown
        async def teardown(ctx: AppContext) -> None: ...

        assert len(app._shutdown_hooks) == 1
        assert app._shutdown_hooks[0] is teardown

    async def test_multiple_hooks_preserve_order(self, app: App) -> None:
        """Multiple hooks of the same type preserve registration order."""

        @app.on_startup
        async def first(ctx: AppContext) -> None: ...

        @app.on_startup
        async def second(ctx: AppContext) -> None: ...

        assert app._startup_hooks == [first, second]


# ---------------------------------------------------------------------------
# TestAdapterRegistration
# ---------------------------------------------------------------------------


@runtime_checkable
class _DummyPort(Protocol):
    """Dummy protocol for adapter registration tests."""

    def do_thing(self) -> str: ...


class _DummyImpl:
    """Concrete adapter for testing."""

    def do_thing(self) -> str:
        return "real"


class _DummyDryRun:
    """Dry-run adapter for testing."""

    def do_thing(self) -> str:
        return "dry"


class TestAdapterRegistration:
    """app.adapter() registration tests.

    Technique: Specification-based Testing — verifying adapter storage,
    duplicate rejection, and dry-run variant capture.
    """

    async def test_registers_adapter(self, app: App) -> None:
        """app.adapter() stores an _AdapterEntry for the port type."""
        app.adapter(_DummyPort, _DummyImpl)
        assert _DummyPort in app._adapters
        assert app._adapters[_DummyPort].impl is _DummyImpl

    async def test_duplicate_port_type_raises(self, app: App) -> None:
        """Registering the same port type twice raises ValueError."""
        app.adapter(_DummyPort, _DummyImpl)
        with pytest.raises(ValueError, match="already registered"):
            app.adapter(_DummyPort, _DummyImpl)

    async def test_dry_run_variant_stored(self, app: App) -> None:
        """dry_run parameter is preserved in the adapter entry."""
        app.adapter(_DummyPort, _DummyImpl, dry_run=_DummyDryRun)
        entry = app._adapters[_DummyPort]
        assert entry.impl is _DummyImpl
        assert entry.dry_run is _DummyDryRun


# ---------------------------------------------------------------------------
# TestRunAsync — integration tests
# ---------------------------------------------------------------------------


class TestRunAsync:
    """Full _run_async lifecycle integration tests.

    Technique: Integration Testing with injected mocks — every test
    provides Settings, MockMqttClient, FakeClock, and a manual
    shutdown_event so no real I/O or signal handlers are involved.
    """

    async def test_device_function_runs(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Device function is called during _run_async.

        Coordination: device sets an event, helper task waits for it
        then triggers shutdown.
        """
        app = App(name="testapp", version="1.0.0")
        device_called = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            device_called.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_called.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert device_called.is_set()

    async def test_telemetry_function_polls_and_publishes(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Telemetry function is called and its return value published as state.

        Coordination: telemetry sets an event on first call, helper
        triggers shutdown so the loop doesn't run forever.
        """
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        @app.telemetry("temp", interval=1)
        async def temp(ctx: DeviceContext) -> dict:
            called.set()
            return {"celsius": 22.5}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await called.wait()
            # Give time for publish_state to complete
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert called.is_set()
        # The state should have been published to testapp/temp/state
        state_messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(state_messages) >= 1
        payload_str = state_messages[0][0]
        assert "22.5" in payload_str

    async def test_startup_hook_runs(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Startup hook is called with an AppContext during _run_async.

        Coordination: hook sets an event, helper triggers shutdown.
        """
        app = App(name="testapp", version="1.0.0")
        hook_called = asyncio.Event()
        received_ctx: list[AppContext] = []

        @app.on_startup
        async def setup(ctx: AppContext) -> None:
            received_ctx.append(ctx)
            hook_called.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await hook_called.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert hook_called.is_set()
        assert len(received_ctx) == 1
        assert isinstance(received_ctx[0], AppContext)

    async def test_shutdown_hook_runs(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Shutdown hook is called with an AppContext on shutdown.

        Coordination: trigger shutdown immediately, verify hook ran
        after _run_async completes.
        """
        app = App(name="testapp", version="1.0.0")
        hook_called = asyncio.Event()

        @app.on_shutdown
        async def teardown(ctx: AppContext) -> None:
            hook_called.set()

        shutdown = asyncio.Event()
        # Trigger shutdown on next event-loop tick
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert hook_called.is_set()

    async def test_device_error_isolation(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A device that raises is caught; the app doesn't crash.

        The error is published via ErrorPublisher and the app continues
        until shutdown.  Technique: verify _run_async completes normally
        despite the exception.
        """
        app = App(name="testapp", version="1.0.0")
        crashed = asyncio.Event()

        @app.device("bad_sensor")
        async def bad_sensor(ctx: DeviceContext) -> None:
            crashed.set()
            msg = "sensor exploded"
            raise RuntimeError(msg)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await crashed.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        # Should NOT raise — error is isolated
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Error should have been published to testapp/error
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1
        assert "sensor exploded" in error_messages[0][0]

    async def test_telemetry_error_resilience(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Telemetry that raises once continues polling on next cycle.

        Technique: first call raises, second call succeeds.
        Verify both the error publication and the successful state
        publication.
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        success = asyncio.Event()

        @app.telemetry("flaky", interval=0.01)
        async def flaky(ctx: DeviceContext) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = "transient failure"
                raise RuntimeError(msg)
            success.set()
            return {"ok": True}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await success.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert call_count >= 2
        # Error published for first failure
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1
        # State published for second success
        state_messages = mock_mqtt.get_messages_for("testapp/flaky/state")
        assert len(state_messages) >= 1

    async def test_command_routing(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Register device with on_command, simulate MQTT message, verify handler.

        Technique: device registers a command handler via ctx.on_command;
        a helper task delivers a message through MockMqttClient.deliver();
        the router dispatches it to the handler via the proxy.
        """
        app = App(name="testapp", version="1.0.0")
        received_command = asyncio.Event()
        received_payloads: list[str] = []

        @app.device("blind")
        async def blind(ctx: DeviceContext) -> None:
            @ctx.on_command
            async def handle(topic: str, payload: str) -> None:
                received_payloads.append(payload)
                received_command.set()

            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        shutdown = asyncio.Event()

        async def simulate_command() -> None:
            # Give the device time to start and register its handler
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/blind/set", "OPEN")
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(simulate_command())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert received_command.is_set()
        assert received_payloads == ["OPEN"]

    async def test_adapter_resolution_in_device(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Registered adapter is available via DeviceContext.adapter().

        Technique: register adapter before run, verify device can
        resolve it at runtime.
        """
        app = App(name="testapp", version="1.0.0")
        resolved_adapter: list[object] = []
        device_done = asyncio.Event()

        app.adapter(_DummyPort, _DummyImpl)

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            adapter = ctx.adapter(_DummyPort)
            resolved_adapter.append(adapter)
            device_done.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_done.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert len(resolved_adapter) == 1
        assert isinstance(resolved_adapter[0], _DummyImpl)

    async def test_dry_run_adapter_swap(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """dry_run=True resolves the dry-run adapter variant.

        Technique: create App with dry_run=True, register adapter
        with a dry_run variant, verify the device gets the dry-run
        instance.
        """
        app = App(name="testapp", version="1.0.0", dry_run=True)
        resolved_adapter: list[object] = []
        device_done = asyncio.Event()

        app.adapter(_DummyPort, _DummyImpl, dry_run=_DummyDryRun)

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            adapter = ctx.adapter(_DummyPort)
            resolved_adapter.append(adapter)
            device_done.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_done.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert len(resolved_adapter) == 1
        assert isinstance(resolved_adapter[0], _DummyDryRun)

    async def test_multiple_devices_run_concurrently(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Two registered devices both run as concurrent tasks.

        Technique: each device sets its own event; both events
        must be set before shutdown triggers.
        """
        app = App(name="testapp", version="1.0.0")
        device_a_ran = asyncio.Event()
        device_b_ran = asyncio.Event()

        @app.device("alpha")
        async def alpha(ctx: DeviceContext) -> None:
            device_a_ran.set()

        @app.device("beta")
        async def beta(ctx: DeviceContext) -> None:
            device_b_ran.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_a_ran.wait()
            await device_b_ran.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert device_a_ran.is_set()
        assert device_b_ran.is_set()

    async def test_health_reporter_publishes_availability(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Devices are registered with the health reporter on startup.

        Technique: verify that availability messages are published
        for each registered device before the device function starts.
        """
        app = App(name="testapp", version="1.0.0")
        device_done = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            device_done.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_done.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # HealthReporter publishes "online" to availability topic
        avail_messages = mock_mqtt.get_messages_for(
            "testapp/sensor/availability",
        )
        assert any(payload == "online" for payload, _, _ in avail_messages)

    async def test_graceful_shutdown_sequence(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """After shutdown, devices complete and health reporter publishes offline.

        Technique: register a device that loops, trigger shutdown,
        verify the device task was cancelled and health reporter
        published offline status.
        """
        app = App(name="testapp", version="1.0.0")
        device_started = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            device_started.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_started.wait()
            await asyncio.sleep(0.02)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Health reporter shutdown publishes "offline" to availability
        avail_messages = mock_mqtt.get_messages_for(
            "testapp/sensor/availability",
        )
        offline = [p for p, _, _ in avail_messages if p == "offline"]
        assert len(offline) >= 1

        # Health reporter also publishes "offline" to status topic
        status_messages = mock_mqtt.get_messages_for("testapp/status")
        offline_status = [p for p, _, _ in status_messages if p == "offline"]
        assert len(offline_status) >= 1

    async def test_mqtt_subscriptions_for_devices(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Command topics are subscribed for each registered device.

        Technique: register two devices, verify mqtt.subscribe was
        called for each ``{prefix}/{device}/set`` topic.
        """
        app = App(name="testapp", version="1.0.0")
        both_started = asyncio.Event()
        started_count = 0

        @app.device("blind")
        async def blind(ctx: DeviceContext) -> None:
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        @app.device("window")
        async def window(ctx: DeviceContext) -> None:
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await both_started.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=Settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert "testapp/blind/set" in mock_mqtt.subscriptions
        assert "testapp/window/set" in mock_mqtt.subscriptions


# ---------------------------------------------------------------------------
# TestAdapterFactoryCallable — factory callable support
# ---------------------------------------------------------------------------


class TestAdapterFactoryCallable:
    """app.adapter() with factory callable support.

    Technique: Specification-based Testing — verifying that factory
    callables (non-type callables) are accepted and invoked during
    adapter resolution, complementing class-based registration.
    """

    async def test_factory_callable_registration(self, app: App) -> None:
        """A lambda returning an adapter instance is accepted and resolved."""
        app.adapter(_DummyPort, lambda: _DummyImpl())

        resolved = app._resolve_adapters()
        assert isinstance(resolved[_DummyPort], _DummyImpl)

    async def test_factory_callable_with_constructor_args(self, app: App) -> None:
        """Factory callable can pass constructor arguments to the adapter."""

        class PinAdapter:
            def __init__(self, pin: int) -> None:
                self.pin = pin

            def do_thing(self) -> str:
                return f"pin-{self.pin}"

        app.adapter(_DummyPort, lambda: PinAdapter(pin=17))

        resolved = app._resolve_adapters()
        adapter = resolved[_DummyPort]
        assert isinstance(adapter, PinAdapter)
        assert adapter.pin == 17

    async def test_factory_callable_for_dry_run(self) -> None:
        """Factory callable used as dry_run variant is resolved in dry-run mode."""
        app = App(name="testapp", version="1.0.0", dry_run=True)
        app.adapter(_DummyPort, _DummyImpl, dry_run=lambda: _DummyDryRun())

        resolved = app._resolve_adapters()
        assert isinstance(resolved[_DummyPort], _DummyDryRun)

    async def test_class_impl_factory_dry_run(self) -> None:
        """Class for impl, factory callable for dry_run — mixed registration."""
        app = App(name="testapp", version="1.0.0", dry_run=True)
        app.adapter(_DummyPort, _DummyImpl, dry_run=lambda: _DummyDryRun())

        resolved = app._resolve_adapters()
        assert isinstance(resolved[_DummyPort], _DummyDryRun)

    async def test_factory_impl_class_dry_run(self) -> None:
        """Factory callable for impl, class for dry_run — mixed registration."""
        app = App(name="testapp", version="1.0.0", dry_run=True)
        app.adapter(_DummyPort, lambda: _DummyImpl(), dry_run=_DummyDryRun)

        resolved = app._resolve_adapters()
        assert isinstance(resolved[_DummyPort], _DummyDryRun)

    async def test_factory_impl_resolves_in_normal_mode(self) -> None:
        """Factory impl is used (not dry_run) when dry_run mode is off."""
        app = App(name="testapp", version="1.0.0")
        app.adapter(_DummyPort, lambda: _DummyImpl(), dry_run=_DummyDryRun)

        resolved = app._resolve_adapters()
        assert isinstance(resolved[_DummyPort], _DummyImpl)

    async def test_string_impl_factory_dry_run(self) -> None:
        """String import for impl, factory callable for dry_run."""
        app = App(name="testapp", version="1.0.0", dry_run=True)
        app.adapter(
            _DummyPort,
            "cosalette._mqtt:NullMqttClient",
            dry_run=lambda: _DummyDryRun(),
        )

        resolved = app._resolve_adapters()
        assert isinstance(resolved[_DummyPort], _DummyDryRun)


# ---------------------------------------------------------------------------
# TestMqttProtocolConformance — MqttLifecycle + MqttMessageHandler
# ---------------------------------------------------------------------------


class TestMqttProtocolConformance:
    """Protocol conformance tests for MqttLifecycle and MqttMessageHandler.

    Technique: Protocol Conformance — isinstance checks using
    ``runtime_checkable`` to verify structural subtyping contracts
    introduced for Interface Segregation (ADR-006, PEP 544).
    """

    def test_mqtt_client_satisfies_lifecycle(
        self,
    ) -> None:
        """MqttClient implements start()/stop() — satisfies MqttLifecycle."""
        from cosalette._mqtt import MqttLifecycle

        client = MqttClient(settings=MqttSettings())
        assert isinstance(client, MqttLifecycle)

    def test_mqtt_client_satisfies_message_handler(self) -> None:
        """MqttClient implements on_message() — satisfies MqttMessageHandler."""
        from cosalette._mqtt import MqttMessageHandler

        client = MqttClient(settings=MqttSettings())
        assert isinstance(client, MqttMessageHandler)

    def test_mock_mqtt_client_satisfies_message_handler(self) -> None:
        """MockMqttClient implements on_message() — satisfies MqttMessageHandler."""
        from cosalette._mqtt import MqttMessageHandler

        assert isinstance(MockMqttClient(), MqttMessageHandler)

    def test_mock_mqtt_client_does_not_satisfy_lifecycle(self) -> None:
        """MockMqttClient lacks start()/stop() — not MqttLifecycle."""
        from cosalette._mqtt import MqttLifecycle

        assert not isinstance(MockMqttClient(), MqttLifecycle)

    def test_null_mqtt_client_does_not_satisfy_lifecycle(self) -> None:
        """NullMqttClient lacks start()/stop() — not MqttLifecycle."""
        from cosalette._mqtt import MqttLifecycle, NullMqttClient

        assert not isinstance(NullMqttClient(), MqttLifecycle)

    def test_null_mqtt_client_does_not_satisfy_message_handler(self) -> None:
        """NullMqttClient lacks on_message() — not MqttMessageHandler."""
        from cosalette._mqtt import MqttMessageHandler, NullMqttClient

        assert not isinstance(NullMqttClient(), MqttMessageHandler)

    def test_all_three_satisfy_mqtt_port(self) -> None:
        """MqttClient, MockMqttClient, NullMqttClient all satisfy MqttPort."""
        from cosalette._mqtt import NullMqttClient

        client = MqttClient(settings=MqttSettings())
        assert isinstance(client, MqttPort)
        assert isinstance(MockMqttClient(), MqttPort)
        assert isinstance(NullMqttClient(), MqttPort)
