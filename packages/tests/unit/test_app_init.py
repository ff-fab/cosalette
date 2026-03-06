"""Tests for cosalette App — init= callback parameter.

Covers: init= registration, fail-fast validation, DI into init callbacks,
type-based injection into handlers, type collision guards, command init
caching, async callable detection, and runtime init failure handling.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._registration import _call_init
from cosalette._settings import Settings
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.unit.conftest import _FakeFilter

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TestInitCallback
# ---------------------------------------------------------------------------


class TestInitCallback:
    """init= callback parameter tests for all three decorators.

    Technique: Specification-based Testing — verifying fail-fast
    validation, DI into the init callback, type-based injection of
    the result into handlers, type collision guards, and command
    init caching.
    """

    # --- Registration / fail-fast ---

    def test_telemetry_init_stored(self, app: App) -> None:
        """init= and init_injection_plan stored on _TelemetryRegistration."""

        def make_filter() -> _FakeFilter:
            return _FakeFilter()

        @app.telemetry("temp", interval=10, init=make_filter)
        async def temp(f: _FakeFilter) -> dict[str, object]:
            return {"v": f.update(1.0)}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.init is make_filter
        assert reg.init_injection_plan == []

    def test_device_init_stored(self, app: App) -> None:
        """init= and init_injection_plan stored on _DeviceRegistration."""

        def make_filter() -> _FakeFilter:
            return _FakeFilter()

        @app.device("dev", init=make_filter)
        async def dev(ctx: DeviceContext, f: _FakeFilter) -> None:
            pass

        reg = app._devices[0]  # noqa: SLF001
        assert reg.init is make_filter
        assert reg.init_injection_plan == []

    def test_command_init_stored(self, app: App) -> None:
        """init= and init_injection_plan stored on _CommandRegistration."""

        def make_filter() -> _FakeFilter:
            return _FakeFilter()

        @app.command("valve", init=make_filter)
        async def valve(payload: str, f: _FakeFilter) -> dict[str, object]:
            return {"pos": payload}

        reg = app._commands[0]  # noqa: SLF001
        assert reg.init is make_filter
        assert reg.init_injection_plan == []

    def test_init_default_none(self, app: App) -> None:
        """Omitting init= defaults to None — backward compat."""

        @app.telemetry("temp", interval=10)
        async def temp() -> dict[str, object]:
            return {"v": 1}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.init is None
        assert reg.init_injection_plan is None

    def test_init_fail_fast_bad_signature(self, app: App) -> None:
        """init= with un-annotated parameters raises TypeError at decoration time.

        Technique: Error Guessing — missing type annotations are caught
        immediately by build_injection_plan, not at runtime.
        """

        def bad_init(some_arg):  # noqa: ANN001, ANN202
            return _FakeFilter()

        with pytest.raises(TypeError, match="no type annotation"):

            @app.telemetry("temp", interval=10, init=bad_init)
            async def temp(f: _FakeFilter) -> dict[str, object]:
                return {"v": 1}

    def test_init_fail_fast_async_callable(self, app: App) -> None:
        """init= with an async callable raises TypeError at decoration time.

        Technique: Error Guessing — async init would silently return
        an unawaited coroutine. The framework rejects this at
        registration time.
        """

        async def async_init() -> _FakeFilter:
            return _FakeFilter()

        with pytest.raises(TypeError, match="synchronous callable"):

            @app.telemetry("temp", interval=10, init=async_init)
            async def temp(f: _FakeFilter) -> dict[str, object]:
                return {"v": 1}

    def test_init_with_settings_injection_plan(self, app: App) -> None:
        """init= that declares Settings parameter records it in the plan."""

        def make_filter(settings: Settings) -> _FakeFilter:
            return _FakeFilter(factor=2.0)

        @app.telemetry("temp", interval=10, init=make_filter)
        async def temp(f: _FakeFilter) -> dict[str, object]:
            return {"v": 1}

        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.init_injection_plan is not None
        assert len(reg.init_injection_plan) == 1
        assert reg.init_injection_plan[0] == ("settings", Settings)

    # --- Integration: telemetry ---

    async def test_telemetry_init_called_and_injected(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """init= result is injected into telemetry handler by type.

        Technique: Integration Testing — register telemetry with init=
        that returns a _FakeFilter. Handler declares _FakeFilter param.
        Verify handler receives it and the filter persists across calls.
        """
        app = App(name="testapp", version="1.0.0")
        call_count = 0
        enough = asyncio.Event()

        def make_filter() -> _FakeFilter:
            return _FakeFilter(factor=2.0)

        @app.telemetry("temp", interval=0.01, init=make_filter)
        async def temp(f: _FakeFilter) -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                enough.set()
            return {"v": f.update(1.0)}

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
        # Filter persists: call_count on the filter should match handler calls
        state_messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(state_messages) >= 1
        # First message should have factor applied: 1.0 * 2.0 = 2.0
        first_payload = json.loads(state_messages[0][0])
        assert first_payload["v"] == 2.0

    # --- Integration: device ---

    async def test_device_init_called_and_injected(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """init= result is injected into device handler by type.

        Technique: Integration Testing — device handler receives a
        _FakeFilter created by init= and uses it during its loop.
        """
        app = App(name="testapp", version="1.0.0")
        filter_used = asyncio.Event()
        captured_value: list[float] = []

        def make_filter() -> _FakeFilter:
            return _FakeFilter(factor=3.0)

        @app.device("sensor", init=make_filter)
        async def sensor(ctx: DeviceContext, f: _FakeFilter) -> None:
            result = f.update(10.0)
            captured_value.append(result)
            filter_used.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await filter_used.wait()
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

        assert filter_used.is_set()
        assert captured_value == [30.0]

    # --- Integration: command ---

    async def test_command_init_called_and_injected(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """init= result is injected into command handler by type.

        Technique: Integration Testing — command handler receives a
        _FakeFilter created by init=. The filter persists across
        multiple command invocations (called once, cached).
        """
        app = App(name="testapp", version="1.0.0")
        results: list[float] = []
        all_received = asyncio.Event()

        def make_filter() -> _FakeFilter:
            return _FakeFilter(factor=5.0)

        @app.command("valve", init=make_filter)
        async def valve(payload: str, f: _FakeFilter) -> dict[str, object]:
            result = f.update(float(payload))
            results.append(result)
            if len(results) >= 2:
                all_received.set()
            return {"v": result}

        shutdown = asyncio.Event()

        async def simulate_commands() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/valve/set", "1.0")
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/valve/set", "2.0")
            await all_received.wait()
            await asyncio.sleep(0.05)
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

        assert len(results) >= 2
        # factor=5.0: 1.0*5=5.0, 2.0*5=10.0
        assert results[0] == 5.0
        assert results[1] == 10.0

    # --- Init receives DI ---

    async def test_init_receives_injection(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """init= callback receives DI-resolved dependencies (Settings).

        Technique: Integration Testing — init callback declares a
        Settings parameter, verifies it receives the actual settings
        instance.
        """
        app = App(name="testapp", version="1.0.0")
        captured_factor: list[float] = []
        called = asyncio.Event()

        def make_filter(settings: Settings) -> _FakeFilter:
            # Use a settings value to prove injection works
            return _FakeFilter(factor=settings.mqtt.reconnect_interval)

        @app.telemetry("temp", interval=0.01, init=make_filter)
        async def temp(f: _FakeFilter) -> dict[str, object]:
            captured_factor.append(f.factor)
            called.set()
            return {"v": f.update(1.0)}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await called.wait()
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(trigger_shutdown())
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

        assert called.is_set()
        assert captured_factor[0] == settings.mqtt.reconnect_interval

    # --- Type collision guard ---

    def test_init_type_collision_raises(self) -> None:
        """init= returning a framework-provided type raises TypeError.

        Technique: Error Guessing — returning asyncio.Event (a known
        injectable type) from init= would shadow the framework-provided
        shutdown event. The framework guards against this at runtime.

        Tested directly against _call_init because the task runner
        swallows task exceptions via gather(return_exceptions=True).
        """
        from cosalette._injection import build_injection_plan

        def bad_init() -> asyncio.Event:
            return asyncio.Event()

        plan = build_injection_plan(bad_init)
        providers: dict[type, object] = {}

        with pytest.raises(TypeError, match="shadows a framework-provided type"):
            _call_init(bad_init, plan, providers)

    # --- Command init called once ---

    async def test_command_init_called_once(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """init= for commands is called exactly once, not per-message.

        Technique: Integration Testing — init_call_count tracks how
        many times the factory is invoked. Multiple messages should
        still show count=1.
        """
        app = App(name="testapp", version="1.0.0")
        init_call_count = 0
        all_received = asyncio.Event()
        msg_count = 0

        def make_filter() -> _FakeFilter:
            nonlocal init_call_count
            init_call_count += 1
            return _FakeFilter()

        @app.command("valve", init=make_filter)
        async def valve(payload: str, f: _FakeFilter) -> dict[str, object]:
            nonlocal msg_count
            msg_count += 1
            if msg_count >= 2:
                all_received.set()
            return {"v": f.update(1.0)}

        shutdown = asyncio.Event()

        async def simulate_commands() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/valve/set", "1")
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/valve/set", "2")
            await all_received.wait()
            await asyncio.sleep(0.05)
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

        assert msg_count >= 2
        assert init_call_count == 1, (
            f"init= should be called exactly once, was called {init_call_count} times"
        )

    # --- Async callable object validation ---

    def test_init_fail_fast_async_callable_object(self, app: App) -> None:
        """init= with a callable whose __call__ is async raises TypeError.

        Technique: Error Guessing — asyncio.iscoroutinefunction does
        not detect async __call__ on class instances.  The framework
        must check the dunder method explicitly.
        """

        class AsyncCallable:
            async def __call__(self) -> _FakeFilter:
                return _FakeFilter()

        with pytest.raises(TypeError, match="synchronous callable"):

            @app.telemetry("temp", interval=10, init=AsyncCallable())
            async def temp(f: _FakeFilter) -> dict[str, object]:
                return {"v": 1}

    # --- Runtime init failure: telemetry ---

    async def test_telemetry_init_runtime_error_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """If telemetry init= raises at runtime, error is published.

        Technique: Error Guessing — the init callback can pass
        decoration-time validation but fail at runtime (e.g. sensor
        not found). The framework should publish an error payload
        rather than letting the task die silently.
        """
        app = App(name="testapp", version="1.0.0")

        def bad_init() -> _FakeFilter:
            raise RuntimeError("sensor not found")

        @app.telemetry("temp", interval=0.01, init=bad_init)
        async def temp(f: _FakeFilter) -> dict[str, object]:
            return {"v": 1}

        # Register a second telemetry device that sets the shutdown flag
        # so the app exits cleanly.
        done = asyncio.Event()

        @app.telemetry("health", interval=0.01)
        async def health() -> dict[str, object]:
            done.set()
            return {"ok": True}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await done.wait()
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

        # The error should have been published to the error topic
        error_messages = mock_mqtt.get_messages_for("testapp/temp/error")
        assert len(error_messages) >= 1, (
            "Expected at least one error message for failed init"
        )
        error_payload = json.loads(error_messages[0][0])
        assert "sensor not found" in error_payload.get("message", "")

        # The handler itself should never have run
        state_messages = mock_mqtt.get_messages_for("testapp/temp/state")
        assert len(state_messages) == 0

    # --- Runtime init failure: device ---

    async def test_device_init_runtime_error_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """If device init= raises at runtime, error is published.

        Technique: Error Guessing — verifying that _run_device
        error isolation covers init failures, not just handler crashes.
        """
        app = App(name="testapp", version="1.0.0")

        def bad_init() -> _FakeFilter:
            raise RuntimeError("hardware init failed")

        @app.device("motor", init=bad_init)
        async def motor(ctx: DeviceContext, f: _FakeFilter) -> None:
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        # Register a second device to trigger shutdown
        done = asyncio.Event()

        @app.telemetry("health", interval=0.01)
        async def health() -> dict[str, object]:
            done.set()
            return {"ok": True}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await done.wait()
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

        error_messages = mock_mqtt.get_messages_for("testapp/motor/error")
        assert len(error_messages) >= 1, (
            "Expected at least one error message for failed device init"
        )

    # --- Runtime init failure: command ---

    async def test_command_init_runtime_error_publishes_error(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """If command init= raises at runtime, error is published.

        Technique: Error Guessing — command init runs during
        _wire_router. A failure should not prevent other commands
        from working.
        """
        app = App(name="testapp", version="1.0.0")

        def bad_init() -> _FakeFilter:
            raise RuntimeError("valve init failed")

        @app.command("bad_valve", init=bad_init)
        async def bad_valve(payload: str, f: _FakeFilter) -> dict[str, object]:
            return {"v": payload}

        # A healthy command device that should still work
        cmd_received = asyncio.Event()

        @app.command("good_relay")
        async def good_relay(payload: str) -> dict[str, object]:
            cmd_received.set()
            return {"state": payload}

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.1)
            await mock_mqtt.deliver("testapp/good_relay/set", "on")
            await cmd_received.wait()
            await asyncio.sleep(0.05)
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

        # The bad command's error should have been published
        error_messages = mock_mqtt.get_messages_for("testapp/bad_valve/error")
        assert len(error_messages) >= 1, (
            "Expected error published for failed command init"
        )

        # The good command should still have worked
        assert cmd_received.is_set(), (
            "Healthy command should still work after sibling init failure"
        )
