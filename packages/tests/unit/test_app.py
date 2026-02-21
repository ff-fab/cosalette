"""Tests for cosalette._app — App orchestrator.

Test Techniques Used:
    - Decorator-based Registration: Verify device/telemetry/lifespan setup
    - Specification-based Testing: Duplicate names, invalid intervals
    - Integration Testing: Full _run_async lifecycle with injected mocks
    - Async Coordination: asyncio.Event for deterministic test control
    - Mock-based Isolation: MockMqttClient + FakeClock avoid real I/O
    - Error Isolation: Verify crashed devices don't crash the App
    - Command Routing: Simulate MQTT messages via MockMqttClient.deliver()
"""

from __future__ import annotations

import asyncio
import logging
import unittest.mock
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable
from unittest.mock import patch

import pytest

from cosalette._app import App, _noop_lifespan
from cosalette._context import AppContext, DeviceContext
from cosalette._mqtt import MqttClient, MqttPort
from cosalette._settings import MqttSettings, Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> App:
    """Minimal App instance for registration tests."""
    return App(name="testapp", version="1.0.0")


# mock_mqtt and fake_clock fixtures provided by cosalette.testing._plugin


# ---------------------------------------------------------------------------
# TestHeartbeatIntervalValidation
# ---------------------------------------------------------------------------


class TestHeartbeatIntervalValidation:
    """heartbeat_interval parameter validation.

    Technique: Boundary Testing — verifying that non-positive values
    are rejected at construction time (fail-fast).
    """

    def test_rejects_zero_interval(self) -> None:
        """Zero interval would create a busy-loop and is rejected."""
        with pytest.raises(ValueError, match="positive"):
            App(name="x", heartbeat_interval=0)

    def test_rejects_negative_interval(self) -> None:
        """Negative intervals are nonsensical and rejected."""
        with pytest.raises(ValueError, match="positive"):
            App(name="x", heartbeat_interval=-1.0)

    def test_accepts_positive_interval(self) -> None:
        """Positive values are accepted without error."""
        app = App(name="x", heartbeat_interval=30.0)
        assert app._heartbeat_interval == 30.0  # noqa: SLF001

    def test_accepts_none_interval(self) -> None:
        """None disables heartbeats — no error raised."""
        app = App(name="x", heartbeat_interval=None)
        assert app._heartbeat_interval is None  # noqa: SLF001


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
# TestLifespan
# ---------------------------------------------------------------------------


class TestLifespan:
    """Lifespan context manager registration tests.

    Technique: Specification-based Testing — verifying that
    ``App(lifespan=...)`` stores a custom lifespan, and that the
    default is the no-op lifespan.
    """

    async def test_default_lifespan_is_noop(self, app: App) -> None:
        """When no lifespan is provided, the no-op default is used."""
        assert app._lifespan is _noop_lifespan  # noqa: SLF001

    async def test_custom_lifespan_stored(self) -> None:
        """A custom lifespan function is stored on the App."""

        @asynccontextmanager
        async def my_lifespan(ctx: AppContext) -> AsyncIterator[None]:
            yield

        app = App(name="testapp", version="1.0.0", lifespan=my_lifespan)
        assert app._lifespan is my_lifespan  # noqa: SLF001


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


@runtime_checkable
class _InjectionTestPort(Protocol):
    """Port protocol for injection adapter tests."""

    def value(self) -> int: ...


class _InjectionTestImpl:
    """Concrete adapter for injection adapter tests."""

    def value(self) -> int:
        return 42


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
                settings=make_settings(),
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
                settings=make_settings(),
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

    async def test_lifespan_startup_runs(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan startup phase runs with an AppContext during _run_async.

        Coordination: lifespan sets an event before yield, helper
        triggers shutdown.
        """
        hook_called = asyncio.Event()
        received_ctx: list[AppContext] = []

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            received_ctx.append(ctx)
            hook_called.set()
            yield

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await hook_called.wait()
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert hook_called.is_set()
        assert len(received_ctx) == 1
        assert isinstance(received_ctx[0], AppContext)

    async def test_lifespan_teardown_runs(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan teardown phase runs on shutdown.

        Coordination: trigger shutdown immediately, verify teardown
        ran after _run_async completes.
        """
        hook_called = asyncio.Event()

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            yield
            hook_called.set()

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        shutdown = asyncio.Event()
        # Trigger shutdown on next event-loop tick
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert hook_called.is_set()

    async def test_lifespan_happy_path(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Both startup and teardown phases of the lifespan run in order.

        Technique: State-based Testing — verify both phases execute
        and ordering is startup → teardown.
        """
        phases: list[str] = []

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            phases.append("startup")
            yield
            phases.append("teardown")

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)
        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert phases == ["startup", "teardown"]

    async def test_lifespan_startup_error_prevents_device_launch(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """If lifespan startup raises, devices never start (fail-fast).

        Technique: Error Guessing — verifying that a startup error
        propagates and prevents device execution.
        """
        device_started = False

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            msg = "startup failed"
            raise RuntimeError(msg)
            yield  # noqa: RET503 — unreachable, required by generator

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            nonlocal device_started
            device_started = True

        shutdown = asyncio.Event()

        with pytest.raises(RuntimeError, match="startup failed"):
            await app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            )

        assert not device_started

    async def test_lifespan_teardown_error_logged_not_raised(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan teardown error is logged but doesn't crash the app.

        Technique: Error Guessing — verifying that teardown errors
        are gracefully handled and logged.

        Note: configure_logging clears caplog's handler, so we check
        the logger method was called via mock.
        """

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            yield
            msg = "teardown failed"
            raise RuntimeError(msg)

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)
        shutdown = asyncio.Event()
        shutdown.set()

        # Should NOT raise
        with patch("cosalette._app.logger") as mock_logger:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

        mock_logger.exception.assert_called_with("Lifespan teardown error")

    async def test_lifespan_receives_correct_app_context(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan receives AppContext with correct settings and adapters.

        Technique: Specification-based Testing — verifying that the
        AppContext passed to the lifespan has the expected settings
        and adapter resolution.
        """
        received_ctx: list[AppContext] = []

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            received_ctx.append(ctx)
            yield

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)
        app.adapter(_DummyPort, _DummyImpl)

        shutdown = asyncio.Event()
        shutdown.set()
        settings = make_settings()

        await asyncio.wait_for(
            app._run_async(
                settings=settings,
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert len(received_ctx) == 1
        ctx = received_ctx[0]
        assert ctx.settings is settings
        assert isinstance(ctx.adapter(_DummyPort), _DummyImpl)

    async def test_lifespan_aexit_receives_exception_info(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan __aexit__ receives real exc info when run phase fails.

        Technique: State-based Testing — a raw async context manager
        records the ``__aexit__`` arguments.  We patch
        ``HealthReporter.publish_heartbeat`` to raise immediately
        inside the ``try`` block, so the exception propagates to the
        ``finally`` block where ``sys.exc_info()`` is captured.

        Why a raw CM instead of @asynccontextmanager?
        ``@asynccontextmanager`` converts the ``(exc_type, exc_val, tb)``
        into a ``gen.athrow()`` call, making it hard to inspect the raw
        args.  A plain class exposes them directly.
        """
        aexit_args: list[tuple[type[BaseException] | None, ...]] = []

        class RecordingLifespan:
            """Context manager that records __aexit__ arguments."""

            async def __aenter__(self) -> None:
                return None

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc_val: BaseException | None,
                exc_tb: object,
            ) -> bool:
                aexit_args.append((exc_type, exc_val))  # type: ignore[arg-type]
                return False  # don't suppress the exception

        app = App(
            name="testapp",
            version="1.0.0",
            lifespan=lambda _ctx: RecordingLifespan(),
        )

        shutdown = asyncio.Event()
        shutdown.set()

        boom = RuntimeError("heartbeat boom")
        with (
            patch(
                "cosalette._health.HealthReporter.publish_heartbeat",
                side_effect=boom,
            ),
            pytest.raises(RuntimeError, match="heartbeat boom"),
        ):
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

        assert len(aexit_args) == 1
        exc_type, exc_val = aexit_args[0]
        assert exc_type is RuntimeError
        assert exc_val is boom

    async def test_lifespan_aexit_receives_none_on_clean_shutdown(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan __aexit__ receives (None, None, None) on clean shutdown.

        Technique: State-based Testing — complementary to
        ``test_lifespan_aexit_receives_exception_info``, this verifies
        that a clean (no-exception) shutdown passes no exception info.
        """
        aexit_args: list[tuple[type[BaseException] | None, ...]] = []

        class RecordingLifespan:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc_val: BaseException | None,
                exc_tb: object,
            ) -> bool:
                aexit_args.append((exc_type, exc_val, exc_tb))
                return False

        app = App(
            name="testapp",
            version="1.0.0",
            lifespan=lambda _ctx: RecordingLifespan(),
        )

        shutdown = asyncio.Event()
        shutdown.set()

        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert len(aexit_args) == 1
        assert aexit_args[0] == (None, None, None)

    async def test_no_lifespan_noop_works(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """App with no lifespan runs the full lifecycle without error.

        Technique: Negative Testing — verifying the no-op default
        path completes successfully.
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
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert device_done.is_set()

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
                settings=make_settings(),
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
                settings=make_settings(),
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
                settings=make_settings(),
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
                settings=make_settings(),
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
                settings=make_settings(),
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
                settings=make_settings(),
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
                settings=make_settings(),
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
                settings=make_settings(),
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
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert "testapp/blind/set" in mock_mqtt.subscriptions
        assert "testapp/window/set" in mock_mqtt.subscriptions

    async def test_lifespan_teardown_runs_after_device_cancellation(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan teardown runs after device tasks are cancelled.

        Verifies shutdown ordering: ``_cancel_tasks`` runs before the
        lifespan teardown phase.  The device's ``finally`` block runs
        first, then the lifespan's post-yield code.

        Technique: State Transition Testing — verifying shutdown-phase
        ordering via observable side effects.
        """
        ordering: list[str] = []
        device_started = asyncio.Event()

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            yield
            ordering.append("lifespan_teardown")

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            device_started.set()
            try:
                while not ctx.shutdown_requested:
                    await ctx.sleep(1)
            finally:
                ordering.append("device_cleanup")

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_started.wait()
            await asyncio.sleep(0.02)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Device cleanup (from task cancellation) must happen
        # before the lifespan teardown runs.
        assert ordering == ["device_cleanup", "lifespan_teardown"]

    async def test_command_error_published_to_mqtt(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Command handler exceptions are published as structured errors.

        When a registered command handler raises, the proxy in
        ``_wire_router`` catches the exception and publishes a
        structured error payload to MQTT via ErrorPublisher.

        Technique: Error Guessing — verifying the error publication
        path for command handlers (analogous to device error isolation).
        """
        app = App(name="testapp", version="1.0.0")
        command_received = asyncio.Event()

        @app.device("valve")
        async def valve(ctx: DeviceContext) -> None:
            @ctx.on_command
            async def handle(topic: str, payload: str) -> None:
                command_received.set()
                msg = "invalid command"
                raise ValueError(msg)

            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        shutdown = asyncio.Event()

        async def simulate_command() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/valve/set", "INVALID")
            await command_received.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(simulate_command())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Error published to global error topic
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1
        assert "invalid command" in error_messages[0][0]

        # Error also published to per-device error topic
        device_errors = mock_mqtt.get_messages_for("testapp/valve/error")
        assert len(device_errors) >= 1
        assert "invalid command" in device_errors[0][0]

    async def test_command_error_publication_failure_is_swallowed(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """If error publication fails, the device continues running.

        Belt-and-suspenders: even when ErrorPublisher.publish() raises
        unexpectedly, the proxy swallows the exception.  The device
        must not crash because error *reporting* failed.

        Technique: Error Guessing — fault injection into the error
        reporting path itself.
        """
        app = App(name="testapp", version="1.0.0")
        command_count = 0
        second_command = asyncio.Event()

        @app.device("valve")
        async def valve(ctx: DeviceContext) -> None:
            @ctx.on_command
            async def handle(topic: str, payload: str) -> None:
                nonlocal command_count
                command_count += 1
                if command_count <= 2:
                    msg = "boom"
                    raise RuntimeError(msg)
                second_command.set()

            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        shutdown = asyncio.Event()

        async def simulate_commands() -> None:
            await asyncio.sleep(0.05)
            # First command: handler raises, error publication works
            await mock_mqtt.deliver("testapp/valve/set", "CMD1")
            await asyncio.sleep(0.05)

            # Sabotage MQTT so error publication will fail
            original_publish = mock_mqtt.publish

            async def failing_publish(
                topic: str,
                payload: str,
                *,
                retain: bool = False,
                qos: int = 0,
            ) -> None:
                if "/error" in topic:
                    msg = "MQTT down"
                    raise ConnectionError(msg)
                await original_publish(
                    topic,
                    payload,
                    retain=retain,
                    qos=qos,
                )

            mock_mqtt.publish = failing_publish  # type: ignore[assignment]

            # Second command: handler raises AND error publication fails
            await mock_mqtt.deliver("testapp/valve/set", "CMD2")
            await asyncio.sleep(0.05)

            # Restore real publish
            mock_mqtt.publish = original_publish  # type: ignore[assignment]

            # Third command: device is still alive (succeeds, sets event)
            await mock_mqtt.deliver("testapp/valve/set", "CMD3")
            await second_command.wait()
            shutdown.set()

        asyncio.create_task(simulate_commands())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Device survived: third command was processed
        assert command_count == 3

    async def test_device_command_handler_error_is_logged(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Device command handler errors are logged before MQTT publication.

        Technique: State-based Testing — verify logger.error() is called
        when a @ctx.on_command handler raises, complementing the existing
        test that verifies MQTT error publication.

        Note: configure_logging clears caplog's handler, so we check
        the logger method was called via mock.
        """
        app = App(name="testapp", version="1.0.0")
        handler_registered = asyncio.Event()
        command_received = asyncio.Event()

        @app.device("valve")
        async def valve(ctx: DeviceContext) -> None:
            @ctx.on_command
            async def handle(topic: str, payload: str) -> None:
                command_received.set()
                msg = "valve malfunction"
                raise RuntimeError(msg)

            handler_registered.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await handler_registered.wait()
            await mock_mqtt.deliver("testapp/valve/set", "OPEN")
            await command_received.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(simulate())

        with patch("cosalette._app.logger") as mock_logger:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

        mock_logger.error.assert_any_call(
            "Device '%s' command handler error: %s",
            "valve",
            unittest.mock.ANY,
        )

    async def test_topic_prefix_override_from_settings(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Settings.mqtt.topic_prefix overrides App(name=...) for all topics.

        When ``topic_prefix`` is non-empty, it replaces the app name
        as the root prefix for status, availability, error, and
        command topics.

        Technique: Integration Testing — verify observable MQTT topics
        use the settings-provided prefix instead of the app name.
        """
        app = App(name="testapp", version="1.0.0")
        device_done = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            device_done.set()

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_done.wait()
            await asyncio.sleep(0.02)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        settings = make_settings(mqtt=MqttSettings(topic_prefix="staging"))
        await asyncio.wait_for(
            app._run_async(
                settings=settings,
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Availability published under overridden prefix, not "testapp"
        avail = mock_mqtt.get_messages_for("staging/sensor/availability")
        assert any(p == "online" for p, _, _ in avail)
        # Nothing published under the app name
        assert mock_mqtt.get_messages_for("testapp/sensor/availability") == []
        # Status topic also uses the overridden prefix
        status = mock_mqtt.get_messages_for("staging/status")
        assert len(status) >= 1

    async def test_topic_prefix_falls_back_to_app_name(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Empty topic_prefix falls back to App(name=...).

        Default behaviour: when ``topic_prefix`` is empty (default),
        the application name is used as the MQTT prefix — ensuring
        backward compatibility.

        Technique: Negative Testing — verifying the fallback path
        with the default (empty) setting.
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
        # topic_prefix="" (default) — should use "testapp"
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        avail = mock_mqtt.get_messages_for("testapp/sensor/availability")
        assert any(p == "online" for p, _, _ in avail)

    async def test_client_id_auto_generated_when_empty(
        self,
        fake_clock: FakeClock,
    ) -> None:
        """Empty client_id is auto-generated as ``{name}-{hex8}``.

        When no ``client_id`` is configured, App generates a
        deterministic-format identifier for debuggability.

        Technique: Spy Pattern — use a real MqttClient and inspect
        the settings it was constructed with.
        """
        app = App(name="myapp", version="1.0.0")

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            pass

        shutdown = asyncio.Event()
        shutdown.set()
        settings = make_settings()
        assert settings.mqtt.client_id == ""

        # Capture the MqttClient created by _create_mqtt
        captured_clients: list[MqttClient] = []
        original_create = app._create_mqtt

        def spy_create(
            mqtt: MqttPort | None,
            resolved_settings: object,
            prefix: str,
        ) -> MqttPort:
            result = original_create(mqtt, resolved_settings, prefix)  # type: ignore[arg-type]
            if isinstance(result, MqttClient):
                captured_clients.append(result)
            return result

        app._create_mqtt = spy_create  # type: ignore[assignment]

        mock_mqtt = MockMqttClient()
        await asyncio.wait_for(
            app._run_async(
                settings=settings,
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # When injected mock is passed, _create_mqtt returns it directly,
        # so we test _create_mqtt directly instead.
        client = app._create_mqtt(None, settings, "myapp")
        assert isinstance(client, MqttClient)
        cid = client.settings.client_id
        assert cid.startswith("myapp-")
        assert len(cid) == len("myapp-") + 8  # 8 hex chars

    async def test_client_id_preserved_when_configured(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Explicitly configured client_id is not overwritten.

        When the user sets ``MQTT__CLIENT_ID``, App must honour it
        rather than auto-generating a new one.

        Technique: Specification-based — verify user setting survives.
        """
        app = App(name="myapp", version="1.0.0")

        settings = make_settings(
            mqtt=MqttSettings(client_id="my-custom-id"),
        )

        # Call _create_mqtt directly to test the branch
        client = app._create_mqtt(None, settings, "myapp")
        assert isinstance(client, MqttClient)
        assert client.settings.client_id == "my-custom-id"

    async def test_heartbeat_published_on_startup(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """An initial heartbeat is published immediately on startup.

        Before the periodic loop starts, ``_run_async`` publishes a
        structured JSON heartbeat to ``{prefix}/status`` so the LWT
        ``"offline"`` string is overwritten right away.

        Technique: Integration Testing — verify status topic contains
        a JSON heartbeat after startup.
        """
        app = App(name="testapp", version="1.0.0", heartbeat_interval=60.0)
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
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        status = mock_mqtt.get_messages_for("testapp/status")
        # First message should be the JSON heartbeat (before shutdown offline)
        assert len(status) >= 1
        first_payload = status[0][0]
        assert '"status": "online"' in first_payload
        assert '"version": "1.0.0"' in first_payload

    async def test_periodic_heartbeat_publishes_multiple_times(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Periodic heartbeat loop publishes at the configured interval.

        Uses a very short interval to verify multiple heartbeats arrive
        within the test timeout.

        Technique: Temporal Testing — short interval triggers multiple
        publications in a controlled window.
        """
        app = App(name="testapp", version="1.0.0", heartbeat_interval=0.02)

        shutdown = asyncio.Event()

        async def wait_for_heartbeats() -> None:
            # Wait long enough for 2+ periodic heartbeats (+ initial)
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(wait_for_heartbeats())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        status = mock_mqtt.get_messages_for("testapp/status")
        # Filter to only JSON heartbeat payloads (not "offline" strings)
        json_heartbeats = [p for p, _, _ in status if p.startswith("{")]
        # Initial + at least 2 periodic = 3+
        assert len(json_heartbeats) >= 3

    async def test_heartbeat_disabled_with_none_interval(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Setting ``heartbeat_interval=None`` disables periodic heartbeats.

        An initial heartbeat is still published (to overwrite LWT),
        but no periodic loop runs.

        Technique: Negative Testing — verify no extra heartbeats
        after a delay that would produce them with a non-None interval.
        """
        app = App(name="testapp", version="1.0.0", heartbeat_interval=None)

        shutdown = asyncio.Event()

        async def delayed_shutdown() -> None:
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(delayed_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        status = mock_mqtt.get_messages_for("testapp/status")
        # Only the initial heartbeat + shutdown "offline" — no periodic ones
        json_heartbeats = [p for p, _, _ in status if p.startswith("{")]
        assert len(json_heartbeats) == 1


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


# ---------------------------------------------------------------------------
# TestSignatureInjection — handler injection integration tests
# ---------------------------------------------------------------------------


class TestSignatureInjection:
    """Signature-based handler injection integration tests.

    Technique: Integration Testing — verify that handlers with various
    signatures are correctly invoked via the full _run_async lifecycle.
    """

    async def test_device_zero_arg_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A device handler with zero parameters is called successfully."""
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        @app.device("sensor")
        async def sensor() -> None:
            called.set()

        shutdown = asyncio.Event()

        async def trigger() -> None:
            await called.wait()
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert called.is_set()

    async def test_telemetry_zero_arg_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A telemetry handler with zero parameters is called and publishes."""
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        @app.telemetry("temp", interval=1)
        async def temp() -> dict[str, object]:
            called.set()
            return {"celsius": 22.5}

        shutdown = asyncio.Event()

        async def trigger() -> None:
            await called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert called.is_set()
        messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(messages) >= 1
        assert "22.5" in messages[0][0]

    async def test_device_settings_only_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A device handler requesting only Settings receives it."""
        app = App(name="testapp", version="1.0.0")
        received_settings: list[Settings] = []

        @app.device("valve")
        async def valve(settings: Settings) -> None:
            received_settings.append(settings)

        shutdown = asyncio.Event()
        test_settings = make_settings()

        async def trigger() -> None:
            while not received_settings:
                await asyncio.sleep(0.01)
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                settings=test_settings,
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert received_settings[0] is test_settings

    async def test_device_logger_only_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A device handler requesting only Logger receives a per-device logger."""
        app = App(name="testapp", version="1.0.0")
        received_logger: list[logging.Logger] = []

        @app.device("valve")
        async def valve(logger: logging.Logger) -> None:
            received_logger.append(logger)

        shutdown = asyncio.Event()

        async def trigger() -> None:
            while not received_logger:
                await asyncio.sleep(0.01)
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert received_logger[0].name == "cosalette.valve"

    async def test_device_multi_arg_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A handler requesting DeviceContext + Logger receives both."""
        app = App(name="testapp", version="1.0.0")
        results: list[tuple[DeviceContext, logging.Logger]] = []

        @app.device("valve")
        async def valve(ctx: DeviceContext, logger: logging.Logger) -> None:
            results.append((ctx, logger))

        shutdown = asyncio.Event()

        async def trigger() -> None:
            while not results:
                await asyncio.sleep(0.01)
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        ctx, log = results[0]
        assert isinstance(ctx, DeviceContext)
        assert log.name == "cosalette.valve"

    async def test_device_with_adapter_injection(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A handler requesting an adapter port type receives the adapter."""
        app = App(name="testapp", version="1.0.0")
        app.adapter(_InjectionTestPort, _InjectionTestImpl)

        received_values: list[int] = []

        @app.device("sensor")
        async def sensor(port: _InjectionTestPort) -> None:
            received_values.append(port.value())

        shutdown = asyncio.Event()

        async def trigger() -> None:
            while not received_values:
                await asyncio.sleep(0.01)
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert received_values[0] == 42

    def test_device_missing_annotation_raises(self) -> None:
        """Registering a handler with unannotated parameters raises TypeError.

        Technique: Error Guessing — fail-fast at registration time.
        """
        app = App(name="testapp", version="1.0.0")

        with pytest.raises(TypeError, match="no type annotation"):

            @app.device("sensor")
            async def sensor(ctx) -> None:  # type: ignore[no-untyped-def]
                ...

    def test_telemetry_missing_annotation_raises(self) -> None:
        """Registering a telemetry with unannotated parameters raises TypeError."""
        app = App(name="testapp", version="1.0.0")

        with pytest.raises(TypeError, match="no type annotation"):

            @app.telemetry("temp", interval=5)
            async def temp(ctx) -> dict:  # type: ignore[no-untyped-def]
                return {}

    async def test_existing_ctx_style_still_works(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Backwards compat: handler(ctx: DeviceContext) still works.

        This is the existing style — injection with a single DeviceContext
        parameter should be functionally identical to the old direct call.
        """
        app = App(name="testapp", version="1.0.0")
        device_called = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            assert isinstance(ctx, DeviceContext)
            assert ctx.name == "sensor"
            device_called.set()

        shutdown = asyncio.Event()

        async def trigger() -> None:
            await device_called.wait()
            shutdown.set()

        asyncio.create_task(trigger())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert device_called.is_set()


# ---------------------------------------------------------------------------
# TestRootDevice — root-level (unnamed) device registration
# ---------------------------------------------------------------------------


class TestRootDevice:
    """Tests for root-level device registration (unnamed devices).

    When ``name`` is omitted from ``@app.device()``, ``@app.telemetry()``,
    or ``@app.command()``, the function name is used internally while
    topics omit the device segment (root-level).

    Technique: Specification-based Testing — verifying decorator
    behaviour, duplicate rejection, and warning on mixed modes.
    """

    def test_telemetry_name_defaults_to_function_name(self) -> None:
        """Unnamed telemetry uses function name internally."""
        app = App(name="testapp", version="1.0.0")

        @app.telemetry(interval=5.0)
        async def sensor() -> dict[str, object]:
            return {"temp": 21.5}

        assert len(app._telemetry) == 1
        assert app._telemetry[0].name == "sensor"
        assert app._telemetry[0].is_root is True

    def test_command_name_defaults_to_function_name(self) -> None:
        """Unnamed command uses function name internally."""
        app = App(name="testapp", version="1.0.0")

        @app.command()
        async def valve(payload: str) -> dict[str, object]:
            return {"state": payload}

        assert len(app._commands) == 1
        assert app._commands[0].name == "valve"
        assert app._commands[0].is_root is True

    def test_device_name_defaults_to_function_name(self) -> None:
        """Unnamed device uses function name internally."""
        app = App(name="testapp", version="1.0.0")

        @app.device()
        async def sensor(ctx: DeviceContext) -> None:
            await ctx.sleep(999)

        assert len(app._devices) == 1
        assert app._devices[0].name == "sensor"
        assert app._devices[0].is_root is True

    def test_second_root_device_raises(self) -> None:
        """Only one root device allowed per app."""
        app = App(name="testapp", version="1.0.0")

        @app.telemetry(interval=5.0)
        async def sensor() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="Only one root device"):

            @app.command()
            async def valve(payload: str) -> dict[str, object]:
                return {}

    def test_named_device_still_works(self) -> None:
        """Named devices are is_root=False."""
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("sensor", interval=5.0)
        async def sensor() -> dict[str, object]:
            return {}

        assert app._telemetry[0].is_root is False

    def test_mixing_root_and_named_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warning logged when mixing root and named devices."""
        app = App(name="testapp", version="1.0.0")

        @app.telemetry("sensor", interval=5.0)
        async def sensor() -> dict[str, object]:
            return {}

        with caplog.at_level(logging.WARNING, logger="cosalette._app"):

            @app.command()
            async def valve(payload: str) -> dict[str, object]:
                return {}

        assert any("wildcard" in r.message for r in caplog.records)

    def test_mixing_named_after_root_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warning logged when adding a named device after a root device."""
        app = App(name="testapp", version="1.0.0")

        @app.telemetry(interval=5.0)
        async def sensor() -> dict[str, object]:
            return {}

        with caplog.at_level(logging.WARNING, logger="cosalette._app"):

            @app.telemetry("other", interval=10.0)
            async def other() -> dict[str, object]:
                return {}

        assert any("wildcard" in r.message for r in caplog.records)

    async def test_root_telemetry_publishes_to_prefix_state(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Root telemetry publishes to {prefix}/state, not {prefix}/{name}/state."""
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        @app.telemetry(interval=1.0)
        async def sensor() -> dict[str, object]:
            called.set()
            return {"temp": 21.5}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Root device: published to testapp/state, NOT testapp/sensor/state
        state_messages = mock_mqtt.get_messages_for("testapp/state")
        assert len(state_messages) >= 1
        assert mock_mqtt.get_messages_for("testapp/sensor/state") == []

    def test_bare_device_decorator_raises_type_error(self) -> None:
        """@app.device (no parens) raises TypeError with clear message.

        Without the guard, the decorated function is silently passed as
        the ``name`` parameter, leading to confusing downstream errors.
        The guard catches this immediately with a helpful message.
        """
        app = App(name="testapp", version="1.0.0")

        with pytest.raises(TypeError, match="parentheses required"):

            @app.device  # type: ignore[arg-type]
            async def sensor(ctx: DeviceContext) -> None:
                await ctx.sleep(999)

    def test_bare_command_decorator_raises_type_error(self) -> None:
        """@app.command (no parens) raises TypeError with clear message.

        Same guard as @app.device — catches missing parentheses and
        provides an actionable error message.
        """
        app = App(name="testapp", version="1.0.0")

        with pytest.raises(TypeError, match="parentheses required"):

            @app.command  # type: ignore[arg-type]
            async def valve(payload: str) -> dict[str, object]:
                return {"state": payload}

    async def test_root_command_receives_on_prefix_set(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Root command subscribes to {prefix}/set and publishes to {prefix}/state.

        Technique: Integration Testing — register a root @app.command(),
        deliver a message to {prefix}/set, verify the handler is called
        and state is published to {prefix}/state (not {prefix}/{name}/state).
        """
        app = App(name="testapp", version="1.0.0")
        command_received = asyncio.Event()

        @app.command()
        async def valve(payload: str) -> dict[str, object]:
            command_received.set()
            return {"position": payload}

        shutdown = asyncio.Event()

        async def simulate_command() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/set", "OPEN")
            await command_received.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(simulate_command())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert command_received.is_set()
        # Root command: state published to testapp/state, NOT testapp/valve/state
        state_messages = mock_mqtt.get_messages_for("testapp/state")
        assert len(state_messages) >= 1
        assert mock_mqtt.get_messages_for("testapp/valve/state") == []

    async def test_root_device_lifecycle_uses_prefix_availability(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Root device availability uses {prefix}/availability, not {prefix}/{name}/....

        Technique: Integration Testing — register a root @app.device(),
        run the full lifecycle, and verify availability is published to
        {prefix}/availability and state to {prefix}/state.
        """
        app = App(name="testapp", version="1.0.0")
        device_started = asyncio.Event()

        @app.device()
        async def sensor(ctx: DeviceContext) -> None:
            device_started.set()
            await ctx.publish_state({"value": 42})
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await device_started.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Root device: availability at testapp/availability,
        # NOT testapp/sensor/availability
        avail_messages = mock_mqtt.get_messages_for("testapp/availability")
        assert len(avail_messages) >= 1
        assert mock_mqtt.get_messages_for("testapp/sensor/availability") == []

        # Root device: state at testapp/state, NOT testapp/sensor/state
        state_messages = mock_mqtt.get_messages_for("testapp/state")
        assert len(state_messages) >= 1
        assert mock_mqtt.get_messages_for("testapp/sensor/state") == []
