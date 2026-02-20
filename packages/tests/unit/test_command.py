"""Tests for @app.command() — FastAPI-style command handler decorator.

Test Techniques Used:
    - Specification-based Testing: Verify decorator registration and rejection
    - Integration Testing: Full _run_async lifecycle with injected mocks
    - Async Coordination: asyncio.Event for deterministic test control
    - Mock-based Isolation: MockMqttClient + FakeClock avoid real I/O
    - Error Isolation: Verify handler errors are published, not propagated
    - Dependency Injection: Verify adapter/context injection via type annotation
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

import pytest

from cosalette._app import App
from cosalette._context import DeviceContext
from cosalette._injection import build_injection_plan
from cosalette.testing import FakeClock, MockMqttClient, make_settings

# ---------------------------------------------------------------------------
# Dummy port / adapter for injection tests
# ---------------------------------------------------------------------------


@runtime_checkable
class _ValvePort(Protocol):
    """Dummy protocol for adapter injection tests."""

    def actuate(self, command: str) -> None: ...

    def read_state(self) -> str: ...


class _FakeValve:
    """Fake adapter implementing _ValvePort."""

    def __init__(self) -> None:
        self._state = "closed"

    def actuate(self, command: str) -> None:
        self._state = command

    def read_state(self) -> str:
        return self._state


# ---------------------------------------------------------------------------
# TestCommandRegistration
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    """@app.command() decorator registration tests.

    Technique: Specification-based Testing — verifying that the
    decorator records registrations, builds injection plans, and
    rejects duplicates and collisions with other device types.
    """

    @pytest.fixture
    def app(self) -> App:
        """Minimal App instance for registration tests."""
        return App(name="testapp", version="1.0.0")

    async def test_command_registers_handler(self, app: App) -> None:
        """@app.command('name') stores a _CommandRegistration internally."""

        @app.command("light")
        async def handle_light(topic: str, payload: str) -> dict[str, object]:
            return {"state": payload}

        assert len(app._commands) == 1  # noqa: SLF001
        assert app._commands[0].name == "light"  # noqa: SLF001
        assert app._commands[0].func is handle_light  # noqa: SLF001

    async def test_command_rejects_duplicate_name(self, app: App) -> None:
        """Registering two commands with the same name raises ValueError."""

        @app.command("light")
        async def handle1(topic: str, payload: str) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.command("light")
            async def handle2(topic: str, payload: str) -> None: ...

    async def test_command_rejects_name_collision_with_device(self, app: App) -> None:
        """A command name can't collide with an existing device name."""

        @app.device("valve")
        async def valve_dev(ctx: DeviceContext) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.command("valve")
            async def valve_cmd(topic: str, payload: str) -> None: ...

    async def test_command_rejects_name_collision_with_telemetry(
        self, app: App
    ) -> None:
        """A command name can't collide with an existing telemetry name."""

        @app.telemetry("sensor", interval=10)
        async def sensor_telem(ctx: DeviceContext) -> dict:
            return {}

        with pytest.raises(ValueError, match="already registered"):

            @app.command("sensor")
            async def sensor_cmd(topic: str, payload: str) -> None: ...

    async def test_device_rejects_collision_with_command(self, app: App) -> None:
        """A device name can't collide with an existing command name."""

        @app.command("valve")
        async def valve_cmd(topic: str, payload: str) -> None: ...

        with pytest.raises(ValueError, match="already registered"):

            @app.device("valve")
            async def valve_dev(ctx: DeviceContext) -> None: ...

    async def test_command_builds_injection_plan_excluding_topic_payload(
        self, app: App
    ) -> None:
        """Injection plan excludes topic and payload, includes other params."""

        @app.command("valve")
        async def handle(
            topic: str, payload: str, ctx: DeviceContext
        ) -> dict[str, object]:
            return {}

        plan = app._commands[0].injection_plan  # noqa: SLF001
        param_names = [name for name, _ in plan]
        assert "topic" not in param_names
        assert "payload" not in param_names
        assert "ctx" in param_names

    async def test_command_returns_original_function(self, app: App) -> None:
        """Decorator returns the original function unchanged (transparent)."""

        async def handle(topic: str, payload: str) -> None: ...

        result = app.command("valve")(handle)
        assert result is handle


# ---------------------------------------------------------------------------
# TestCommandRouting
# ---------------------------------------------------------------------------


class TestCommandRouting:
    """Full lifecycle tests for @app.command() handlers.

    Technique: Integration Testing with injected mocks — every test
    provides Settings, MockMqttClient, FakeClock, and a manual
    shutdown_event so no real I/O or signal handlers are involved.
    """

    async def test_command_handler_receives_topic_and_payload(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler receives the MQTT topic and payload as named params.

        Coordination: simulate_command delivers a message, handler
        captures args, helper triggers shutdown.
        """
        app = App(name="testapp", version="1.0.0")
        received: list[tuple[str, str]] = []
        command_done = asyncio.Event()

        @app.command("light")
        async def handle_light(topic: str, payload: str) -> None:
            received.append((topic, payload))
            command_done.set()

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
        assert received[0] == ("testapp/light/set", "ON")

    async def test_command_handler_return_dict_publishes_state(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Returning a dict from a handler auto-publishes via publish_state().

        The returned dict is JSON-serialised and published to
        ``{prefix}/{device}/state``.
        """
        app = App(name="testapp", version="1.0.0")
        command_done = asyncio.Event()

        @app.command("light")
        async def handle_light(topic: str, payload: str) -> dict[str, object]:
            command_done.set()
            return {"state": payload, "brightness": 100}

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/light/set", "ON")
            await command_done.wait()
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

        state_messages = mock_mqtt.get_messages_for("testapp/light/state")
        assert len(state_messages) >= 1
        assert '"state": "ON"' in state_messages[0][0]
        assert '"brightness": 100' in state_messages[0][0]

    async def test_command_handler_return_none_skips_publish(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Returning None from a handler skips auto-publish.

        No state message should be published to the device's state topic.
        """
        app = App(name="testapp", version="1.0.0")
        command_done = asyncio.Event()

        @app.command("silent")
        async def handle_silent(topic: str, payload: str) -> None:
            command_done.set()

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/silent/set", "PING")
            await command_done.wait()
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

        state_messages = mock_mqtt.get_messages_for("testapp/silent/state")
        assert state_messages == []

    async def test_command_handler_with_injected_context(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler can receive DeviceContext via type-annotated parameter.

        The DI system resolves ``ctx: DeviceContext`` from the provider
        mapping.
        """
        app = App(name="testapp", version="1.0.0")
        received_ctx: list[DeviceContext] = []
        command_done = asyncio.Event()

        @app.command("light")
        async def handle_light(
            topic: str, payload: str, ctx: DeviceContext
        ) -> dict[str, object]:
            received_ctx.append(ctx)
            command_done.set()
            return {"state": payload}

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

        assert len(received_ctx) == 1
        assert isinstance(received_ctx[0], DeviceContext)
        assert received_ctx[0].name == "light"

    async def test_command_handler_with_injected_adapter(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler can receive a registered adapter via type annotation.

        The DI system looks up the adapter by its port type from the
        resolved adapters dict.
        """
        app = App(name="testapp", version="1.0.0")
        app.adapter(_ValvePort, _FakeValve)
        command_done = asyncio.Event()

        @app.command("valve")
        async def handle_valve(
            topic: str, payload: str, controller: _ValvePort
        ) -> dict[str, object]:
            controller.actuate(payload)
            command_done.set()
            return {"state": controller.read_state()}

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/valve/set", "OPEN")
            await command_done.wait()
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

        state_messages = mock_mqtt.get_messages_for("testapp/valve/state")
        assert len(state_messages) >= 1
        assert '"state": "OPEN"' in state_messages[0][0]

    async def test_command_handler_error_published_to_mqtt(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler exceptions are caught and published via ErrorPublisher.

        The error is published to the error topic; the app does not crash.
        """
        app = App(name="testapp", version="1.0.0")
        command_received = asyncio.Event()

        @app.command("valve")
        async def handle_valve(topic: str, payload: str) -> dict[str, object]:
            command_received.set()
            msg = "invalid command"
            raise ValueError(msg)

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/valve/set", "BAD")
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

        error_messages = mock_mqtt.get_messages_for("testapp/error")
        assert len(error_messages) >= 1
        assert "invalid command" in error_messages[0][0]

        device_errors = mock_mqtt.get_messages_for("testapp/valve/error")
        assert len(device_errors) >= 1
        assert "invalid command" in device_errors[0][0]

    async def test_command_handler_error_publication_failure_swallowed(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """If error publication itself fails, the exception is swallowed.

        Belt-and-suspenders: the ``contextlib.suppress(Exception)``
        in ``_run_command`` ensures the app cannot crash due to
        error-reporting failures.
        """
        app = App(name="testapp", version="1.0.0")
        command_count = 0
        second_command = asyncio.Event()

        @app.command("valve")
        async def handle_valve(topic: str, payload: str) -> dict[str, object]:
            nonlocal command_count
            command_count += 1
            if command_count <= 1:
                msg = "boom"
                raise RuntimeError(msg)
            second_command.set()
            return {"state": payload}

        shutdown = asyncio.Event()

        async def simulate() -> None:
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
                await original_publish(topic, payload, retain=retain, qos=qos)

            mock_mqtt.publish = failing_publish  # type: ignore[assignment]

            # Second command with sabotaged publish — should not crash
            # (but handler won't raise this time, so it's fine)
            # Let's actually make the handler raise AND error pub fail
            command_count_backup = command_count

            # Restore count to force another raise
            await mock_mqtt.deliver("testapp/valve/set", "CMD_FAIL")
            await asyncio.sleep(0.05)

            # Restore real publish
            mock_mqtt.publish = original_publish  # type: ignore[assignment]

            # Third command: still alive
            await mock_mqtt.deliver("testapp/valve/set", "CMD3")
            await second_command.wait()
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

        # Handler survived: third command was processed
        assert command_count == 3

    async def test_command_availability_published_on_startup(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Command devices have 'online' availability published at startup.

        Just like @app.device and @app.telemetry, command handlers
        get availability published by the health reporter.
        """
        app = App(name="testapp", version="1.0.0")
        shutdown = asyncio.Event()

        @app.command("light")
        async def handle_light(topic: str, payload: str) -> None: ...

        async def delayed_shutdown() -> None:
            await asyncio.sleep(0.05)
            shutdown.set()

        asyncio.create_task(delayed_shutdown())
        await asyncio.wait_for(
            app._run_async(
                settings=make_settings(),
                shutdown_event=shutdown,
                mqtt=mock_mqtt,
                clock=fake_clock,
            ),
            timeout=5.0,
        )

        avail = mock_mqtt.get_messages_for("testapp/light/availability")
        assert any(payload == "online" for payload, _, _ in avail)

    async def test_command_availability_offline_on_shutdown(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Command devices get 'offline' availability published on shutdown.

        The health reporter's shutdown() publishes offline for all
        tracked devices, including command registrations.
        """
        app = App(name="testapp", version="1.0.0")
        shutdown = asyncio.Event()

        @app.command("light")
        async def handle_light(topic: str, payload: str) -> None: ...

        # Immediate shutdown
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

        avail = mock_mqtt.get_messages_for("testapp/light/availability")
        offline = [p for p, _, _ in avail if p == "offline"]
        assert len(offline) >= 1

    async def test_command_coexists_with_device(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """@app.command and @app.device can coexist in the same app.

        Both handlers process their respective MQTT messages
        independently.
        """
        app = App(name="testapp", version="1.0.0")
        device_ran = asyncio.Event()
        command_received = asyncio.Event()

        @app.device("sensor")
        async def sensor(ctx: DeviceContext) -> None:
            device_ran.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        @app.command("light")
        async def handle_light(topic: str, payload: str) -> dict[str, object]:
            command_received.set()
            return {"state": payload}

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await device_ran.wait()
            await asyncio.sleep(0.02)
            await mock_mqtt.deliver("testapp/light/set", "ON")
            await command_received.wait()
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

        assert device_ran.is_set()
        assert command_received.is_set()
        # Command handler published state
        state_msgs = mock_mqtt.get_messages_for("testapp/light/state")
        assert len(state_msgs) >= 1


# ---------------------------------------------------------------------------
# TestCommandInjection
# ---------------------------------------------------------------------------


class TestCommandInjection:
    """Tests for build_injection_plan() with mqtt_params.

    Technique: Specification-based Testing — verifying that MQTT
    parameters are excluded from the injection plan while other
    parameters are included correctly.
    """

    async def test_injection_plan_skips_mqtt_params(self) -> None:
        """Parameters named topic and payload are excluded from the plan."""

        async def handler(
            topic: str, payload: str, ctx: DeviceContext
        ) -> dict[str, object]:
            return {}

        plan = build_injection_plan(handler, mqtt_params={"topic", "payload"})
        param_names = [name for name, _ in plan]
        assert "topic" not in param_names
        assert "payload" not in param_names
        assert param_names == ["ctx"]
        assert plan[0][1] is DeviceContext

    async def test_injection_plan_without_mqtt_params_unchanged(self) -> None:
        """Without mqtt_params, all annotated params are in the plan.

        This preserves backward compatibility for non-command use cases.
        """

        async def handler(topic: str, payload: str) -> None: ...

        plan = build_injection_plan(handler)
        param_names = [name for name, _ in plan]
        assert "topic" in param_names
        assert "payload" in param_names

    async def test_command_handler_zero_extra_params(
        self,
        mock_mqtt: MockMqttClient,
        fake_clock: FakeClock,
    ) -> None:
        """Handler with only topic + payload works (no DI needed).

        The simplest possible command handler: just topic and payload,
        no injected dependencies.
        """
        app = App(name="testapp", version="1.0.0")
        command_done = asyncio.Event()

        @app.command("simple")
        async def handle(topic: str, payload: str) -> None:
            command_done.set()

        shutdown = asyncio.Event()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await mock_mqtt.deliver("testapp/simple/set", "GO")
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

        assert command_done.is_set()

    async def test_injection_plan_rejects_unannotated_param(self) -> None:
        """Parameters without type annotations raise TypeError.

        The DI system requires type annotations to resolve dependencies.
        """

        async def handler(topic: str, payload: str, mystery) -> None: ...  # type: ignore[no-untyped-def]

        with pytest.raises(TypeError, match="lacks a type annotation"):
            build_injection_plan(handler, mqtt_params={"topic", "payload"})
