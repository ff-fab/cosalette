"""Tests for @app.periodic background task registration and runtime.

Covers: registration, name collision, interval validation, enabled spec
(literal + deferred callable), timedelta conversion, runtime loop (exception
isolation, cancellation), and AppHarness.tick_periodic.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

import pytest

from cosalette._app import App
from cosalette._clock import ClockPort
from cosalette._periodic import _PeriodicRegistration, run_periodic
from cosalette._settings import Settings
from cosalette.testing import AppHarness, make_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> App:
    return App(name="testapp", version="1.0.0")


# ---------------------------------------------------------------------------
# TestPeriodicRegistration
# ---------------------------------------------------------------------------


class TestPeriodicRegistration:
    """Decorator stores correct registration; name collision; interval validation."""

    def test_registers_named_periodic(self, app: App) -> None:
        """Name is stored on _PeriodicRegistration."""

        @app.periodic("heartbeat", interval=30.0)
        async def heartbeat() -> None:
            pass

        assert len(app._periodic) == 1
        reg = app._periodic[0]
        assert reg.name == "heartbeat"
        assert reg.interval == 30.0

    def test_registers_with_default_name(self, app: App) -> None:
        """When name=None, function name is used."""

        @app.periodic(interval=10.0)
        async def my_task() -> None:
            pass

        assert app._periodic[0].name == "my_task"

    def test_timedelta_interval_converted(self, app: App) -> None:
        """timedelta is normalised to float seconds."""

        @app.periodic("sync", interval=datetime.timedelta(minutes=5))
        async def sync() -> None:
            pass

        assert app._periodic[0].interval == 300.0

    def test_name_collision_with_device_raises(self, app: App) -> None:
        """A periodic name that matches a device raises ValueError."""

        @app.device("sensor")
        async def sensor(ctx: object) -> None:  # noqa: ANN001
            pass

        with pytest.raises(ValueError, match="already registered"):

            @app.periodic("sensor", interval=10.0)
            async def duplicate() -> None:
                pass

    def test_name_collision_with_telemetry_raises(self, app: App) -> None:
        """A periodic name that matches telemetry raises ValueError."""

        @app.telemetry("temp", interval=10)
        async def temp() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="already registered"):

            @app.periodic("temp", interval=5.0)
            async def temp2() -> None:
                pass

    def test_duplicate_periodic_name_raises(self, app: App) -> None:
        """Two periodic tasks with the same name raises ValueError."""

        @app.periodic("task", interval=10.0)
        async def task1() -> None:
            pass

        with pytest.raises(ValueError, match="already registered"):

            @app.periodic("task", interval=20.0)
            async def task2() -> None:
                pass

    def test_zero_interval_raises(self, app: App) -> None:
        """interval=0 raises ValueError."""
        with pytest.raises(ValueError, match="must be positive"):

            @app.periodic("bad", interval=0.0)
            async def bad() -> None:
                pass

    def test_negative_interval_raises(self, app: App) -> None:
        """Negative interval raises ValueError."""
        with pytest.raises(ValueError, match="must be positive"):

            @app.periodic("bad", interval=-1.0)
            async def bad() -> None:
                pass

    def test_int_interval_accepted(self, app: App) -> None:
        """Integer intervals (e.g. interval=30) are accepted and stored."""

        @app.periodic("task", interval=30)
        async def task() -> None:
            pass

        assert app._periodic[0].interval == 30

    def test_zero_int_interval_raises(self, app: App) -> None:
        """interval=0 as int raises ValueError (not just float)."""
        with pytest.raises(ValueError, match="must be positive"):

            @app.periodic("bad", interval=0)
            async def bad() -> None:
                pass

    def test_deferred_enabled_name_collision_raises(self, app: App) -> None:
        """Deferred enabled= branch validates name collision at decoration time."""

        @app.periodic("existing", interval=10.0)
        async def existing() -> None:
            pass

        with pytest.raises(ValueError, match="already registered"):

            @app.periodic("existing", interval=5.0, enabled=lambda s: True)
            async def duplicate() -> None:
                pass

    def test_deferred_enabled_negative_interval_raises(self, app: App) -> None:
        """Deferred enabled= branch validates interval positivity at decoration time."""
        with pytest.raises(ValueError, match="must be positive"):

            @app.periodic("task", interval=-1.0, enabled=lambda s: True)
            async def task() -> None:
                pass

    def test_summary_and_behavior_stored(self, app: App) -> None:
        """summary= and behavior= are stored on the registration."""

        @app.periodic("task", interval=5.0, summary="does things", behavior=["polls"])
        async def task() -> None:
            pass

        reg = app._periodic[0]
        assert reg.summary == "does things"
        assert reg.behavior == ["polls"]


# ---------------------------------------------------------------------------
# TestPeriodicEnabled
# ---------------------------------------------------------------------------


class TestPeriodicEnabled:
    """Literal False skips; callable enabled= deferred."""

    def test_literal_false_skips_registration(self, app: App) -> None:
        """enabled=False means nothing is registered."""

        @app.periodic("skip", interval=10.0, enabled=False)
        async def skip() -> None:
            pass

        assert len(app._periodic) == 0

    def test_literal_true_registers(self, app: App) -> None:
        """enabled=True (default) registers normally."""

        @app.periodic("run", interval=10.0, enabled=True)
        async def run() -> None:
            pass

        assert len(app._periodic) == 1

    def test_callable_enabled_stored_as_spec(self, app: App) -> None:
        """callable enabled= is stored without resolution at decoration time."""

        @app.periodic("run", interval=10.0, enabled=lambda s: True)
        async def run() -> None:
            pass

        assert callable(app._periodic[0].enabled_spec)

    def test_callable_enabled_false_removes_at_bootstrap(self) -> None:
        """callable enabled=lambda s: False drops the registration during bootstrap."""
        app = App(name="testapp", version="1.0.0")

        @app.periodic("skip", interval=10.0, enabled=lambda s: False)
        async def skip() -> None:
            pass

        assert len(app._periodic) == 1  # stored, not yet resolved
        settings = make_settings()
        from cosalette._wiring import resolve_enabled

        resolve_enabled(
            app._telemetry,
            app._devices,
            app._commands,
            settings,
            None,
            periodic_list=app._periodic,
        )
        assert len(app._periodic) == 0


# ---------------------------------------------------------------------------
# TestPeriodicIntervalResolution
# ---------------------------------------------------------------------------


class TestPeriodicIntervalResolution:
    """Callable interval= deferred; SettingRef; zero/negative raises."""

    def test_callable_interval_resolved_at_bootstrap(self) -> None:
        """Callable interval is resolved by resolve_intervals_periodic."""
        app = App(name="testapp", version="1.0.0")

        @app.periodic("task", interval=lambda s: 42.0)
        async def task() -> None:
            pass

        settings = make_settings()
        from cosalette._wiring import resolve_intervals_periodic

        resolve_intervals_periodic(app._periodic, settings)
        assert app._periodic[0].interval == 42.0

    def test_setting_ref_resolved_at_bootstrap(self) -> None:
        """SettingRef interval is resolved by resolve_intervals_periodic."""
        from cosalette._settings_ref import setting_ref

        app = App(name="testapp", version="1.0.0")

        @app.periodic("task", interval=setting_ref("mqtt.reconnect_interval"))
        async def task() -> None:
            pass

        settings = make_settings()
        from cosalette._wiring import resolve_intervals_periodic

        resolve_intervals_periodic(app._periodic, settings)
        assert isinstance(app._periodic[0].interval, float)
        assert app._periodic[0].interval > 0

    def test_callable_resolves_to_zero_raises(self) -> None:
        """Callable interval that resolves to 0 raises ValueError."""
        app = App(name="testapp", version="1.0.0")

        @app.periodic("bad", interval=lambda s: 0.0)
        async def bad() -> None:
            pass

        settings = make_settings()
        from cosalette._wiring import resolve_intervals_periodic

        with pytest.raises(ValueError, match="must be positive"):
            resolve_intervals_periodic(app._periodic, settings)


# ---------------------------------------------------------------------------
# TestPeriodicRuntime
# ---------------------------------------------------------------------------


class TestPeriodicRuntime:
    """run_periodic() loop: runs, exception isolated, CancelledError stops."""

    async def test_loop_runs_handler(self) -> None:
        """run_periodic() calls the handler after sleeping."""
        calls: list[int] = []

        async def handler() -> None:
            calls.append(1)
            if len(calls) >= 2:
                raise asyncio.CancelledError

        reg = _PeriodicRegistration(
            name="test",
            func=handler,
            injection_plan=[],
            interval=0.001,
        )
        with pytest.raises(asyncio.CancelledError):
            await run_periodic(reg, {})

        assert len(calls) >= 1

    async def test_exception_logged_loop_continues(self) -> None:
        """Handler exception is logged; loop continues."""
        calls: list[int] = []

        async def handler() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("boom")
            if len(calls) >= 3:
                raise asyncio.CancelledError

        reg = _PeriodicRegistration(
            name="test",
            func=handler,
            injection_plan=[],
            interval=0.001,
        )
        with pytest.raises(asyncio.CancelledError):
            await run_periodic(reg, {})

        assert len(calls) >= 3  # continued after exception

    async def test_cancelled_error_stops_loop(self) -> None:
        """asyncio.CancelledError from handler propagates out."""
        called = 0

        async def handler() -> None:
            nonlocal called
            called += 1
            raise asyncio.CancelledError

        reg = _PeriodicRegistration(
            name="test",
            func=handler,
            injection_plan=[],
            interval=0.001,
        )
        with pytest.raises(asyncio.CancelledError):
            await run_periodic(reg, {})


# ---------------------------------------------------------------------------
# TestAppHarnessPeriodic
# ---------------------------------------------------------------------------


class TestAppHarnessPeriodic:
    """tick_periodic invokes handler; run_periodic=False skips spawning."""

    async def test_tick_periodic_invokes_handler(self) -> None:
        """tick_periodic calls the handler once."""
        harness = AppHarness.create()
        calls: list[int] = []

        @harness.app.periodic("cache", interval=60.0)
        async def cache() -> None:
            calls.append(1)

        await harness.tick_periodic("cache")
        assert calls == [1]

    async def test_tick_periodic_unknown_name_raises(self) -> None:
        """tick_periodic with unknown name raises ValueError."""
        harness = AppHarness.create()
        with pytest.raises(ValueError, match="No periodic task named"):
            await harness.tick_periodic("nonexistent")

    async def test_run_periodic_false_does_not_spawn(self) -> None:
        """AppHarness with run_periodic=False skips periodic task spawning."""
        harness = AppHarness.create(run_periodic=False)
        calls: list[int] = []

        @harness.app.periodic("counter", interval=0.001)
        async def counter() -> None:
            calls.append(1)

        async def _shutdown() -> None:
            await asyncio.sleep(0.01)
            harness.trigger_shutdown()

        asyncio.create_task(_shutdown())
        await harness.run()

        # With run_periodic=False, counter should never have been called
        assert calls == []

    async def test_tick_periodic_injects_settings_and_clock(self) -> None:
        """tick_periodic correctly injects Settings and ClockPort by type."""
        harness = AppHarness.create()
        received: dict[str, object] = {}

        class MySettings(Settings):
            pass

        harness.settings = make_settings()

        @harness.app.periodic("di-test", interval=60.0)
        async def di_handler(settings: Settings, clock: ClockPort) -> None:
            received["settings"] = settings
            received["clock"] = clock

        await harness.tick_periodic("di-test")

        assert isinstance(received["settings"], Settings)
        assert isinstance(received["clock"], ClockPort)

    async def test_tick_periodic_injects_logger(self) -> None:
        """tick_periodic provides a Logger keyed by logging.Logger."""
        harness = AppHarness.create()
        received: dict[str, object] = {}

        @harness.app.periodic("log-test", interval=60.0)
        async def log_handler(logger: logging.Logger) -> None:
            received["logger"] = logger

        await harness.tick_periodic("log-test")

        assert isinstance(received["logger"], logging.Logger)
