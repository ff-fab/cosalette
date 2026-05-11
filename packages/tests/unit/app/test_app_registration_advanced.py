"""Tests for App registration — conditional, adapter, group, and scoped names.

Covers: enabled= parameter, declarative adapters= constructor, telemetry
group= parameter, and per-type name-uniqueness scoping rules.

Test Techniques Used:
    - Specification-based Testing: enabled= spec variants, adapters= constructor,
      and telemetry group= parameter contracts.
    - Equivalence Partitioning: Name-uniqueness scoping — same name is valid
      across different registration types but invalid within the same type.
    - Contract Testing: HealthReporter protocol conformance for adapter
      health-check integration.
    - Integration Testing: Declarative adapters= wired through App bootstrap.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Protocol

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._health import HealthReporter
from cosalette.testing import FakeClock, MockMqttClient, make_settings
from tests.unit.conftest import (
    _DummyDryRun,
    _DummyImpl,
    _DummyPort,
    _LifecycleAdapter,
    _LifecyclePort,
)

pytestmark = pytest.mark.unit


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
        assert inspect.iscoroutinefunction(temp)

    # --- 16. root command enabled=False ------------------------------------

    def test_root_command_enabled_false(self, app: App) -> None:
        """Root command with enabled=False is skipped, function returned."""

        @app.command(enabled=False)
        async def relay(payload: str) -> dict[str, object]:
            return {"state": payload}

        assert len(app._commands) == 0  # noqa: SLF001
        assert inspect.iscoroutinefunction(relay)

    # --- 17. telemetry persist with enabled=False no error -----------------

    def test_telemetry_persist_disabled_no_store_no_error(self, app: App) -> None:
        """persist= with enabled=False and no store should not raise."""
        from cosalette._persistence._persist import SaveOnPublish

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
                adapters={_DummyPort: (_DummyImpl, _DummyDryRun, _DummyImpl)},  # ty: ignore[invalid-argument-type]
            )

    # --- 12. Lifecycle: adapter from adapters= runs at runtime -------------

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


# ---------------------------------------------------------------------------
# TestScopedNameUniqueness
# ---------------------------------------------------------------------------


class TestScopedNameUniqueness:
    """Per-type name scoping: telemetry + command may share a name.

    Technique: Specification-based Testing — verifying the scoped
    uniqueness rules introduced to allow telemetry and command handlers
    to coexist under the same device name while still rejecting all
    other cross-type or same-type duplicates.
    """

    async def test_telemetry_and_command_share_name(self, app: App) -> None:
        """Telemetry registered first, then command with same name succeeds."""

        @app.telemetry("sensor", interval=10)
        async def sensor_telem(ctx: DeviceContext) -> dict:
            return {"v": 1}

        @app.command("sensor")
        async def sensor_cmd(topic: str, payload: str) -> None: ...

        assert len(app._telemetry) == 1
        assert len(app._commands) == 1
        assert app._telemetry[0].name == "sensor"
        assert app._commands[0].name == "sensor"

    async def test_command_then_telemetry_share_name(self, app: App) -> None:
        """Command registered first, then telemetry with same name succeeds."""

        @app.command("valve")
        async def valve_cmd(topic: str, payload: str) -> None: ...

        @app.telemetry("valve", interval=5)
        async def valve_telem(ctx: DeviceContext) -> dict:
            return {"open": True}

        assert len(app._commands) == 1
        assert len(app._telemetry) == 1

    async def test_device_rejects_collision_with_telemetry(self, app: App) -> None:
        """Device after telemetry with same name is still rejected."""

        @app.telemetry("sensor", interval=10)
        async def sensor_telem(ctx: DeviceContext) -> dict:
            return {}

        with pytest.raises(ValueError, match="already registered"):

            @app.device("sensor")
            async def sensor_dev(ctx: DeviceContext) -> None: ...

    async def test_device_rejects_collision_with_command(self, app: App) -> None:
        """Device after command with same name is still rejected."""

        @app.command("valve")
        async def valve_cmd(topic: str, payload: str) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.device("valve")
            async def valve_dev(ctx: DeviceContext) -> None: ...

    async def test_telemetry_after_device_rejected(self, app: App) -> None:
        """Telemetry after device with same name is still rejected."""

        @app.device("sensor")
        async def sensor_dev(ctx: DeviceContext) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.telemetry("sensor", interval=10)
            async def sensor_telem(ctx: DeviceContext) -> dict:
                return {}

    async def test_command_after_device_rejected(self, app: App) -> None:
        """Command after device with same name is still rejected."""

        @app.device("valve")
        async def valve_dev(ctx: DeviceContext) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.command("valve")
            async def valve_cmd(topic: str, payload: str) -> None: ...

    async def test_two_telemetry_same_name_rejected(self, app: App) -> None:
        """Two telemetry with same name is still rejected."""

        @app.telemetry("sensor", interval=10)
        async def telem1(ctx: DeviceContext) -> dict:
            return {"v": 1}

        with pytest.raises(ValueError, match="already registered"):

            @app.telemetry("sensor", interval=5)
            async def telem2(ctx: DeviceContext) -> dict:
                return {"v": 2}

    async def test_two_commands_same_name_rejected(self, app: App) -> None:
        """Two commands with same name is still rejected."""

        @app.command("valve")
        async def cmd1(topic: str, payload: str) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.command("valve")
            async def cmd2(topic: str, payload: str) -> None: ...

    async def test_shared_name_with_decorator_api(self, app: App) -> None:
        """Decorator-based telemetry + command pair with shared name works."""

        @app.telemetry("pump", interval=60)
        async def pump_telem() -> dict[str, object]:
            return {"rpm": 1200}

        @app.command("pump")
        async def pump_cmd(topic: str, payload: str) -> None: ...

        assert app._telemetry[0].name == "pump"
        assert app._commands[0].name == "pump"

    async def test_add_telemetry_and_add_command_share_name(self, app: App) -> None:
        """Imperative API: add_telemetry + add_command with same name works."""

        async def telem() -> dict[str, object]:
            return {"v": 1}

        async def cmd(topic: str, payload: str) -> None: ...

        app.add_telemetry("sensor", telem, interval=10)
        app.add_command("sensor", cmd)

        assert len(app._telemetry) == 1
        assert len(app._commands) == 1

    async def test_disabled_telemetry_allows_same_name_command(self, app: App) -> None:
        """Disabled telemetry doesn't reserve name; command with same name works."""

        @app.telemetry("sensor", interval=10, enabled=False)
        async def sensor_telem(ctx: DeviceContext) -> dict:
            return {}

        @app.command("sensor")
        async def sensor_cmd(topic: str, payload: str) -> None: ...

        assert len(app._telemetry) == 0
        assert len(app._commands) == 1

    async def test_shared_name_publishes_availability_once(self, app: App) -> None:
        """Shared telemetry+command name publishes availability once.

        Technique: Specification-based Testing — verifying that
        ``_publish_device_availability`` deduplicates when a telemetry
        and command share the same name, publishing exactly one
        availability message.
        """

        async def telem(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        async def cmd(topic: str, payload: str) -> dict[str, object]:
            return {"ok": True}

        app.add_telemetry("hw", telem, interval=10)
        app.add_command("hw", cmd)

        mqtt = MockMqttClient()
        clock = FakeClock()
        health = HealthReporter(
            mqtt=mqtt, topic_prefix="test", version="0.1.0", clock=clock
        )

        await app._publish_device_availability(health)

        # Should publish to test/hw/availability exactly once
        msgs = mqtt.get_messages_for("test/hw/availability")
        assert len(msgs) == 1
        assert msgs[0][0] == "online"

    async def test_shared_name_produces_one_context(self, app: App) -> None:
        """Shared telemetry+command name yields a single DeviceContext.

        Technique: Specification-based Testing — verifying that
        ``_build_contexts`` deduplicates when a telemetry and command
        share the same name, producing exactly one context entry.
        """

        async def telem(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        async def cmd(topic: str, payload: str) -> dict[str, object]:
            return {"ok": True}

        app.add_telemetry("hw", telem, interval=10)
        app.add_command("hw", cmd)

        # Build contexts — should have exactly one entry for "hw"
        contexts = app._build_contexts(
            settings=make_settings(),
            mqtt=MockMqttClient(),
            prefix="test",
            shutdown_event=asyncio.Event(),
            adapters={},
            clock=FakeClock(),
        )
        assert len(contexts) == 1
        assert "hw" in contexts

    async def test_root_telemetry_named_command_same_name_rejected(
        self, app: App
    ) -> None:
        """Root telemetry + named command sharing a name is rejected.

        Technique: Specification-based Testing — a root registration
        publishes to ``{prefix}/state`` while a named one publishes to
        ``{prefix}/{name}/state``.  Sharing a name across these two
        MQTT namespace layouts would silently route to the wrong topic.
        """

        @app.telemetry(interval=10)
        async def sensor(ctx: DeviceContext) -> dict[str, object]:
            return {"v": 1}

        with pytest.raises(ValueError, match="root and named"):

            @app.command("sensor")
            async def sensor_cmd(topic: str, payload: str) -> None: ...

    async def test_named_telemetry_root_command_same_name_rejected(
        self, app: App
    ) -> None:
        """Named telemetry + root command sharing a name is rejected.

        Technique: Specification-based Testing — bidirectional check:
        the mismatch is caught regardless of registration order.
        """

        @app.command()
        async def valve(topic: str, payload: str, ctx: DeviceContext) -> None: ...

        with pytest.raises(ValueError, match="root and named"):

            @app.telemetry("valve", interval=5)
            async def valve_telem(ctx: DeviceContext) -> dict[str, object]:
                return {"v": 1}
