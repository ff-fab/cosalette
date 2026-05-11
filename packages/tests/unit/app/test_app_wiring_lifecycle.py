"""Tests for cosalette App — device wiring, lifespan integration, and lifecycle.

Covers: device wiring, concurrent execution, graceful shutdown, MQTT
subscriptions, topic prefix, client ID, lifespan startup/teardown, and
lifespan-yielded injectable state (ADR-027).

Test Techniques Used:
    - State Transition Testing: Device lifecycle (start → running → shutdown)
      and lifespan context entry/exit ordering.
    - Specification-based Testing: MQTT topic prefix, client ID, and
      subscription wiring contracts.
    - Integration Testing: Full App bootstrap with MockMqttClient and
      FakeClock to verify end-to-end wiring.
    - Error-handling Testing: Graceful shutdown under concurrent device tasks.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from cosalette._app import App
from cosalette._context import AppContext, DeviceContext
from cosalette._mqtt import MqttClient
from cosalette._settings import MqttSettings, Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.unit.conftest import _DummyImpl, _DummyPort

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_called.set()
            yield

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
        async def alpha(ctx: DeviceContext) -> AsyncIterator[None]:
            device_a_ran.set()
            yield

        @app.device("beta")
        async def beta(ctx: DeviceContext) -> AsyncIterator[None]:
            device_b_ran.set()
            yield

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_started.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_done.set()
            yield

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
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        @app.device("window")
        async def window(ctx: DeviceContext) -> AsyncIterator[None]:
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_done.set()
            yield

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_done.set()
            yield

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            nonlocal device_started
            device_started = True
            yield

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_done.set()
            yield

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_started.set()
            try:
                while not ctx.shutdown_requested:
                    await ctx.sleep(1)
                    yield
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
        async def device(ctx: DeviceContext) -> AsyncIterator[None]:
            device_ran.set()
            yield

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
        async def device(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        async def device(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        async def device(
            ctx: DeviceContext, state: _YieldedState
        ) -> AsyncIterator[None]:
            received.append(state)
            yield

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
        async def device(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        async def device(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
