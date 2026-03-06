"""Tests for cosalette App — command routing and error handling.

Covers: command routing via MockMqttClient.deliver(), command handler
error publication, error publication failure resilience, and command
handler error logging.
"""

from __future__ import annotations

import asyncio
import unittest.mock
from unittest.mock import patch

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TestRunAsyncCommand — command routing and error handling tests
# ---------------------------------------------------------------------------


class TestRunAsyncCommand:
    """Command routing, error publication, and error resilience tests."""

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

        with patch("cosalette._command_runner.logger") as mock_logger:
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
