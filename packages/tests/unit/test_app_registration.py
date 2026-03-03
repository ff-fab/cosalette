"""Tests for cosalette App — registration decorators and imperative API.

Covers: @app.device, @app.telemetry, add_device(), add_telemetry(),
add_command(), conditional registration (enabled=), and the declarative
adapters= constructor parameter.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._strategies import OnChange
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.unit.conftest import (
    _DummyDryRun,
    _DummyImpl,
    _DummyPort,
    _FakeFilter,
    _LifecycleAdapter,
    _LifecyclePort,
)

pytestmark = pytest.mark.unit

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
# TestDirectFunctionRegistration — imperative add_*() methods
# ---------------------------------------------------------------------------


class TestDirectFunctionRegistration:
    """Tests for imperative add_device(), add_telemetry(), add_command() methods.

    Technique: Specification-based Testing — verifying that the imperative
    API produces correct registrations, shares validation with decorators,
    and detects collisions across both APIs.
    """

    # --- add_device ---------------------------------------------------------

    def test_add_device_registers_function(self, app: App) -> None:
        """add_device stores a _DeviceRegistration with is_root=False."""

        async def sensor(ctx: DeviceContext) -> None: ...

        app.add_device("sensor", sensor)

        assert len(app._devices) == 1  # noqa: SLF001
        reg = app._devices[0]  # noqa: SLF001
        assert reg.name == "sensor"
        assert reg.func is sensor
        assert reg.is_root is False

    def test_add_device_duplicate_name_raises(self, app: App) -> None:
        """Registering two devices with the same name raises ValueError."""

        async def dev1(ctx: DeviceContext) -> None: ...

        async def dev2(ctx: DeviceContext) -> None: ...

        app.add_device("x", dev1)
        with pytest.raises(ValueError, match="already registered"):
            app.add_device("x", dev2)

    def test_add_device_cross_type_collision(self, app: App) -> None:
        """A device name can't collide with an existing telemetry name."""

        async def dev(ctx: DeviceContext) -> None: ...

        async def telem() -> dict[str, object]:
            return {"v": 1}

        app.add_device("x", dev)
        with pytest.raises(ValueError, match="already registered"):
            app.add_telemetry("x", telem, interval=1)

    def test_add_device_with_init(self, app: App) -> None:
        """init callback is stored on the registration."""

        def make_filter() -> _FakeFilter:
            return _FakeFilter()

        async def dev(ctx: DeviceContext, f: _FakeFilter) -> None: ...

        app.add_device("dev", dev, init=make_filter)

        reg = app._devices[0]  # noqa: SLF001
        assert reg.init is make_filter
        assert reg.init_injection_plan == []

    def test_add_device_async_init_raises(self, app: App) -> None:
        """Async init is rejected with TypeError."""

        async def async_init() -> _FakeFilter:
            return _FakeFilter()

        async def dev(ctx: DeviceContext) -> None: ...

        with pytest.raises(TypeError, match="synchronous callable"):
            app.add_device("dev", dev, init=async_init)

    def test_add_device_unannotated_param_raises(self, app: App) -> None:
        """Function with unannotated param raises TypeError."""

        async def bad(some_arg) -> None:  # noqa: ANN001
            pass

        with pytest.raises(TypeError, match="no type annotation"):
            app.add_device("bad", bad)

    # --- add_telemetry ------------------------------------------------------

    def test_add_telemetry_registers_function(self, app: App) -> None:
        """add_telemetry stores a _TelemetryRegistration with correct fields."""

        async def temp() -> dict[str, object]:
            return {"celsius": 22.5}

        app.add_telemetry("temp", temp, interval=30)

        assert len(app._telemetry) == 1  # noqa: SLF001
        reg = app._telemetry[0]  # noqa: SLF001
        assert reg.name == "temp"
        assert reg.func is temp
        assert reg.interval == 30
        assert reg.is_root is False

    def test_add_telemetry_zero_interval_raises(self, app: App) -> None:
        """interval=0 raises ValueError."""

        async def temp() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="positive"):
            app.add_telemetry("temp", temp, interval=0)

    def test_add_telemetry_negative_interval_raises(self, app: App) -> None:
        """interval=-1 raises ValueError."""

        async def temp() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="positive"):
            app.add_telemetry("temp", temp, interval=-1)

    def test_add_telemetry_persist_without_store_raises(self, app: App) -> None:
        """persist set but no store on App raises ValueError."""
        from cosalette._persist import SaveOnPublish

        async def temp() -> dict[str, object]:
            return {}

        with pytest.raises(ValueError, match="store="):
            app.add_telemetry("temp", temp, interval=10, persist=SaveOnPublish())

    # --- add_command --------------------------------------------------------

    def test_add_command_registers_function(self, app: App) -> None:
        """add_command stores a _CommandRegistration with correct fields."""

        async def switch(payload: str) -> dict[str, object]:
            return {"state": payload}

        app.add_command("switch", switch)

        assert len(app._commands) == 1  # noqa: SLF001
        reg = app._commands[0]  # noqa: SLF001
        assert reg.name == "switch"
        assert reg.func is switch
        assert reg.is_root is False

    def test_add_command_detects_mqtt_params(self, app: App) -> None:
        """Function with topic and payload params detected in mqtt_params."""

        async def handler(topic: str, payload: str) -> dict[str, object]:
            return {"t": topic, "p": payload}

        app.add_command("switch", handler)

        reg = app._commands[0]  # noqa: SLF001
        assert reg.mqtt_params == frozenset({"topic", "payload"})

    # --- Collision between decorator and imperative -------------------------

    def test_decorator_and_add_collision(self, app: App) -> None:
        """@app.device('x') then app.add_device('x', ...) raises."""

        @app.device("x")
        async def x_dev(ctx: DeviceContext) -> None: ...

        async def x_dev2(ctx: DeviceContext) -> None: ...

        with pytest.raises(ValueError, match="already registered"):
            app.add_device("x", x_dev2)

    def test_add_and_decorator_collision(self, app: App) -> None:
        """app.add_device('x', ...) then @app.device('x') raises."""

        async def x_dev(ctx: DeviceContext) -> None: ...

        app.add_device("x", x_dev)

        with pytest.raises(ValueError, match="already registered"):

            @app.device("x")
            async def x_dev2(ctx: DeviceContext) -> None: ...

    # --- Decorator equivalence ----------------------------------------------

    def test_decorator_equivalence_device(self, app: App) -> None:
        """Decorator with name produces same registration fields as add_device."""
        app2 = App(name="testapp", version="1.0.0")

        async def sensor(ctx: DeviceContext) -> None: ...

        # Decorator path
        app.device("sensor")(sensor)
        # Imperative path
        app2.add_device("sensor", sensor)

        d_reg = app._devices[0]  # noqa: SLF001
        a_reg = app2._devices[0]  # noqa: SLF001
        assert d_reg.name == a_reg.name
        assert d_reg.func is a_reg.func
        assert d_reg.is_root == a_reg.is_root == False  # noqa: E712
        assert d_reg.injection_plan == a_reg.injection_plan
        assert d_reg.init == a_reg.init
        assert d_reg.init_injection_plan == a_reg.init_injection_plan

    def test_decorator_equivalence_telemetry(self, app: App) -> None:
        """Decorator with name produces same registration fields as add_telemetry."""
        app2 = App(name="testapp", version="1.0.0")
        strategy = OnChange()

        async def temp() -> dict[str, object]:
            return {"v": 1}

        app.telemetry("temp", interval=10, publish=strategy)(temp)
        app2.add_telemetry("temp", temp, interval=10, publish=strategy)

        d_reg = app._telemetry[0]  # noqa: SLF001
        a_reg = app2._telemetry[0]  # noqa: SLF001
        assert d_reg.name == a_reg.name
        assert d_reg.func is a_reg.func
        assert d_reg.is_root == a_reg.is_root == False  # noqa: E712
        assert d_reg.interval == a_reg.interval
        assert d_reg.publish_strategy is a_reg.publish_strategy
        assert d_reg.injection_plan == a_reg.injection_plan

    def test_decorator_equivalence_command(self, app: App) -> None:
        """Decorator with name produces same registration fields as add_command."""
        app2 = App(name="testapp", version="1.0.0")

        async def valve(payload: str) -> dict[str, object]:
            return {"v": payload}

        app.command("valve")(valve)
        app2.add_command("valve", valve)

        d_reg = app._commands[0]  # noqa: SLF001
        a_reg = app2._commands[0]  # noqa: SLF001
        assert d_reg.name == a_reg.name
        assert d_reg.func is a_reg.func
        assert d_reg.is_root == a_reg.is_root == False  # noqa: E712
        assert d_reg.mqtt_params == a_reg.mqtt_params
        assert d_reg.injection_plan == a_reg.injection_plan

    # --- Mixed registration -------------------------------------------------

    def test_mixed_decorator_and_imperative(self, app: App) -> None:
        """Mix of decorators and imperative registrations all register."""

        @app.device("d1")
        async def d1(ctx: DeviceContext) -> None: ...

        async def d2(ctx: DeviceContext) -> None: ...

        app.add_device("d2", d2)

        @app.telemetry("t1", interval=10)
        async def t1() -> dict[str, object]:
            return {}

        async def t2() -> dict[str, object]:
            return {}

        app.add_telemetry("t2", t2, interval=20)

        @app.command("c1")
        async def c1(payload: str) -> dict[str, object]:
            return {}

        async def c2(payload: str) -> dict[str, object]:
            return {}

        app.add_command("c2", c2)

        assert len(app._devices) == 2  # noqa: SLF001
        assert len(app._telemetry) == 2  # noqa: SLF001
        assert len(app._commands) == 2  # noqa: SLF001
        all_names = {
            r.name
            for r in [*app._devices, *app._telemetry, *app._commands]  # noqa: SLF001
        }
        assert all_names == {"d1", "d2", "t1", "t2", "c1", "c2"}

    # --- Runtime integration ------------------------------------------------

    async def test_add_device_runs_at_runtime(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Imperatively registered device actually executes in _run_async.

        Technique: Integration Testing — register a device via add_device,
        verify it runs during the async lifecycle.
        """
        app = App(name="testapp", version="1.0.0")
        device_called = asyncio.Event()

        async def sensor(ctx: DeviceContext) -> None:
            device_called.set()

        app.add_device("sensor", sensor)

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

    async def test_add_telemetry_runs_at_runtime(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Imperatively registered telemetry polls and publishes.

        Technique: Integration Testing — register telemetry via
        add_telemetry, verify it runs and publishes state.
        """
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        async def temp() -> dict[str, object]:
            called.set()
            return {"celsius": 22.5}

        app.add_telemetry("temp", temp, interval=0.01)

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
        assert "22.5" in state_messages[0][0]

    async def test_add_command_routes_at_runtime(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Imperatively registered command receives dispatched messages.

        Technique: Integration Testing — register a command via
        add_command, deliver an MQTT message, verify the handler
        is invoked and state is published.
        """
        app = App(name="testapp", version="1.0.0")
        command_received = asyncio.Event()

        async def relay(payload: str) -> dict[str, object]:
            command_received.set()
            return {"state": payload}

        app.add_command("relay", relay)

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/relay/set", "ON")
            await command_received.wait()
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

        assert command_received.is_set()
        state_messages = mock_mqtt.get_messages_for("testapp/relay/state")
        assert len(state_messages) >= 1
        assert "ON" in state_messages[0][0]


# ---------------------------------------------------------------------------
# TestConditionalRegistration
# ---------------------------------------------------------------------------


class TestConditionalRegistration:
    """Tests for the ``enabled=`` parameter on device registration methods.

    Technique: Specification-based Testing — verifying that ``enabled=False``
    silently skips registration without erroring, while ``enabled=True``
    (the default) preserves backward-compatible behaviour.
    """

    # --- 1. enabled=True registers device ----------------------------------

    def test_enabled_true_registers_device(self, app: App) -> None:
        """Explicit enabled=True registers the device normally."""

        async def sensor(ctx: DeviceContext) -> None: ...

        app.add_device("x", sensor, enabled=True)

        assert len(app._devices) == 1  # noqa: SLF001
        assert app._devices[0].name == "x"  # noqa: SLF001

    # --- 2. enabled=False skips device -------------------------------------

    def test_enabled_false_skips_device(self, app: App) -> None:
        """add_device with enabled=False produces an empty registry."""

        async def sensor(ctx: DeviceContext) -> None: ...

        app.add_device("x", sensor, enabled=False)

        assert len(app._devices) == 0  # noqa: SLF001

    # --- 3. enabled=False skips telemetry ----------------------------------

    def test_enabled_false_skips_telemetry(self, app: App) -> None:
        """add_telemetry with enabled=False produces an empty registry."""

        async def temp() -> dict[str, object]:
            return {"celsius": 22.5}

        app.add_telemetry("x", temp, interval=10, enabled=False)

        assert len(app._telemetry) == 0  # noqa: SLF001

    # --- 4. enabled=False skips command ------------------------------------

    def test_enabled_false_skips_command(self, app: App) -> None:
        """add_command with enabled=False produces an empty registry."""

        async def relay(payload: str) -> dict[str, object]:
            return {"state": payload}

        app.add_command("x", relay, enabled=False)

        assert len(app._commands) == 0  # noqa: SLF001

    # --- 5. decorator enabled=False returns original function (device) -----

    def test_decorator_enabled_false_returns_original_function(self, app: App) -> None:
        """@app.device with enabled=False returns the function unmodified."""

        @app.device("x", enabled=False)
        async def sensor(ctx: DeviceContext) -> None: ...

        assert sensor.__name__ == "sensor"
        assert len(app._devices) == 0  # noqa: SLF001

    # --- 6. decorator enabled=False telemetry ------------------------------

    def test_decorator_enabled_false_telemetry(self, app: App) -> None:
        """@app.telemetry with enabled=False returns function, empty registry."""

        @app.telemetry("x", interval=10, enabled=False)
        async def temp() -> dict[str, object]:
            return {"celsius": 22.5}

        assert temp.__name__ == "temp"
        assert len(app._telemetry) == 0  # noqa: SLF001

    # --- 7. decorator enabled=False command --------------------------------

    def test_decorator_enabled_false_command(self, app: App) -> None:
        """@app.command with enabled=False returns function, empty registry."""

        @app.command("x", enabled=False)
        async def relay(payload: str) -> dict[str, object]:
            return {"state": payload}

        assert relay.__name__ == "relay"
        assert len(app._commands) == 0  # noqa: SLF001

    # --- 8. disabled device does not reserve name --------------------------

    def test_disabled_device_does_not_reserve_name(self, app: App) -> None:
        """A disabled device doesn't block a later registration of the same name."""

        async def f1(ctx: DeviceContext) -> None: ...

        async def f2(ctx: DeviceContext) -> None: ...

        app.add_device("x", f1, enabled=False)
        app.add_device("x", f2)  # should succeed — name not reserved

        assert len(app._devices) == 1  # noqa: SLF001
        assert app._devices[0].func is f2  # noqa: SLF001

    # --- 9. default enabled=True (backward compat) -------------------------

    def test_default_enabled_true(self, app: App) -> None:
        """Omitting enabled= registers the device (backward compat)."""

        async def sensor(ctx: DeviceContext) -> None: ...

        app.add_device("x", sensor)

        assert len(app._devices) == 1  # noqa: SLF001

    # --- 10. root device enabled=False -------------------------------------

    def test_root_device_enabled_false(self, app: App) -> None:
        """@app.device(enabled=False) on a root device skips registration."""

        @app.device(enabled=False)
        async def sensor(ctx: DeviceContext) -> None: ...

        assert sensor.__name__ == "sensor"
        assert len(app._devices) == 0  # noqa: SLF001

    # --- 11. mixed enabled and disabled ------------------------------------

    def test_mixed_enabled_disabled(self, app: App) -> None:
        """Only enabled devices appear in the registry."""

        async def dev_a(ctx: DeviceContext) -> None: ...

        async def dev_b(ctx: DeviceContext) -> None: ...

        async def tel_a() -> dict[str, object]:
            return {"v": 1}

        async def cmd_a(payload: str) -> dict[str, object]:
            return {"s": payload}

        app.add_device("a", dev_a, enabled=True)
        app.add_device("b", dev_b, enabled=False)
        app.add_telemetry("t1", tel_a, interval=10, enabled=False)
        app.add_command("c1", cmd_a, enabled=True)

        assert len(app._devices) == 1  # noqa: SLF001
        assert len(app._telemetry) == 0  # noqa: SLF001
        assert len(app._commands) == 1  # noqa: SLF001

    # --- 12. disabled device not in _all_registrations ---------------------

    def test_disabled_device_not_in_all_registrations(self, app: App) -> None:
        """Disabled devices are absent from _all_registrations."""

        async def dev(ctx: DeviceContext) -> None: ...

        async def tel() -> dict[str, object]:
            return {"v": 1}

        app.add_device("d", dev, enabled=False)
        app.add_telemetry("t", tel, interval=10, enabled=False)

        assert len(app._all_registrations) == 0  # noqa: SLF001

    # --- 13. enabled=False skips validation --------------------------------

    def test_enabled_false_no_validation(self, app: App) -> None:
        """Disabled add_device skips signature validation (unannotated param ok)."""

        async def bad_func(x) -> None:  # noqa: ANN001
            ...

        # With enabled=True this would raise TypeError (missing annotation).
        # With enabled=False the early return skips all validation.
        app.add_device("x", bad_func, enabled=False)

        assert len(app._devices) == 0  # noqa: SLF001

    # --- 14. disabled device not started at runtime ------------------------

    async def test_disabled_device_not_started_at_runtime(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """A device registered with enabled=False never executes at runtime."""
        app = App(name="testapp", version="1.0.0")
        called = asyncio.Event()

        async def should_not_run(ctx: DeviceContext) -> None:
            called.set()

        app.add_device("ghost", should_not_run, enabled=False)

        # Register a real telemetry device so the app has work to do
        @app.telemetry("alive", interval=1)
        async def alive() -> dict[str, object]:
            return {"ok": True}

        shutdown = asyncio.Event()

        async def trigger_shutdown() -> None:
            await asyncio.sleep(0.3)
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

        assert not called.is_set(), "Disabled device should never execute"

    # --- 15. root telemetry enabled=False -----------------------------------

    def test_root_telemetry_enabled_false(self, app: App) -> None:
        """Root telemetry with enabled=False is skipped, function returned."""

        @app.telemetry(interval=10, enabled=False)
        async def temp() -> dict[str, object]:
            return {"v": 1}

        assert len(app._telemetry) == 0  # noqa: SLF001
        # Decorator returns the original function
        assert asyncio.iscoroutinefunction(temp)

    # --- 16. root command enabled=False ------------------------------------

    def test_root_command_enabled_false(self, app: App) -> None:
        """Root command with enabled=False is skipped, function returned."""

        @app.command(enabled=False)
        async def relay(payload: str) -> dict[str, object]:
            return {"state": payload}

        assert len(app._commands) == 0  # noqa: SLF001
        assert asyncio.iscoroutinefunction(relay)

    # --- 17. telemetry persist with enabled=False no error -----------------

    def test_telemetry_persist_disabled_no_store_no_error(self, app: App) -> None:
        """persist= with enabled=False and no store should not raise."""
        from cosalette._persist import SaveOnPublish

        @app.telemetry("temp", interval=10, persist=SaveOnPublish(), enabled=False)
        async def temp() -> dict[str, object]:
            return {}

        assert len(app._telemetry) == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# TestDeclarativeAdapterBlock
# ---------------------------------------------------------------------------


class TestDeclarativeAdapterBlock:
    """Tests for the ``adapters=`` constructor parameter.

    Technique: Specification-based Testing — verifying that the adapters dict
    produces the same registrations as imperative ``app.adapter()`` calls.
    """

    # --- 1. Tuple form registers impl and dry-run --------------------------

    def test_tuple_form_registers_impl_and_dry_run(self) -> None:
        """adapters={Port: (Impl, DryRun)} registers both variants."""
        app = App(name="testapp", adapters={_DummyPort: (_DummyImpl, _DummyDryRun)})
        assert _DummyPort in app._adapters  # noqa: SLF001
        entry = app._adapters[_DummyPort]  # noqa: SLF001
        assert entry.impl is _DummyImpl
        assert entry.dry_run is _DummyDryRun

    # --- 2. Bare form registers impl only ----------------------------------

    def test_bare_form_registers_impl_only(self) -> None:
        """adapters={Port: Impl} registers with dry_run=None."""
        app = App(name="testapp", adapters={_DummyPort: _DummyImpl})
        assert _DummyPort in app._adapters  # noqa: SLF001
        entry = app._adapters[_DummyPort]  # noqa: SLF001
        assert entry.impl is _DummyImpl
        assert entry.dry_run is None

    # --- 3. Empty dict is valid no-op --------------------------------------

    def test_empty_dict(self) -> None:
        """adapters={} is a valid no-op."""
        app = App(name="testapp", adapters={})
        assert len(app._adapters) == 0  # noqa: SLF001

    # --- 4. None default produces empty registry ---------------------------

    def test_none_default(self) -> None:
        """Omitting adapters= produces an empty adapter registry."""
        app = App(name="testapp")
        assert len(app._adapters) == 0  # noqa: SLF001

    # --- 5. Multiple adapters in one dict ----------------------------------

    def test_multiple_adapters(self) -> None:
        """Multiple port types in one dict all register."""

        class _SecondPort(Protocol):
            def other(self) -> str: ...

        class _SecondImpl:
            def other(self) -> str:
                return "second"

        app = App(
            name="testapp",
            adapters={
                _DummyPort: _DummyImpl,
                _SecondPort: (_SecondImpl, _SecondImpl),
            },
        )
        assert len(app._adapters) == 2  # noqa: SLF001
        assert _DummyPort in app._adapters  # noqa: SLF001
        assert _SecondPort in app._adapters  # noqa: SLF001

    # --- 6. Duplicate port with imperative raises ValueError ---------------

    def test_duplicate_port_with_imperative_raises(self) -> None:
        """Registering same port via adapters= and then adapter() raises."""
        app = App(name="testapp", adapters={_DummyPort: _DummyImpl})
        with pytest.raises(ValueError, match="already registered"):
            app.adapter(_DummyPort, _DummyImpl)

    # --- 7. Coexistence with imperative for different ports ----------------

    def test_coexistence_with_imperative(self) -> None:
        """adapters= and app.adapter() can register different ports."""

        class _OtherPort(Protocol):
            def other(self) -> str: ...

        class _OtherImpl:
            def other(self) -> str:
                return "other"

        app = App(name="testapp", adapters={_DummyPort: _DummyImpl})
        app.adapter(_OtherPort, _OtherImpl)

        assert len(app._adapters) == 2  # noqa: SLF001

    # --- 8. Fail-fast validation applies to dict entries -------------------

    def test_fail_fast_validation(self) -> None:
        """Invalid factory in adapters= dict triggers fail-fast TypeError."""

        def bad_factory(unknown_param) -> object:  # noqa: ANN001
            return object()

        with pytest.raises(TypeError):
            App(name="testapp", adapters={_DummyPort: bad_factory})

    # --- 9. Lazy string import in tuple form -------------------------------

    def test_lazy_string_in_tuple(self) -> None:
        """String import paths are accepted in tuple form."""
        app = App(
            name="testapp",
            adapters={_DummyPort: ("cosalette._app:App", "cosalette._app:App")},
        )
        assert _DummyPort in app._adapters  # noqa: SLF001

    # --- 10. Equivalence: dict == imperative registration ------------------

    def test_equivalence_with_imperative(self) -> None:
        """adapters= dict produces identical _AdapterEntry as app.adapter()."""
        app_dict = App(name="a", adapters={_DummyPort: (_DummyImpl, _DummyDryRun)})
        app_imp = App(name="b")
        app_imp.adapter(_DummyPort, _DummyImpl, dry_run=_DummyDryRun)

        entry_d = app_dict._adapters[_DummyPort]  # noqa: SLF001
        entry_i = app_imp._adapters[_DummyPort]  # noqa: SLF001
        assert entry_d.impl is entry_i.impl
        assert entry_d.dry_run is entry_i.dry_run

    # --- 11. Invalid tuple length raises ValueError ------------------------

    def test_invalid_tuple_length_raises(self) -> None:
        """A 3-tuple adapter value raises ValueError with clear message."""
        with pytest.raises(ValueError, match="2-tuple"):
            App(
                name="testapp",
                adapters={_DummyPort: (_DummyImpl, _DummyDryRun, _DummyImpl)},
            )

    # --- 12. Lifecycle: adapter from adapters= runs at runtime -------------

    @pytest.mark.anyio
    async def test_lifecycle_adapter_from_dict(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Adapter registered via adapters= is entered and exited at runtime."""
        log: list[str] = []
        adapter = _LifecycleAdapter(name="dict-adapter", log=log)

        app = App(
            name="testapp",
            version="1.0.0",
            adapters={_LifecyclePort: lambda: adapter},
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

        assert adapter.entered
        assert adapter.exited
        assert log == ["dict-adapter:enter", "dict-adapter:exit"]


# ---------------------------------------------------------------------------
# TestTelemetryGroupParameter
# ---------------------------------------------------------------------------


class TestTelemetryGroupParameter:
    """Coalescing group parameter on telemetry registration.

    Technique: Specification-based Testing — verify that the group
    parameter is stored, defaulted, and validated correctly across
    all registration paths.
    """

    def test_group_defaults_to_none(self, app: App) -> None:
        """group= defaults to None when not specified."""

        @app.telemetry(interval=10)
        async def poll() -> dict[str, object]:
            return {"v": 1}

        assert app._telemetry[0].group is None

    def test_group_stored_on_decorator(self, app: App) -> None:
        """group= value is threaded to registration."""

        @app.telemetry(name="temp", interval=10, group="optolink")
        async def poll() -> dict[str, object]:
            return {"v": 1}

        assert app._telemetry[0].group == "optolink"

    def test_group_stored_on_add_telemetry(self, app: App) -> None:
        """group= via imperative add_telemetry."""

        async def poll() -> dict[str, object]:
            return {"v": 1}

        app.add_telemetry("temp", poll, interval=10, group="spi_bus")
        assert app._telemetry[0].group == "spi_bus"

    def test_group_stored_on_root_telemetry(self, app: App) -> None:
        """group= on root (unnamed) telemetry decorator."""

        @app.telemetry(interval=10, group="optolink")
        async def poll() -> dict[str, object]:
            return {"v": 1}

        assert app._telemetry[0].group == "optolink"

    def test_empty_group_raises_on_decorator(self, app: App) -> None:
        """Empty string group= raises ValueError on decorator."""
        with pytest.raises(ValueError, match="group must be non-empty"):

            @app.telemetry(name="temp", interval=10, group="")
            async def poll() -> dict[str, object]:
                return {"v": 1}

    def test_empty_group_raises_on_add_telemetry(self, app: App) -> None:
        """Empty string group= raises ValueError on add_telemetry."""

        async def poll() -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(ValueError, match="group must be non-empty"):
            app.add_telemetry("temp", poll, interval=10, group="")

    def test_empty_group_raises_on_root_decorator(self, app: App) -> None:
        """Empty string group= raises ValueError on root decorator."""
        with pytest.raises(ValueError, match="group must be non-empty"):

            @app.telemetry(interval=10, group="")
            async def poll() -> dict[str, object]:
                return {"v": 1}

    def test_none_group_no_validation_error(self, app: App) -> None:
        """group=None does not trigger validation."""

        @app.telemetry(name="temp", interval=10, group=None)
        async def poll() -> dict[str, object]:
            return {"v": 1}

        assert app._telemetry[0].group is None

    def test_disabled_decorator_skips_empty_group_validation(self, app: App) -> None:
        """enabled=False silently skips — no ValueError for group=''."""

        @app.telemetry(name="temp", interval=10, enabled=False, group="")
        async def poll() -> dict[str, object]:
            return {"v": 1}

        assert len(app._telemetry) == 0
