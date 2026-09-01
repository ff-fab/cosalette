"""Tests for cosalette App — injection, settings resolution, store factories.

Covers: signature-based handler injection, resolve_settings priority,
store factory DI, and resolve_enabled branch coverage.

Test Techniques Used:
    - Specification-based Testing: Handler signature injection contracts and
      settings resolution precedence rules (ADR-003).
    - Equivalence Partitioning: Settings source priority (env → file → default)
      and enabled= spec variants (bool, callable, SettingRef).
    - Integration Testing: Store factory DI wired through App bootstrap.
    - Branch/Condition Coverage: resolve_enabled True/False/callable branches.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._persistence._stores import NullStore, Store
from cosalette._settings import Settings
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
        async def sensor() -> AsyncIterator[None]:
            called.set()
            yield

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
        async def valve(settings: Settings) -> AsyncIterator[None]:
            received_settings.append(settings)
            yield

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
        async def valve(logger: logging.Logger) -> AsyncIterator[None]:
            received_logger.append(logger)
            yield

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
        async def valve(
            ctx: DeviceContext, logger: logging.Logger
        ) -> AsyncIterator[None]:
            results.append((ctx, logger))
            yield

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
        async def sensor(port: _InjectionTestPort) -> AsyncIterator[None]:
            received_values.append(port.value())
            yield

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
            async def temp(ctx) -> dict[str, object]:  # type: ignore[no-untyped-def]
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
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            assert isinstance(ctx, DeviceContext)
            assert ctx.name == "sensor"
            device_called.set()
            yield

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
        async def d(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        async def d(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        async def d(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        async def d(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        async def d(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        from cosalette._persistence._persist import SaveOnPublish
        from cosalette._persistence._stores import MemoryStore

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

        async def fn(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        dev: list[object] = []
        cmd: list[object] = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 1
        assert tel[0].enabled_spec is True  # ty: ignore[unresolved-attribute]

    def test_callable_falsy_removes_telemetry(self) -> None:
        """A callable returning False drops the registration."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel = [self._make_telemetry("x", lambda s: False)]
        dev: list[object] = []
        cmd: list[object] = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 0

    def test_literal_true_passed_through(self) -> None:
        """Literal True registrations are not modified."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel = [self._make_telemetry("x", True)]
        dev: list[object] = []
        cmd: list[object] = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 1
        assert tel[0].enabled_spec is True  # ty: ignore[unresolved-attribute]

    def test_resolve_enabled_propagates_to_devices_and_commands(self) -> None:
        """resolve_enabled processes devices and commands, not just telemetry."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel: list[object] = []
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

        from cosalette._persistence._persist import SaveOnPublish
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

    def test_callable_enabled_triggerable_group_raises_during_bootstrap(self) -> None:
        """Deferred enabled telemetry still rejects triggerable= with group=.

        Technique: Regression — this pair is validated after enabled= resolves
        truthy, not at decoration time.
        """
        from cosalette._registration import _TelemetryRegistration
        from cosalette._wiring import resolve_enabled

        async def fn() -> dict[str, object]:
            return {}

        reg = _TelemetryRegistration(
            name="sensor",
            func=fn,
            injection_plan=[],
            interval=10.0,
            enabled_spec=lambda s: True,
            group="g",
            triggerable="local",
        )

        with pytest.raises(ValueError, match="triggerable= and group="):
            resolve_enabled([reg], [], [], Settings(), store=None)

    def test_callable_enabled_root_mqtt_trigger_raises_during_bootstrap(self) -> None:
        """Deferred enabled telemetry still rejects MQTT triggers on roots.

        Technique: Regression — callable enabled= defers this validation until
        bootstrap confirms the registration survives.
        """
        from cosalette._registration import _TelemetryRegistration
        from cosalette._wiring import resolve_enabled

        async def fn() -> dict[str, object]:
            return {}

        reg = _TelemetryRegistration(
            name="root_sensor",
            func=fn,
            injection_plan=[],
            interval=10.0,
            enabled_spec=lambda s: True,
            is_root=True,
            triggerable="mqtt",
        )

        with pytest.raises(ValueError, match="requires a named device"):
            resolve_enabled([reg], [], [], Settings(), store=None)

    def test_callable_settings_argument_received(self) -> None:
        """The callable receives the Settings instance passed to resolve_enabled."""
        from cosalette._wiring import resolve_enabled

        received: list[Settings] = []
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

        async def fn(ctx: DeviceContext) -> AsyncIterator[None]:
            yield
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
        dev: list[object] = []
        cmd: list[object] = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 1

    def test_falsy_non_bool_removes_entry(self) -> None:
        """Callable returning a falsy non-bool value drops the registration."""
        from cosalette._wiring import resolve_enabled

        settings = Settings()
        tel = [self._make_telemetry("x", lambda s: 0)]
        dev: list[object] = []
        cmd: list[object] = []

        resolve_enabled(tel, dev, cmd, settings, store=None)  # ty: ignore[invalid-argument-type]

        assert len(tel) == 0
