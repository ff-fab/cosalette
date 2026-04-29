"""Tests for cosalette App — wiring, lifecycle, heartbeat, protocols, and injection.

Covers: device wiring, concurrent execution, graceful shutdown, MQTT
subscriptions, topic prefix, client ID, lifespan integration, heartbeat
publishing, MqttLifecycle/MqttMessageHandler protocol conformance, and
signature-based handler injection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from cosalette._app import App
from cosalette._context import AppContext, DeviceContext
from cosalette._mqtt import MqttClient, MqttPort
from cosalette._settings import MqttSettings, Settings
from cosalette._stores import NullStore, Store
from cosalette._wiring import resolve_settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.unit.conftest import (
    _DummyImpl,
    _DummyPort,
    _InjectionTestImpl,
    _InjectionTestPort,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper classes for TestLifespanYieldedState (module-level so PEP 563
# forward-reference resolution can find them via __globals__).
# ---------------------------------------------------------------------------


class _YieldedState:
    """Injectable state yielded by a lifespan."""

    def __init__(self, value: object = 42) -> None:
        self.value = value


class _ConflictState:
    """Class used as both adapter key and lifespan-yielded type to trigger conflict."""


# ---------------------------------------------------------------------------
# TestRunAsyncWiring — device wiring integration tests
# ---------------------------------------------------------------------------


class TestRunAsyncWiring:
    """Device wiring, concurrency, shutdown, and MQTT subscription tests."""

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
        from cosalette import _wiring

        settings = make_settings()
        assert settings.mqtt.client_id == ""

        # Call _wiring.create_mqtt directly to test auto-generated client ID.
        client = _wiring.create_mqtt(None, settings, "myapp", "myapp")
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
        from cosalette import _wiring

        settings = make_settings(
            mqtt=MqttSettings(client_id="my-custom-id"),
        )

        # Call _wiring.create_mqtt directly to test the branch
        client = _wiring.create_mqtt(None, settings, "myapp", "myapp")
        assert isinstance(client, MqttClient)
        assert client.settings.client_id == "my-custom-id"


# ---------------------------------------------------------------------------
# TestRunAsyncLifespan — lifespan integration tests
# ---------------------------------------------------------------------------


class TestRunAsyncLifespan:
    """Lifespan startup/teardown integration tests within _run_async."""

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
        with patch("cosalette._wiring._tasks.logger") as mock_logger:
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
                aexit_args.append((exc_type, exc_val))  # ty: ignore[invalid-argument-type]
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
                aexit_args.append((exc_type, exc_val, exc_tb))  # ty: ignore[invalid-argument-type]
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


# ---------------------------------------------------------------------------
# TestLifespanYieldedState — lifespan-yielded injectable state (ADR-027)
# ---------------------------------------------------------------------------


class TestLifespanYieldedState:
    """Tests for lifespan-yielded injectable state (ADR-027)."""

    async def test_yielded_state_injected_into_telemetry_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan yields _YieldedState -> telemetry handler receives it via DI."""
        received: list[_YieldedState] = []

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[_YieldedState]:
            yield _YieldedState(42)

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        @app.telemetry("sensor", interval=1)
        async def handler(state: _YieldedState) -> dict[str, object]:
            received.append(state)
            return {"v": state.value}

        shutdown = asyncio.Event()

        async def trigger() -> None:
            while not received:
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

        assert len(received) >= 1
        assert received[0].value == 42

    async def test_yield_none_no_registration(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan yielding None does not register anything (backward compat)."""
        device_ran = asyncio.Event()

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[None]:
            yield

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        @app.device("d")
        async def device(ctx: DeviceContext) -> None:
            device_ran.set()

        shutdown = asyncio.Event()

        async def trigger() -> None:
            await device_ran.wait()
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

        assert device_ran.is_set()

    async def test_yield_conflicting_type_raises(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan yielding a type already in DI raises RuntimeError."""

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[_ConflictState]:
            yield _ConflictState()

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)
        # Register _ConflictState as both port and impl so it's a key
        # in resolved_adapters — yielding the same type triggers conflict.
        app.adapter(_ConflictState, _ConflictState)

        @app.device("d")
        async def device(ctx: DeviceContext) -> None:
            pass  # pragma: no cover

        shutdown = asyncio.Event()

        with pytest.raises(
            RuntimeError, match="conflicts with existing DI registration"
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

    async def test_yield_framework_type_raises(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan yielding a framework-provided type raises RuntimeError."""

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[Settings]:
            yield Settings(mqtt=MqttSettings(host="localhost"))

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        @app.device("d")
        async def device(ctx: DeviceContext) -> None:
            pass  # pragma: no cover

        shutdown = asyncio.Event()

        with pytest.raises(
            RuntimeError, match="conflicts with framework-provided injectable type"
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

    async def test_yielded_state_injected_into_command_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan yields state -> command handler receives it via DI."""
        received: list[_YieldedState] = []
        command_done = asyncio.Event()

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[_YieldedState]:
            yield _YieldedState("cmd-hello")

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        @app.command("light")
        async def handle_light(
            topic: str,
            payload: str,
            state: _YieldedState,
        ) -> dict[str, object]:
            received.append(state)
            command_done.set()
            return {"v": state.value}

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/light/set", "ON")
            await command_done.wait()
            await asyncio.sleep(0.02)
            shutdown.set()

        asyncio.create_task(simulate())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        assert len(received) == 1
        assert received[0].value == "cmd-hello"

    async def test_yielded_state_injected_into_device_handler(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan yields state -> @app.device handler receives it via DI."""
        received: list[_YieldedState] = []

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[_YieldedState]:
            yield _YieldedState("hello")

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        @app.device("d")
        async def device(ctx: DeviceContext, state: _YieldedState) -> None:
            received.append(state)

        shutdown = asyncio.Event()

        async def trigger() -> None:
            while not received:
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

        assert len(received) == 1
        assert received[0].value == "hello"

    async def test_yielded_state_cleaned_up_on_teardown(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Yielded state is removed from DI after lifespan teardown.

        Verified by running the app twice with the same lifespan — if
        cleanup failed, the second run would raise a conflict error.
        """

        @asynccontextmanager
        async def lifespan(ctx: AppContext) -> AsyncIterator[_YieldedState]:
            yield _YieldedState()

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)

        @app.device("d")
        async def device(ctx: DeviceContext) -> None:
            pass

        shutdown1 = asyncio.Event()
        shutdown1.set()
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown1,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # Second run succeeds only if first run cleaned up the type.
        shutdown2 = asyncio.Event()
        shutdown2.set()
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown2,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

    async def test_lifespan_teardown_runs_on_conflict_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Lifespan teardown runs even when yield type conflicts with DI.

        Ensures __aexit__ is called so resources acquired before yield
        are properly released.
        """
        cleanup_ran = False

        @asynccontextmanager
        async def lifespan(
            ctx: AppContext,
        ) -> AsyncIterator[_ConflictState]:
            try:
                yield _ConflictState()
            finally:
                nonlocal cleanup_ran
                cleanup_ran = True

        app = App(name="testapp", version="1.0.0", lifespan=lifespan)
        app.adapter(_ConflictState, _ConflictState)

        @app.device("d")
        async def device(ctx: DeviceContext) -> None:
            pass  # pragma: no cover

        shutdown = asyncio.Event()

        with pytest.raises(
            RuntimeError, match="conflicts with existing DI registration"
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

        assert cleanup_ran


# ---------------------------------------------------------------------------
# TestRunAsyncHeartbeat — heartbeat publishing tests
# ---------------------------------------------------------------------------


class TestRunAsyncHeartbeat:
    """Heartbeat publishing integration tests."""

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
        parsed = json.loads(first_payload)
        assert parsed["status"] == "online"
        assert parsed["version"] == "1.0.0"

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
# TestPublishRegistrySnapshot — registry snapshot MQTT publication
# ---------------------------------------------------------------------------


class TestPublishRegistrySnapshot:
    """Tests for :func:`cosalette._wiring.publish_registry_snapshot`.

    Test Techniques Used:
        - Specification-based Testing: Verifies topic, retain flag, QoS,
          and payload structure.
        - Error-handling Testing: Ensures fire-and-forget semantics
          when MQTT publish raises.
    """

    @pytest.mark.anyio
    async def test_publishes_snapshot_as_retained_json(self) -> None:
        """Snapshot is published as compact retained JSON to _meta/registry."""
        from unittest.mock import AsyncMock

        from cosalette._wiring import publish_registry_snapshot

        # Arrange
        app = App(name="testapp", version="1.0.0")
        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/testapp"

        # Act
        await publish_registry_snapshot(app, mqtt, prefix)

        # Assert
        mqtt.publish.assert_awaited_once()
        call_args = mqtt.publish.call_args
        topic = call_args.args[0]
        payload = call_args.args[1]
        retain = call_args.kwargs["retain"]
        qos = call_args.kwargs["qos"]

        assert topic == "cosalette/testapp/_meta/registry"
        assert retain is True
        assert qos == 1

        # Payload must be dict matching snapshot structure
        assert isinstance(payload, dict)
        assert payload["app"]["name"] == "testapp"
        assert payload["app"]["version"] == "1.0.0"
        assert isinstance(payload["devices"], list)
        assert isinstance(payload["telemetry"], list)
        assert isinstance(payload["commands"], list)
        assert isinstance(payload["adapters"], list)

    @pytest.mark.anyio
    async def test_logs_and_continues_on_publish_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Publish failure is logged but not raised (fire-and-forget)."""
        from unittest.mock import AsyncMock

        from cosalette._wiring import publish_registry_snapshot

        # Arrange
        app = App(name="testapp", version="1.0.0")
        mqtt = AsyncMock(spec=MqttPort)
        mqtt.publish.side_effect = RuntimeError("broker down")
        prefix = "cosalette/testapp"

        # Act — should not raise
        with caplog.at_level(logging.ERROR, logger="cosalette._wiring"):
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert
        assert "Failed to publish registry" in caplog.text

    @pytest.mark.anyio
    async def test_logs_and_continues_on_snapshot_build_failure(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Snapshot build failure is logged but not raised (fire-and-forget).

        Technique: Error Guessing — verifying the full fire-and-forget
        contract covers snapshot construction, not only MQTT publish.
        """
        from unittest.mock import AsyncMock, patch

        from cosalette._wiring import publish_registry_snapshot

        # Arrange
        app = App(name="testapp", version="1.0.0")
        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/testapp"

        with (
            patch(
                "cosalette._introspect.build_registry_snapshot",
                side_effect=RuntimeError("bad registry"),
            ),
            caplog.at_level(logging.ERROR, logger="cosalette._wiring"),
        ):
            # Act — should not raise
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert
        assert "Failed to publish registry" in caplog.text
        mqtt.publish.assert_not_awaited()

    @pytest.mark.anyio
    async def test_warns_when_payload_exceeds_size_threshold(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A WARNING is logged when the serialized payload exceeds 128 KiB.

        Technique: Boundary Value Analysis — payload just over the
        ``_REGISTRY_PAYLOAD_WARN_BYTES`` threshold triggers a warning
        while publishing still proceeds (advisory only).
        """
        from unittest.mock import AsyncMock, patch

        from cosalette._wiring import (
            _REGISTRY_PAYLOAD_WARN_BYTES,
            publish_registry_snapshot,
        )

        # Arrange — build a snapshot dict that serializes above the threshold
        oversized_snapshot = {
            "app": {"name": "testapp", "version": "1.0.0"},
            "devices": [],
            "telemetry": [],
            "commands": [],
            "adapters": [],
            "padding": "x" * (_REGISTRY_PAYLOAD_WARN_BYTES + 1),
        }
        app = App(name="testapp", version="1.0.0")
        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/testapp"

        with (
            patch(
                "cosalette._introspect.build_registry_snapshot",
                return_value=oversized_snapshot,
            ),
            caplog.at_level(logging.WARNING, logger="cosalette._wiring"),
        ):
            # Act
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert — warning was emitted
        assert "large payloads may exceed broker max_packet_size" in caplog.text
        assert str(_REGISTRY_PAYLOAD_WARN_BYTES) in caplog.text

        # Assert — publish still happened (advisory only)
        mqtt.publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_no_warning_when_payload_at_or_below_threshold(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No WARNING is logged when the payload is at or below 128 KiB.

        Technique: Boundary Value Analysis — complement to the above-threshold
        test; payload exactly at ``_REGISTRY_PAYLOAD_WARN_BYTES`` must NOT
        trigger a warning.
        """
        from unittest.mock import AsyncMock, patch

        from cosalette._wiring import (
            _REGISTRY_PAYLOAD_WARN_BYTES,
            publish_registry_snapshot,
        )

        # Arrange — craft a snapshot whose UTF-8-encoded JSON is exactly at
        # the threshold.  We measure the overhead of the wrapper dict first,
        # then fill the padding to hit the exact byte count.
        shell: dict[str, object] = {
            "app": {"name": "t", "version": "0"},
            "devices": [],
            "telemetry": [],
            "commands": [],
            "adapters": [],
            "padding": "",
        }
        import json as _json

        overhead = len(_json.dumps(shell, separators=(",", ":")).encode("utf-8"))
        fill_size = _REGISTRY_PAYLOAD_WARN_BYTES - overhead
        shell["padding"] = "x" * fill_size

        app = App(name="t", version="0")
        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/t"

        with (
            patch(
                "cosalette._introspect.build_registry_snapshot",
                return_value=shell,
            ),
            caplog.at_level(logging.WARNING, logger="cosalette._wiring"),
        ):
            # Act
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert — no warning for at-threshold payload
        assert "large payloads" not in caplog.text
        mqtt.publish.assert_awaited_once()

    @pytest.mark.anyio
    async def test_populated_app_snapshot_includes_all_registrations(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Snapshot from an app with devices, telemetry, command, and adapter.

        Technique: Specification-based Testing — verifies the publish
        payload reflects real registrations (not just empty lists from a
        bare ``App``).  Also confirms no spurious size warning for a small
        payload.
        """
        from unittest.mock import AsyncMock

        from cosalette._wiring import publish_registry_snapshot

        # Arrange — build an app with diverse registrations
        app = App(name="myapp", version="2.0.0")

        @app.device("blind")
        async def _blind(ctx: DeviceContext) -> None:
            pass  # pragma: no cover

        @app.telemetry("temperature", interval=60)
        async def _temperature() -> dict[str, object]:
            return {"value": 21.5}  # pragma: no cover

        @app.command("set_mode")
        async def _set_mode(topic: str, payload: str) -> None:
            pass  # pragma: no cover

        app.adapter(_DummyPort, _DummyImpl)

        mqtt = AsyncMock(spec=MqttPort)
        prefix = "cosalette/myapp"

        with caplog.at_level(logging.WARNING, logger="cosalette._wiring"):
            # Act
            await publish_registry_snapshot(app, mqtt, prefix)

        # Assert — no spurious size warning for a small populated app
        assert "large payloads" not in caplog.text
        mqtt.publish.assert_awaited_once()
        payload_dict = mqtt.publish.call_args.args[1]
        assert isinstance(payload_dict, dict)

        assert payload_dict["app"]["name"] == "myapp"
        assert payload_dict["app"]["version"] == "2.0.0"

        # Devices
        device_names = [d["name"] for d in payload_dict["devices"]]
        assert "blind" in device_names

        # Telemetry
        telem_names = [t["name"] for t in payload_dict["telemetry"]]
        assert "temperature" in telem_names
        temp_reg = next(
            t for t in payload_dict["telemetry"] if t["name"] == "temperature"
        )
        assert temp_reg["interval"] == 60

        # Commands
        cmd_names = [c["name"] for c in payload_dict["commands"]]
        assert "set_mode" in cmd_names

        # Adapters
        adapter_ports = [a["port"] for a in payload_dict["adapters"]]
        assert "_DummyPort" in adapter_ports


class TestResolveSettings:
    """resolve_settings priority logic.

    Test Techniques Used:
    - Decision Table: Three-way priority (explicit > eager > class)
    - Branch Coverage: All three branches
    """

    def test_explicit_settings_wins(self) -> None:
        """Explicit settings override takes top priority.

        Technique: Decision Table — settings=X, eager=Y → X wins.
        """
        explicit = Settings()
        eager = Settings()
        result = resolve_settings(explicit, eager, Settings)
        assert result is explicit

    def test_eager_settings_used_when_no_explicit(self) -> None:
        """Eager settings used when no explicit override.

        Technique: Decision Table — settings=None, eager=Y → Y wins.
        """
        eager = Settings()
        result = resolve_settings(None, eager, Settings)
        assert result is eager

    def test_fresh_instance_when_none_provided(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fresh Settings created when both None.

        Technique: Decision Table — settings=None, eager=None → class().
        """
        monkeypatch.delenv("MQTT__HOST", raising=False)
        monkeypatch.delenv("MQTT__PORT", raising=False)
        result = resolve_settings(None, None, Settings)
        assert isinstance(result, Settings)
        assert result.mqtt.host == "localhost"


# ---------------------------------------------------------------------------
# TestStoreFactoryResolution
# ---------------------------------------------------------------------------


class TestStoreFactoryResolution:
    """Store factory resolution during _run_async lifecycle.

    Technique: Specification-based — verifying that callable store
    factories are invoked with DI-resolved settings during bootstrap.
    """

    async def test_factory_called_with_settings(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Factory receives settings via DI and produces the store."""
        factory_called = False

        def make_store(settings: Settings) -> NullStore:
            nonlocal factory_called
            factory_called = True
            assert isinstance(settings, Settings)
            return NullStore()

        app = App(name="testapp", store=make_store)

        @app.device("d")
        async def d(ctx: DeviceContext) -> None:
            pass

        shutdown = asyncio.Event()
        shutdown.set()
        await app._run_async(  # noqa: SLF001
            mqtt=mock_mqtt, shutdown_event=shutdown, clock=fake_clock
        )
        assert factory_called

    async def test_factory_non_store_raises(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Factory returning non-Store raises TypeError."""

        def bad_factory() -> Store:
            return "not a store"  # ty: ignore[invalid-return-type]

        app = App(name="testapp", store=bad_factory)

        @app.device("d")
        async def d(ctx: DeviceContext) -> None:
            pass

        shutdown = asyncio.Event()
        shutdown.set()
        with pytest.raises(TypeError, match="expected a Store"):
            await app._run_async(  # noqa: SLF001
                mqtt=mock_mqtt, shutdown_event=shutdown, clock=fake_clock
            )

    async def test_factory_receives_adapter_via_di(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Factory injected with registered adapter port type."""
        injected_adapter: list[object] = []

        def make_store(port: _DummyPort) -> NullStore:
            injected_adapter.append(port)
            return NullStore()

        app = App(name="testapp", store=make_store)
        app.adapter(_DummyPort, _DummyImpl)

        @app.device("d")
        async def d(ctx: DeviceContext) -> None:
            pass

        shutdown = asyncio.Event()
        shutdown.set()
        await app._run_async(  # noqa: SLF001
            mqtt=mock_mqtt, shutdown_event=shutdown, clock=fake_clock
        )
        assert len(injected_adapter) == 1
        assert isinstance(injected_adapter[0], _DummyImpl)

    async def test_async_factory_raises_at_bootstrap(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """An async store factory raises TypeError at bootstrap time."""

        async def async_factory() -> NullStore:
            return NullStore()

        app = App(name="testapp", store=async_factory)  # ty: ignore[invalid-argument-type]

        @app.device("d")
        async def d(ctx: DeviceContext) -> None:
            pass

        shutdown = asyncio.Event()
        shutdown.set()
        with pytest.raises(TypeError, match="async"):
            await app._run_async(  # noqa: SLF001
                mqtt=mock_mqtt, shutdown_event=shutdown, clock=fake_clock
            )

    async def test_factory_called_with_settings_and_adapter(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Factory receives both settings and adapter port via combined DI."""
        combined: list[tuple[Settings, object]] = []

        def make_store(settings: Settings, port: _DummyPort) -> NullStore:
            combined.append((settings, port))
            return NullStore()

        app = App(name="testapp", store=make_store)
        app.adapter(_DummyPort, _DummyImpl)

        @app.device("d")
        async def d(ctx: DeviceContext) -> None:
            pass

        shutdown = asyncio.Event()
        shutdown.set()
        await app._run_async(  # noqa: SLF001
            mqtt=mock_mqtt, shutdown_event=shutdown, clock=fake_clock
        )

        assert len(combined) == 1
        settings_arg, port_arg = combined[0]
        assert isinstance(settings_arg, Settings)
        assert isinstance(port_arg, _DummyImpl)

    async def test_persist_with_store_factory_integration(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """persist= on telemetry works end-to-end when store= is a factory."""
        from cosalette._persist import SaveOnPublish
        from cosalette._stores import MemoryStore

        backend = MemoryStore()

        def make_store() -> MemoryStore:
            return backend

        app = App(name="testapp", store=make_store)

        @app.telemetry("sensor", interval=0.001, persist=SaveOnPublish())
        async def sensor_handler(ctx: DeviceContext) -> dict[str, object]:
            ctx._shutdown_event.set()  # noqa: SLF001
            return {"value": 99}

        await asyncio.wait_for(
            app._run_async(
                mqtt=mock_mqtt,
                shutdown_event=asyncio.Event(),
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        # SaveOnPublish fires after each publish — backend must contain the sensor key
        # (DeviceStore saves its mutable state dict, not the MQTT payload)
        assert backend.load("sensor") is not None


# ---------------------------------------------------------------------------
# resolve_enabled — direct unit tests
# ---------------------------------------------------------------------------


class TestResolveEnabled:
    """Unit tests for :func:`cosalette._wiring.resolve_enabled`.

    Technique: Specification-based Testing and Branch/Condition Coverage.

    Tests the three branches of the enabled_spec dispatch:
    - callable spec → resolved and retained (truthy) or dropped (falsy)
    - literal True  → passed through unchanged
    - literal False → passed through (already absent in practice)
    """

    def _make_telemetry(self, name: str, enabled_spec: object) -> object:
        """Build a minimal _TelemetryRegistration for testing."""

        from cosalette._registration import _TelemetryRegistration

        async def fn() -> dict[str, object]:
            return {}

        return _TelemetryRegistration(
            name=name,
            func=fn,
            injection_plan=[],
            interval=10.0,
            enabled_spec=enabled_spec,  # ty: ignore[invalid-argument-type]
        )

    def _make_device(self, name: str, enabled_spec: object) -> object:
        """Build a minimal _DeviceRegistration for testing."""

        from cosalette._registration import _DeviceRegistration

        async def fn(ctx: DeviceContext) -> None:
            pass

        return _DeviceRegistration(
            name=name,
            func=fn,
            injection_plan=[],
            enabled_spec=enabled_spec,  # ty: ignore[invalid-argument-type]
        )

    def _make_command(self, name: str, enabled_spec: object) -> object:
        """Build a minimal _CommandRegistration for testing."""
        from cosalette._registration import _CommandRegistration

        async def fn(payload: str) -> dict[str, object]:
            return {}

        return _CommandRegistration(
            name=name,
            func=fn,
            injection_plan=[],
            mqtt_params=frozenset({"payload"}),
            enabled_spec=enabled_spec,  # ty: ignore[invalid-argument-type]
        )

    def test_callable_truthy_retains_telemetry(self) -> None:
        """A callable returning True keeps the registration."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel = [self._make_telemetry("x", lambda s: True)]
        dev: list = []
        cmd: list = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 1
        assert tel[0].enabled_spec is True  # ty: ignore[unresolved-attribute]

    def test_callable_falsy_removes_telemetry(self) -> None:
        """A callable returning False drops the registration."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel = [self._make_telemetry("x", lambda s: False)]
        dev: list = []
        cmd: list = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 0

    def test_literal_true_passed_through(self) -> None:
        """Literal True registrations are not modified."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel = [self._make_telemetry("x", True)]
        dev: list = []
        cmd: list = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 1
        assert tel[0].enabled_spec is True  # ty: ignore[unresolved-attribute]

    def test_resolve_enabled_propagates_to_devices_and_commands(self) -> None:
        """resolve_enabled processes devices and commands, not just telemetry."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel: list = []
        dev = [
            self._make_device("keep", lambda s: True),
            self._make_device("drop", lambda s: False),
        ]
        cmd = [
            self._make_command("keep", lambda s: True),
            self._make_command("drop", lambda s: False),
        ]

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert [r.name for r in dev] == ["keep"]  # ty: ignore[unresolved-attribute]
        assert [r.name for r in cmd] == ["keep"]  # ty: ignore[unresolved-attribute]

    def test_callable_enabled_persist_without_store_raises(self) -> None:
        """Deferred telemetry with persist= raises if no store configured."""

        from cosalette._persist import SaveOnPublish
        from cosalette._registration import _TelemetryRegistration
        from cosalette._wiring import resolve_enabled

        async def fn() -> dict[str, object]:
            return {}

        reg = _TelemetryRegistration(
            name="x",
            func=fn,
            injection_plan=[],
            interval=10.0,
            enabled_spec=lambda s: True,
            persist_policy=SaveOnPublish(),
        )
        settings = Settings()
        tel = [reg]

        with pytest.raises(ValueError, match="store="):
            resolve_enabled(tel, [], [], settings, store=None)

    def test_callable_settings_argument_received(self) -> None:
        """The callable receives the Settings instance passed to resolve_enabled."""
        from cosalette._wiring import resolve_enabled

        received: list[object] = []
        settings = Settings()

        def check(s: Settings) -> bool:
            received.append(s)
            return True

        tel = [self._make_telemetry("x", check)]
        resolve_enabled(tel, [], [], settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(received) == 1
        assert received[0] is settings

    def test_async_enabled_callable_raises_on_telemetry(self) -> None:
        """An async enabled= callable on telemetry raises TypeError."""
        from cosalette._wiring import resolve_enabled

        async def async_pred(s: Settings) -> bool:
            return True

        settings = Settings()
        tel = [self._make_telemetry("x", async_pred)]
        with pytest.raises(TypeError, match="async"):
            resolve_enabled(tel, [], [], settings, store=None)  # ty: ignore[invalid-argument-type]

    def test_async_enabled_callable_raises_on_device(self) -> None:
        """An async enabled= callable on a device raises TypeError."""
        from cosalette._wiring import resolve_enabled

        async def async_pred(s: Settings) -> bool:
            return True

        settings = Settings()
        dev = [self._make_device("x", async_pred)]
        with pytest.raises(TypeError, match="async"):
            resolve_enabled([], dev, [], settings, store=None)  # ty: ignore[invalid-argument-type]

    def test_per_device_config_passed_to_enabled_callable(self) -> None:
        """For dict-name registrations, per_device_config is forwarded."""

        from cosalette._registration import _DeviceRegistration
        from cosalette._wiring import _resolve_list_enabled

        received: list[object] = []

        def check_cfg(cfg: object) -> bool:
            received.append(cfg)
            return True

        async def fn(ctx: DeviceContext) -> None:
            pass

        class _SomeCfg:
            pass

        config = _SomeCfg()
        reg = _DeviceRegistration(
            name="dev",
            func=fn,
            injection_plan=[],
            enabled_spec=check_cfg,
            per_device_config=config,
        )
        settings = Settings()
        result = _resolve_list_enabled([reg], settings)

        assert len(result) == 1
        assert len(received) == 1
        assert received[0] is config

    def test_truthy_non_bool_retains_entry(self) -> None:
        """Callable returning a truthy non-bool value keeps the registration."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel = [self._make_telemetry("x", lambda s: 1)]
        dev: list = []
        cmd: list = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 1

    def test_falsy_non_bool_removes_entry(self) -> None:
        """Callable returning a falsy non-bool value drops the registration."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel = [self._make_telemetry("x", lambda s: 0)]
        dev: list = []
        cmd: list = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 0
