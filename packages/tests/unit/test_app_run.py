"""Tests for App.run() — the synchronous public entrypoint.

Test Techniques Used:
    - Integration Testing: run() with injected MockMqttClient + FakeClock
    - Signal Handling: Verify SIGTERM/SIGINT trigger graceful shutdown
    - Thread-safety: run() called from main thread, uses asyncio.run()
    - Error Isolation: KeyboardInterrupt suppressed cleanly
    - CLI separation: verify cli() still builds the Typer CLI path

See Also:
    ADR-001 — Framework architecture.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import threading
from unittest.mock import AsyncMock, patch

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette.testing import FakeClock, MockMqttClient, make_settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> App:
    """Minimal App instance for run() tests."""
    return App(name="testapp", version="1.0.0")


# ---------------------------------------------------------------------------
# TestRunSyncEntrypoint
# ---------------------------------------------------------------------------


class TestRunSyncEntrypoint:
    """App.run() synchronous entrypoint tests.

    Technique: Integration Testing — run() with injected test doubles
    to verify the full lifecycle without real I/O.
    """

    def test_run_starts_and_stops_cleanly(self, app: App) -> None:
        """run() with immediate shutdown completes without error.

        Coordination: pre-set shutdown_event so _run_async returns
        immediately after bootstrap.
        """
        shutdown = asyncio.Event()
        shutdown.set()

        app.run(
            mqtt=MockMqttClient(),
            settings=make_settings(),
            shutdown_event=shutdown,
            clock=FakeClock(),
        )
        # If we reach here, run() completed without raising.

    def test_run_executes_device_function(self, app: App) -> None:
        """run() executes registered device functions.

        Coordination: device sets a flag, a background task then
        triggers shutdown.
        """
        device_ran = False
        shutdown = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            nonlocal device_ran
            device_ran = True
            shutdown.set()

        app.run(
            mqtt=MockMqttClient(),
            settings=make_settings(),
            shutdown_event=shutdown,
            clock=FakeClock(),
        )

        assert device_ran

    def test_run_with_mock_mqtt(self, app: App) -> None:
        """run(mqtt=MockMqttClient()) works for programmatic/testing use.

        This is the primary use-case: users pass a mock MQTT client
        so no broker is needed.
        """
        mock_mqtt = MockMqttClient()
        shutdown = asyncio.Event()
        shutdown.set()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            await ctx.publish_state({"value": 42})

        app.run(
            mqtt=mock_mqtt,
            settings=make_settings(),
            shutdown_event=shutdown,
            clock=FakeClock(),
        )

        # The availability message should have been published
        messages = mock_mqtt.get_messages_for("testapp/sensor/availability")
        assert len(messages) >= 1

    def test_run_suppresses_keyboard_interrupt(self, app: App) -> None:
        """run() suppresses KeyboardInterrupt for clean Ctrl-C exit.

        Technique: Mock _run_async to raise KeyboardInterrupt and
        verify run() does not propagate it.
        """
        with patch.object(
            app,
            "_run_async",
            new_callable=AsyncMock,
            side_effect=KeyboardInterrupt,
        ):
            # Should NOT raise
            app.run()

    def test_run_propagates_system_exit(self, app: App) -> None:
        """run() lets SystemExit propagate (e.g. config errors).

        SystemExit is intentional (bad config, --version) and must
        not be swallowed.
        """
        with (
            patch.object(
                app,
                "_run_async",
                new_callable=AsyncMock,
                side_effect=SystemExit(1),
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            app.run()

    def test_run_propagates_runtime_errors(self, app: App) -> None:
        """run() lets unexpected exceptions propagate."""
        with (
            patch.object(
                app,
                "_run_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            app.run()

    def test_run_passes_all_kwargs_to_run_async(self, app: App) -> None:
        """run() forwards mqtt, settings, shutdown_event, clock to _run_async."""
        mock_mqtt = MockMqttClient()
        settings = make_settings()
        shutdown = asyncio.Event()
        shutdown.set()
        clock = FakeClock()

        with patch.object(app, "_run_async", new_callable=AsyncMock) as mock_run_async:
            app.run(
                mqtt=mock_mqtt,
                settings=settings,
                shutdown_event=shutdown,
                clock=clock,
            )

        mock_run_async.assert_awaited_once_with(
            mqtt=mock_mqtt,
            settings=settings,
            shutdown_event=shutdown,
            clock=clock,
        )

    def test_run_with_no_args_calls_run_async_with_nones(self, app: App) -> None:
        """run() with no arguments passes all None defaults."""
        with patch.object(app, "_run_async", new_callable=AsyncMock) as mock_run_async:
            app.run()

        mock_run_async.assert_awaited_once_with(
            mqtt=None,
            settings=None,
            shutdown_event=None,
            clock=None,
        )


# ---------------------------------------------------------------------------
# TestRunSignalHandling
# ---------------------------------------------------------------------------


class TestRunSignalHandling:
    """Signal handling via run().

    Technique: Integration Testing — verify that SIGTERM/SIGINT
    trigger graceful shutdown when no shutdown_event is injected.
    Signal handlers are installed by _run_async._install_signal_handlers
    when shutdown_event=None.
    """

    def test_sigterm_triggers_shutdown(self, app: App) -> None:
        """SIGTERM triggers graceful shutdown during run().

        Coordination: a background thread sends SIGTERM after a
        brief delay, which triggers the signal handler installed
        by _run_async.
        """
        device_started = threading.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            device_started.set()
            # Block until cancelled (shutdown will cancel us)
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.Event().wait()

        def send_signal() -> None:
            device_started.wait(timeout=5.0)
            import os

            os.kill(os.getpid(), signal.SIGTERM)

        t = threading.Thread(target=send_signal, daemon=True)
        t.start()

        # run() should return cleanly after SIGTERM
        app.run(
            mqtt=MockMqttClient(),
            settings=make_settings(),
            clock=FakeClock(),
        )
        t.join(timeout=2.0)

    def test_sigint_triggers_shutdown(self, app: App) -> None:
        """SIGINT (Ctrl-C) triggers graceful shutdown during run().

        Same coordination pattern as SIGTERM test.
        """
        device_started = threading.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            device_started.set()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.Event().wait()

        def send_signal() -> None:
            device_started.wait(timeout=5.0)
            import os

            os.kill(os.getpid(), signal.SIGINT)

        t = threading.Thread(target=send_signal, daemon=True)
        t.start()

        app.run(
            mqtt=MockMqttClient(),
            settings=make_settings(),
            clock=FakeClock(),
        )
        t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# TestCliMethod
# ---------------------------------------------------------------------------


class TestCliMethod:
    """App.cli() method tests.

    Technique: Behavioural Testing — verify cli() still delegates
    to build_cli and Typer.
    """

    def test_cli_method_builds_and_invokes_typer(self, app: App) -> None:
        """cli() delegates to build_cli().

        We mock build_cli to verify cli() calls it with the App instance.
        """
        from unittest.mock import MagicMock

        mock_typer = MagicMock()

        with patch("cosalette._cli.build_cli", return_value=mock_typer) as mock_build:
            app.cli()

        mock_build.assert_called_once_with(app)
        mock_typer.assert_called_once_with(standalone_mode=True)
