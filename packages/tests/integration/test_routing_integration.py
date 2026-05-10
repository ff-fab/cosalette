"""Integration tests — command routing (iterator and sub-topic).

Validates ctx.commands() async iterator pull-based command consumption
and sub-topic command routing end-to-end.

See Also:
    ADR-025 — Command channel and sub-topic routing.
    ADR-007 — Testing strategy (integration layer).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from cosalette._command import Command
from cosalette._context import DeviceContext
from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# TestCommandsIterator — ctx.commands() integration tests
# ---------------------------------------------------------------------------


class TestCommandsIterator:
    """Integration tests for ``ctx.commands()`` async iterator.

    Validates that devices using the pull-based ``async for cmd in
    ctx.commands():`` pattern receive routed MQTT commands via the
    device proxy, including multi-device routing, drain-on-shutdown,
    and high-throughput FIFO ordering.

    Technique: Integration Testing — exercises the real App
    orchestrator with ``AppHarness`` test doubles.
    """

    async def test_device_receives_command_via_commands_iterator(self) -> None:
        """Device using ctx.commands() receives MQTT command via deliver().

        Technique: State-based Testing — register device with
        ctx.commands(), deliver a message, verify the Command object
        has correct topic, payload, and a positive timestamp.
        """
        harness = AppHarness.create()
        received: list[Command] = []
        handler_registered = asyncio.Event()
        command_received = asyncio.Event()

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            handler_registered.set()
            async for cmd in cmds:
                received.append(cmd)  # ty: ignore[invalid-argument-type]
                command_received.set()
                yield
                break

        async def _simulate() -> None:
            await handler_registered.wait()
            await harness.mqtt.deliver("testapp/blind/set", "OPEN")
            await command_received.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(received) == 1
        assert received[0].topic == "testapp/blind/set"
        assert received[0].payload == "OPEN"
        assert received[0].timestamp > 0

    async def test_multiple_devices_commands_routed_correctly(self) -> None:
        """Two devices with ctx.commands() receive only their own messages.

        Technique: State-based Testing — register two devices, deliver
        one message to each, verify no cross-routing.
        """
        harness = AppHarness.create()
        blind_cmds: list[Command] = []
        light_cmds: list[Command] = []
        blind_ready = asyncio.Event()
        light_ready = asyncio.Event()
        blind_done = asyncio.Event()
        light_done = asyncio.Event()

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            blind_ready.set()
            async for cmd in cmds:
                blind_cmds.append(cmd)  # ty: ignore[invalid-argument-type]
                blind_done.set()
                yield
                break

        @harness.app.device("light")
        async def light(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            light_ready.set()
            async for cmd in cmds:
                light_cmds.append(cmd)  # ty: ignore[invalid-argument-type]
                light_done.set()
                yield
                break

        async def _simulate() -> None:
            await blind_ready.wait()
            await light_ready.wait()
            await harness.mqtt.deliver("testapp/blind/set", "OPEN")
            await harness.mqtt.deliver("testapp/light/set", "ON")
            await blind_done.wait()
            await light_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(blind_cmds) == 1
        assert blind_cmds[0].payload == "OPEN"
        assert len(light_cmds) == 1
        assert light_cmds[0].payload == "ON"

    async def test_commands_and_on_command_coexist(self) -> None:
        """commands() on one device and on_command on another both work.

        Technique: Integration Testing — verify the proxy dispatches
        via _command_queue for commands() devices and via callback
        for on_command devices simultaneously.
        """
        harness = AppHarness.create()
        iter_received: list[Command] = []
        callback_received: list[str] = []
        blind_ready = asyncio.Event()
        light_ready = asyncio.Event()
        blind_done = asyncio.Event()
        light_done = asyncio.Event()

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            blind_ready.set()
            async for cmd in cmds:
                iter_received.append(cmd)  # ty: ignore[invalid-argument-type]
                blind_done.set()
                yield
                break

        @harness.app.device("light")
        async def light(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command
            async def handle(sub_topic: str | None, payload: str) -> None:
                callback_received.append(payload)
                light_done.set()

            light_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await blind_ready.wait()
            await light_ready.wait()
            await harness.mqtt.deliver("testapp/blind/set", "OPEN")
            await harness.mqtt.deliver("testapp/light/set", "ON")
            await blind_done.wait()
            await light_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(iter_received) == 1
        assert iter_received[0].payload == "OPEN"
        assert callback_received == ["ON"]

    async def test_commands_high_throughput_fifo(self) -> None:
        """100 commands queued rapidly are all consumed in FIFO order.

        Technique: State-based Testing — deliver 100 sequential
        commands, verify all received in order.
        """
        harness = AppHarness.create()
        received: list[Command] = []
        handler_registered = asyncio.Event()
        all_received = asyncio.Event()
        count = 100

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            cmds = ctx.commands()
            handler_registered.set()
            async for cmd in cmds:
                received.append(cmd)  # ty: ignore[invalid-argument-type]
                if len(received) >= count:
                    all_received.set()
                yield

        async def _simulate() -> None:
            await handler_registered.wait()
            for i in range(count):
                await harness.mqtt.deliver("testapp/blind/set", str(i))
            await all_received.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(received) == count
        assert [c.payload for c in received] == [str(i) for i in range(count)]


# ---------------------------------------------------------------------------
# TestSubTopicRouting — sub-topic command routing integration tests
# ---------------------------------------------------------------------------


class TestSubTopicRouting:
    """Integration tests for sub-topic command routing.

    Validates the end-to-end flow: AppHarness → Router → CommandRunner →
    device handler for commands delivered to ``{prefix}/{device}/{sub}/set``.

    Technique: Integration Testing — exercises the real orchestrator with
    ``AppHarness`` test doubles. Sub-topic routing follows ADR-025.

    See Also:
        ADR-025 — Command channel and sub-topic routing.
    """

    async def test_subtopic_routes_to_correct_handler(self) -> None:
        """Distinct sub-topic handlers each receive only their commands.

        Registers ``calibrate`` and ``reset`` sub-topic handlers, delivers
        a command to each, and verifies correct ``(sub_topic, payload)``
        dispatch.
        """
        harness = AppHarness.create()
        cal_payloads: list[tuple[str | None, str]] = []
        rst_payloads: list[tuple[str | None, str]] = []
        handlers_ready = asyncio.Event()
        cal_done = asyncio.Event()
        rst_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command("calibrate")
            async def handle_cal(sub_topic: str | None, payload: str) -> None:
                cal_payloads.append((sub_topic, payload))
                cal_done.set()

            @ctx.on_command("reset")
            async def handle_rst(sub_topic: str | None, payload: str) -> None:
                rst_payloads.append((sub_topic, payload))
                rst_done.set()

            handlers_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await handlers_ready.wait()
            await harness.mqtt.deliver("testapp/sensor/calibrate/set", "HIGH")
            await harness.mqtt.deliver("testapp/sensor/reset/set", "SOFT")
            await cal_done.wait()
            await rst_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert cal_payloads == [("calibrate", "HIGH")]
        assert rst_payloads == [("reset", "SOFT")]

    async def test_root_and_subtopic_coexist(self) -> None:
        """Root and sub-topic handlers on the same device dispatch correctly.

        Registers a root handler and a ``calibrate`` sub-topic handler,
        delivers one command to each, and verifies independent dispatch.
        """
        harness = AppHarness.create()
        root_payloads: list[tuple[str | None, str]] = []
        cal_payloads: list[tuple[str | None, str]] = []
        handlers_ready = asyncio.Event()
        root_done = asyncio.Event()
        cal_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command
            async def handle_root(sub_topic: str | None, payload: str) -> None:
                root_payloads.append((sub_topic, payload))
                root_done.set()

            @ctx.on_command("calibrate")
            async def handle_cal(sub_topic: str | None, payload: str) -> None:
                cal_payloads.append((sub_topic, payload))
                cal_done.set()

            handlers_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await handlers_ready.wait()
            await harness.mqtt.deliver("testapp/sensor/set", "STOP")
            await harness.mqtt.deliver("testapp/sensor/calibrate/set", "HIGH")
            await root_done.wait()
            await cal_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert root_payloads == [(None, "STOP")]
        assert cal_payloads == [("calibrate", "HIGH")]

    async def test_command_type_handler_with_subtopic(self) -> None:
        """New-style Command-annotated handler receives sub-topic commands.

        Registers a handler typed with ``Command``, delivers a sub-topic
        command, and verifies the ``Command`` object carries the correct
        ``sub_topic`` and ``payload``.
        """
        harness = AppHarness.create()
        received: list[Command] = []
        handlers_ready = asyncio.Event()
        cmd_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command("calibrate")
            async def handle_cal(cmd: Command) -> None:
                received.append(cmd)
                cmd_done.set()

            handlers_ready.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def _simulate() -> None:
            await handlers_ready.wait()
            await harness.mqtt.deliver("testapp/sensor/calibrate/set", "HIGH")
            await cmd_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert len(received) == 1
        assert received[0].sub_topic == "calibrate"
        assert received[0].payload == "HIGH"
        assert received[0].topic == "testapp/sensor/calibrate/set"
        assert received[0].timestamp > 0

    async def test_commands_iterator_receives_only_root(self) -> None:
        """``commands()`` yields only root commands; sub-topic handler gets its own.

        Registers ``commands()`` (root queue) and ``on_command("calibrate")``.
        Delivers both a root and a sub-topic command. Verifies ``commands()``
        only yields the root command while the sub-topic handler fires
        independently.
        """
        harness = AppHarness.create()
        iter_received: list[Command] = []
        cal_payloads: list[tuple[str | None, str]] = []
        ready = asyncio.Event()
        root_done = asyncio.Event()
        cal_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext) -> AsyncIterator[None]:
            @ctx.on_command("calibrate")
            async def handle_cal(sub_topic: str | None, payload: str) -> None:
                cal_payloads.append((sub_topic, payload))
                cal_done.set()

            cmds = ctx.commands()
            ready.set()
            async for cmd in cmds:
                iter_received.append(cmd)  # ty: ignore[invalid-argument-type]
                root_done.set()
                yield
                break

        async def _simulate() -> None:
            await ready.wait()
            await harness.mqtt.deliver("testapp/sensor/calibrate/set", "HIGH")
            await harness.mqtt.deliver("testapp/sensor/set", "STOP")
            await cal_done.wait()
            await root_done.wait()
            harness.trigger_shutdown()

        asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # commands() only got the root command
        assert len(iter_received) == 1
        assert iter_received[0].payload == "STOP"
        assert iter_received[0].sub_topic is None

        # sub-topic handler got its command
        assert cal_payloads == [("calibrate", "HIGH")]
