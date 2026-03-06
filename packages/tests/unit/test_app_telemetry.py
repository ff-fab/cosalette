"""Tests for cosalette App — telemetry publish strategies and error handling.

Covers: publish strategy integration (OnChange, Every), telemetry error
resilience, error deduplication state machine, recovery logging, and
heartbeat status updates from telemetry errors.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from cosalette._app import App
from cosalette._clock import ClockPort
from cosalette._context import DeviceContext
from cosalette._strategies import Every, OnChange
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TestTelemetryPublishStrategies
# ---------------------------------------------------------------------------


class TestTelemetryPublishStrategies:
    """Publish-strategy integration with the telemetry loop.

    Technique: Integration Testing — verify that publish strategies
    control when telemetry readings are actually published via MQTT.
    """

    async def test_telemetry_with_strategy_stores_registration(
        self,
        app: App,
    ) -> None:
        """publish= parameter is stored on _TelemetryRegistration."""
        strategy = OnChange()

        @app.telemetry("temp", interval=10, publish=strategy)
        async def temp() -> dict[str, object]:
            return {"celsius": 22.5}

        assert app._telemetry[0].publish_strategy is strategy  # noqa: SLF001

    async def test_telemetry_without_strategy_defaults_to_none(
        self,
        app: App,
    ) -> None:
        """Backward compat: omitting publish= defaults to None."""

        @app.telemetry("temp", interval=10)
        async def temp() -> dict[str, object]:
            return {"celsius": 22.5}

        assert app._telemetry[0].publish_strategy is None  # noqa: SLF001

    async def test_telemetry_strategy_suppresses_publish(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """OnChange() suppresses duplicate payloads.

        Technique: Integration Testing — the handler returns the same
        dict every call.  Only the first should be published.
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry("temp", interval=0.01, publish=OnChange())
        async def temp() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                enough.set()
            return {"celsius": 22.5}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
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

        assert call_count >= 3
        # Only first publish should have gone through (duplicates suppressed)
        state_messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(state_messages) == 1

    async def test_telemetry_none_return_suppresses_publish(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler returning None suppresses that cycle entirely."""
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry("temp", interval=0.01)
        async def temp() -> dict[str, object] | None:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                enough.set()
            return None

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
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

        assert call_count >= 3
        state_messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(state_messages) == 0

    async def test_telemetry_first_publish_always_goes_through(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """First reading is always published, even with a restrictive strategy.

        Every(seconds=9999) would suppress everything after binding,
        but the very first reading bypasses the strategy check.
        """
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        @app.telemetry("temp", interval=0.01, publish=Every(seconds=9999))
        async def temp() -> dict[str, object]:
            called.set()
            return {"celsius": 22.5}

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

        assert called.is_set()
        state_messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(state_messages) >= 1

    async def test_telemetry_strategy_on_published_called(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """on_published is called after a successful publish.

        Technique: Mock-based Testing — use a spy strategy to verify
        on_published is invoked.
        """
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()
        on_published_calls: list[bool] = []

        class SpyStrategy:
            """Strategy that records on_published calls."""

            def should_publish(
                self,
                current: dict[str, object],
                previous: dict[str, object] | None,
            ) -> bool:
                return True

            def on_published(self) -> None:
                on_published_calls.append(True)

            def _bind(self, clock: ClockPort) -> None:  # noqa: ARG002
                pass

        @app.telemetry("temp", interval=0.01, publish=SpyStrategy())
        async def temp() -> dict[str, object]:
            called.set()
            return {"celsius": 22.5}

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

        assert len(on_published_calls) >= 1


# ---------------------------------------------------------------------------
# TestRunAsyncTelemetry — telemetry error handling integration tests
# ---------------------------------------------------------------------------


class TestRunAsyncTelemetry:
    """Telemetry polling, error isolation, deduplication, and recovery tests."""

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

    async def test_telemetry_persistent_error_deduplicated(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Same exception type on every call publishes only once.

        Technique: State Transition Testing — healthy → error (published),
        error → error (same type, suppressed).
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry("sensor", interval=0.01)
        async def sensor(ctx: DeviceContext) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                enough.set()
            msg = "boom"
            raise RuntimeError(msg)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
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

        assert call_count >= 3
        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) == 1

    async def test_telemetry_different_error_types_not_suppressed(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Different exception types each trigger a publish.

        Technique: State Transition Testing — error(A) → error(B)
        publishes new error for each type change.
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()
        errors: list[type[Exception]] = [RuntimeError, OSError, ValueError]

        @app.telemetry("sensor", interval=0.01)
        async def sensor(ctx: DeviceContext) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count > len(errors):
                enough.set()
                # Need to keep raising to not trigger recovery
                msg = "extra"
                raise ValueError(msg)
            exc_type = errors[call_count - 1]
            msg = f"err{call_count}"
            raise exc_type(msg)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
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

        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) == 3

    async def test_telemetry_recovery_logged(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Recovery from error logs an INFO message.

        Technique: State Transition Testing — error → healthy transition
        produces a recovery log entry.

        Note: configure_logging clears caplog's handler, so we check
        the logger method was called via mock.
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        success = asyncio.Event()

        @app.telemetry("sensor", interval=0.01)
        async def sensor(ctx: DeviceContext) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = "fail"
                raise RuntimeError(msg)
            success.set()
            return {"ok": True}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await success.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
        with patch("cosalette._telemetry_runner.logger") as mock_logger:
            await asyncio.wait_for(
                app._run_async(
                    settings=make_settings(),
                    shutdown_event=shutdown,
                    mqtt=mock_mqtt,
                    clock=fake_clock,
                ),
                timeout=5.0,
            )

        recovery_calls = [
            call
            for call in mock_logger.info.call_args_list
            if len(call.args) >= 2
            and "recovered" in str(call.args[0])
            and "sensor" in str(call.args[1])
        ]
        assert len(recovery_calls) >= 1

    async def test_telemetry_error_after_recovery_published(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Recovery resets dedup; same error type after recovery is published again.

        Technique: State Transition Testing — full cycle:
        healthy → error (pub) → healthy (recovery) → error (pub again).
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        @app.telemetry("sensor", interval=0.01)
        async def sensor(ctx: DeviceContext) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = "first failure"
                raise RuntimeError(msg)
            if call_count == 2:
                return {"ok": True}
            if call_count == 3:
                msg = "second failure"
                raise RuntimeError(msg)
            enough.set()
            # Keep raising to avoid extra recovery publish
            msg = "still failing"
            raise RuntimeError(msg)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await enough.wait()
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

        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) == 2

    async def test_telemetry_error_updates_heartbeat_status(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Telemetry error sets device status to 'error' in heartbeat payload.

        After recovery the status returns to 'ok'.  Uses a short
        heartbeat interval so heartbeats are published while the device
        cycles through error → recovery states.

        Technique: State Transition Testing — error/recovery reflected
        in health heartbeat payload.
        """

        app = App(name="testapp", version="1.0.0", heartbeat_interval=0.02)
        call_count = 0
        recovery = asyncio.Event()

        @app.telemetry("sensor", interval=0.01)
        async def sensor(ctx: DeviceContext) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                msg = "sensor broken"
                raise RuntimeError(msg)
            recovery.set()
            return {"value": 42}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await recovery.wait()
            # Let a couple more heartbeats fire after recovery
            await asyncio.sleep(0.1)
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

        # Parse all heartbeat payloads (skip "offline" shutdown message)
        status_messages = mock_mqtt.get_messages_for("testapp/status")
        heartbeats = [
            json.loads(payload)
            for payload, *_ in status_messages
            if payload.startswith("{")
        ]

        device_statuses = [
            hb.get("devices", {}).get("sensor", {}).get("status") for hb in heartbeats
        ]

        # At least one heartbeat should show the device in "error" state
        assert "error" in device_statuses, (
            f"No heartbeat with error status found. Statuses: {device_statuses}"
        )

        # After recovery, at least one heartbeat should show "ok"
        # Find the last "error" index; an "ok" must appear after it
        last_error_idx = len(device_statuses) - 1 - device_statuses[::-1].index("error")
        ok_after_recovery = [
            s for s in device_statuses[last_error_idx + 1 :] if s == "ok"
        ]
        assert len(ok_after_recovery) >= 1, (
            f"No 'ok' heartbeat after recovery. Statuses: {device_statuses}"
        )
