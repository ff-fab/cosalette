"""Tests for App registration — deferred interval, root devices, deferred enabled.

Covers: IntervalSpec callable/SettingRef intervals, root (unnamed) device
registration, and callable enabled= spec resolved at bootstrap.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._persistence._persist import SaveOnPublish
from cosalette._settings import Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers — deferred interval tests
# ---------------------------------------------------------------------------


async def _dummy_telemetry() -> dict[str, object]:
    """Minimal telemetry handler for registration tests."""
    return {"value": 1}


# ---------------------------------------------------------------------------
# TestDeferredIntervalResolution
# ---------------------------------------------------------------------------


class TestDeferredIntervalResolution:
    """Tests for IntervalSpec — callable intervals resolved at runtime.

    Technique: Specification-based Testing — verify that callable intervals
    are accepted at registration, resolved after settings are available,
    and validated for positive values.

    **What:** ``IntervalSpec = float | Callable[[Settings], float]`` allows
    deferred interval resolution so apps that derive intervals from settings
    don't crash when ``app.settings`` is ``None`` (e.g. ``--help``).

    **Why:** PEP 695 ``type`` statement provides a clean type alias.
    The callable is resolved in ``_run_async`` after ``_wiring.resolve_settings()``,
    keeping the happy path identical for downstream code.
    """

    def test_callable_interval_accepted_at_registration(self) -> None:
        """add_telemetry accepts a callable as interval."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)

        app.add_telemetry(
            "sensor",
            _dummy_telemetry,
            interval=lambda s: s.mqtt.reconnect_interval,
        )
        assert callable(app._telemetry[0].interval)  # noqa: SLF001

    def test_callable_interval_resolved_in_run_async(self) -> None:
        """Callable intervals are resolved to floats before device tasks start."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)
        expected = app.settings.mqtt.reconnect_interval

        app.add_telemetry(
            "sensor",
            _dummy_telemetry,
            interval=lambda s: s.mqtt.reconnect_interval,
        )

        # Resolve manually (same as _run_async does internally)
        app._resolve_intervals(app.settings)  # noqa: SLF001
        assert app._telemetry[0].interval == expected  # noqa: SLF001
        assert not callable(app._telemetry[0].interval)  # noqa: SLF001

    def test_float_interval_unchanged_by_resolution(self) -> None:
        """Float intervals pass through _resolve_intervals unchanged."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)

        app.add_telemetry("sensor", _dummy_telemetry, interval=5.0)
        app._resolve_intervals(app.settings)  # noqa: SLF001
        assert app._telemetry[0].interval == 5.0  # noqa: SLF001

    def test_callable_interval_negative_raises(self) -> None:
        """Callable that returns non-positive value raises ValueError."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)

        app.add_telemetry("sensor", _dummy_telemetry, interval=lambda s: -1.0)
        with pytest.raises(ValueError, match="must be positive"):
            app._resolve_intervals(app.settings)  # noqa: SLF001

    def test_callable_interval_zero_raises(self) -> None:
        """Callable that returns zero raises ValueError."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)

        app.add_telemetry("sensor", _dummy_telemetry, interval=lambda s: 0.0)
        with pytest.raises(ValueError, match="must be positive"):
            app._resolve_intervals(app.settings)  # noqa: SLF001

    def test_decorator_accepts_callable_interval(self) -> None:
        """@app.telemetry decorator accepts callable interval."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)

        @app.telemetry("sensor", interval=lambda s: 10.0)
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        assert callable(app._telemetry[0].interval)  # noqa: SLF001

    def test_root_decorator_accepts_callable_interval(self) -> None:
        """Root @app.telemetry (name=None) accepts callable interval."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)

        @app.telemetry(interval=lambda s: 10.0)
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        assert callable(app._telemetry[0].interval)  # noqa: SLF001

    def test_no_crash_with_missing_settings_and_callable_interval(self) -> None:
        """Registration with callable interval succeeds even when settings are None.

        This is the core --help/--version fix: apps can register with
        callable intervals without requiring valid settings at import time.
        """
        from pydantic_settings import BaseSettings

        class NeedsField(BaseSettings):
            required_field: str  # no default → validation fails

        app = App(
            name="testapp",
            version="0.0.1",
            settings_class=NeedsField,  # ty: ignore[invalid-argument-type]
        )
        assert app._settings is None  # noqa: SLF001

        # Registration with callable interval doesn't touch app.settings
        app.add_telemetry(
            "sensor",
            _dummy_telemetry,
            interval=lambda s: 5.0,
        )
        assert callable(app._telemetry[0].interval)  # noqa: SLF001

    def test_mixed_float_and_callable_intervals(self) -> None:
        """Mix of float and callable intervals are all resolved correctly."""
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)

        app.add_telemetry("a", _dummy_telemetry, interval=5.0)
        app.add_telemetry("b", _dummy_telemetry, interval=lambda s: 10.0)
        app.add_telemetry("c", _dummy_telemetry, interval=30.0)

        app._resolve_intervals(app.settings)  # noqa: SLF001

        assert app._telemetry[0].interval == 5.0  # noqa: SLF001
        assert app._telemetry[1].interval == 10.0  # noqa: SLF001
        assert app._telemetry[2].interval == 30.0  # noqa: SLF001

    def test_int_interval_unchanged_by_resolution(self) -> None:
        """Integer intervals pass through _resolve_intervals unchanged.

        Confirms that ``interval=10`` (int, not float) works correctly
        — ``callable(10)`` is False, so the resolution step skips it.
        """
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)

        app.add_telemetry("sensor", _dummy_telemetry, interval=10)
        app._resolve_intervals(app.settings)  # noqa: SLF001
        assert app._telemetry[0].interval == 10  # noqa: SLF001

    def test_setting_ref_interval_resolution(self) -> None:
        """SettingRef intervals are resolved correctly like lambda callables."""
        import cosalette
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)
        expected = app.settings.mqtt.reconnect_interval

        app.add_telemetry(
            "sensor",
            _dummy_telemetry,
            interval=cosalette.setting_ref("mqtt.reconnect_interval"),
        )

        # Before resolution, it should still be a SettingRef
        assert isinstance(app._telemetry[0].interval, cosalette.SettingRef)  # noqa: SLF001

        # Resolve manually (same as _run_async does internally)
        app._resolve_intervals(app.settings)  # noqa: SLF001

        # After resolution, it should be the actual float value
        assert app._telemetry[0].interval == expected  # noqa: SLF001
        assert isinstance(app._telemetry[0].interval, float)  # noqa: SLF001

    def test_setting_ref_decorator_interval_resolution(self) -> None:
        """SettingRef works with @app.telemetry decorator."""
        import cosalette
        from cosalette.testing._settings import _IsolatedSettings

        app = App(name="testapp", version="1.0.0", settings_class=_IsolatedSettings)
        expected = app.settings.mqtt.reconnect_interval

        @app.telemetry(
            "sensor", interval=cosalette.setting_ref("mqtt.reconnect_interval")
        )
        async def sensor() -> dict[str, object] | None:
            return {"value": 42.0}

        # Before resolution, it should still be a SettingRef
        assert isinstance(app._telemetry[0].interval, cosalette.SettingRef)  # noqa: SLF001

        # Resolve manually (same as _run_async does internally)
        app._resolve_intervals(app.settings)  # noqa: SLF001

        # After resolution, it should be the actual float value
        assert app._telemetry[0].interval == expected  # noqa: SLF001


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

            @app.device  # ty: ignore[invalid-argument-type]
            async def sensor(ctx: DeviceContext) -> None:
                await ctx.sleep(999)

    def test_bare_command_decorator_raises_type_error(self) -> None:
        """@app.command (no parens) raises TypeError with clear message.

        Same guard as @app.device — catches missing parentheses and
        provides an actionable error message.
        """
        app = App(name="testapp", version="1.0.0")

        with pytest.raises(TypeError, match="parentheses required"):

            @app.command  # ty: ignore[invalid-argument-type]
            async def valve(payload: str) -> dict[str, object]:
                return {"state": payload}

    def test_bare_telemetry_decorator_raises_error(self) -> None:
        """@app.telemetry (no parens) raises ValueError.

        Unlike @app.device and @app.command, telemetry() requires
        either ``interval`` or ``schedule``.  Using the bare decorator
        triggers the mutual-exclusivity validation.
        """
        app = App(name="testapp", version="1.0.0")

        with pytest.raises(ValueError, match="interval.*schedule"):

            @app.telemetry  # ty: ignore[invalid-argument-type]
            async def sensor() -> dict[str, object]:
                return {"temp": 21.5}

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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            device_started.set()
            await ctx.publish_state({"value": 42})
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

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


# ---------------------------------------------------------------------------
# Deferred enabled= (callable EnabledSpec)
# ---------------------------------------------------------------------------


class TestDeferredEnabledSpec:
    """Tests for callable ``enabled=`` (EnabledSpec) on decorator registrations.

    Technique: Specification-based Testing and Equivalence Partitioning.

    The three equivalence classes for ``enabled=`` are:
    - Literal ``False`` — skip at decoration time (existing behaviour).
    - Literal ``True`` — register immediately (existing behaviour).
    - Callable — store as deferred spec; resolve at bootstrap.

    This class covers the callable partition across all three decorator
    types, plus key deferred-validation scenarios.
    """

    # --- Decoration-time storage ----------------------------------------

    def test_telemetry_callable_enabled_defers_registration(self, app: App) -> None:
        """Callable enabled= stores the spec without resolving it."""

        @app.telemetry("mag", interval=10, enabled=lambda s: True)
        async def magnetometer() -> dict[str, object]:
            return {}

        assert len(app._telemetry) == 1  # noqa: SLF001
        reg = app._telemetry[0]  # noqa: SLF001
        assert callable(reg.enabled_spec)

    def test_device_callable_enabled_defers_registration(self, app: App) -> None:
        """Callable enabled= on @app.device stores the spec."""

        @app.device("sensor", enabled=lambda s: True)
        async def sensor(ctx: DeviceContext) -> None:
            pass

        assert len(app._devices) == 1  # noqa: SLF001
        assert callable(app._devices[0].enabled_spec)  # noqa: SLF001

    def test_command_callable_enabled_defers_registration(self, app: App) -> None:
        """Callable enabled= on @app.command stores the spec."""

        @app.command("relay", enabled=lambda s: True)
        async def relay(payload: str) -> dict[str, object]:
            return {"state": payload}

        assert len(app._commands) == 1  # noqa: SLF001
        assert callable(app._commands[0].enabled_spec)  # noqa: SLF001

    def test_callable_enabled_does_not_raise_persist_at_decoration_time(
        self, app: App
    ) -> None:
        """persist= on a callable-enabled telemetry does NOT raise at decoration.

        Without a store configured, persist= would raise for a literal-True
        ``enabled``.  With callable ``enabled``, the check is deferred.
        """

        # No store configured on app — normally raises ValueError immediately.
        @app.telemetry(
            "x", interval=10, persist=SaveOnPublish(), enabled=lambda s: True
        )
        async def temp() -> dict[str, object]:
            return {}

        assert len(app._telemetry) == 1  # noqa: SLF001

    def test_literal_false_still_skips_immediately(self, app: App) -> None:
        """Literal False still skips at decoration time (backward compat)."""

        @app.telemetry("x", interval=10, enabled=False)
        async def temp() -> dict[str, object]:
            return {}

        assert len(app._telemetry) == 0  # noqa: SLF001

    def test_literal_true_still_registers_immediately(self, app: App) -> None:
        """Literal True still registers at decoration time with resolved spec."""

        @app.telemetry("x", interval=10, enabled=True)
        async def temp() -> dict[str, object]:
            return {}

        assert len(app._telemetry) == 1  # noqa: SLF001
        assert app._telemetry[0].enabled_spec is True  # noqa: SLF001

    # --- Bootstrap resolution (runs through _run_async) -------------------

    async def test_factory_returning_true_keeps_device(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A factory returning True keeps the telemetry in the registry at runtime."""
        ran = asyncio.Event()
        app = App(name="testapp")

        @app.telemetry("mag", interval=0.05, enabled=lambda s: True)
        async def magnetometer() -> dict[str, object]:
            ran.set()
            return {"bx": 1.0}

        shutdown = asyncio.Event()

        async def stop() -> None:
            await asyncio.sleep(0.3)
            shutdown.set()

        asyncio.create_task(stop())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert ran.is_set(), "Enabled device should have run"

    async def test_factory_returning_false_removes_device(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A factory returning False removes the telemetry at bootstrap."""
        ran = asyncio.Event()
        app = App(name="testapp")

        @app.telemetry("mag", interval=0.05, enabled=lambda s: False)
        async def magnetometer() -> dict[str, object]:
            ran.set()
            return {"bx": 1.0}

        # Need at least one device for the app to have work to do
        @app.device("d")
        async def dummy(ctx: DeviceContext) -> None:
            pass

        shutdown = asyncio.Event()

        async def stop() -> None:
            await asyncio.sleep(0.2)
            shutdown.set()

        asyncio.create_task(stop())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert not ran.is_set(), "Disabled device should never execute"

    async def test_factory_receives_settings(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """The enabled factory is called with the resolved Settings instance."""
        received: list[Settings] = []

        class MySettings(Settings): ...

        def check_settings(s: MySettings) -> bool:
            received.append(s)
            return False

        app = App(name="testapp", settings_class=MySettings)

        @app.telemetry("x", interval=10, enabled=check_settings)
        async def temp() -> dict[str, object]:
            return {}

        @app.device("d")
        async def dummy(ctx: DeviceContext) -> None:
            pass

        shutdown = asyncio.Event()
        shutdown.set()
        await asyncio.wait_for(
            app._run_async(
                settings=MySettings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )
        assert len(received) == 1
        assert isinstance(received[0], MySettings)
