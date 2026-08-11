"""Integration tests — Router composition and triggerable telemetry under prefix.

Validates that two routers mounted at different MQTT prefixes each receive
only their own messages, and that triggerable telemetry under a router prefix
fires on MQTT trigger.

See Also:
    ADR-044 — Public Router and composition API.
    ADR-007 — Testing strategy (integration layer).
"""

from __future__ import annotations

import asyncio
import json

import pytest

import cosalette
from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# TestRouterComposition — prefix routing and cross-talk isolation
# ---------------------------------------------------------------------------


class TestRouterComposition:
    """Router composition: distinct prefixes isolate command dispatch.

    Validates that two routers mounted at different MQTT prefixes each
    receive only their own messages; messages to one prefix do not
    cross-talk into the other router's handlers.

    Technique:
        - Integration Testing: full lifecycle via AppHarness.
        - State-based Testing: inspect published state topics to prove
          only the targeted handler fired.

    See Also:
        ADR-044 — Public Router and composition API.
    """

    async def test_two_routers_no_crosstalk(self) -> None:
        """Commands to zone1 do not reach zone2 and vice versa.

        Registers two routers at prefixes 'zone1' and 'zone2', each with a
        'valve' command. Delivers a command to zone1/valve/set and asserts
        only zone1's handler fires; then vice versa.

        Technique: State-based Testing + Integration Testing.
        """
        harness = AppHarness.create()

        zone1_calls: list[str] = []
        zone2_calls: list[str] = []

        router1 = cosalette.Router(prefix="zone1")
        router2 = cosalette.Router(prefix="zone2")

        @router1.command("valve")
        async def zone1_valve(payload: str) -> dict[str, object]:
            zone1_calls.append(payload)
            return {"zone": "zone1", "cmd": payload}

        @router2.command("valve")
        async def zone2_valve(payload: str) -> dict[str, object]:
            zone2_calls.append(payload)
            return {"zone": "zone2", "cmd": payload}

        harness.app.include_router(router1)
        harness.app.include_router(router2)

        # Dispatch to zone1/valve — only zone1 handler must fire
        await harness.call_command("zone1/valve", "OPEN")
        assert zone1_calls == ["OPEN"], "zone1 valve handler must fire"
        assert zone2_calls == [], "zone2 must not receive zone1's command"

        # Dispatch to zone2/valve — only zone2 handler must fire
        await harness.call_command("zone2/valve", "CLOSE")
        assert zone1_calls == ["OPEN"], "zone1 must not receive zone2's command"
        assert zone2_calls == ["CLOSE"], "zone2 valve handler must fire"

        # Verify correct state topics published for each zone
        zone1_msgs = harness.messages_for("testapp/zone1/valve/state")
        zone2_msgs = harness.messages_for("testapp/zone2/valve/state")
        assert len(zone1_msgs) == 1
        assert json.loads(zone1_msgs[0][0])["zone"] == "zone1"
        assert len(zone2_msgs) == 1
        assert json.loads(zone2_msgs[0][0])["zone"] == "zone2"

    async def test_slash_composed_command_topic_routed_correctly(self) -> None:
        """Router-prefixed command is invoked via slash-composed name.

        A command registered as 'calibrate' on a router with prefix='sensors'
        produces a registration named 'sensors/calibrate'. call_command must
        find it and route to the exact handler; the state publishes to
        testapp/sensors/calibrate/state.

        Technique: State-based Testing + Integration Testing.
        """
        harness = AppHarness.create()
        handler_invoked = False

        router = cosalette.Router(prefix="sensors")

        @router.command("calibrate")
        async def calibrate(payload: str) -> dict[str, object]:
            nonlocal handler_invoked
            handler_invoked = True
            return {"calibrated": True, "ref": payload}

        harness.app.include_router(router)

        await harness.call_command("sensors/calibrate", '{"ref": "factory"}')

        assert handler_invoked
        msgs = harness.messages_for("testapp/sensors/calibrate/state")
        assert len(msgs) == 1
        assert json.loads(msgs[0][0])["calibrated"] is True

    async def test_slash_composed_command_live_mqtt_delivery(self) -> None:
        """slash-composed router command receives live MQTT delivery via harness.run().

        Registers a command on a router with prefix='floor1', included under
        prefix='building', producing the full name 'building/floor1/calibrate'.
        Delivers a real MQTT message to testapp/building/floor1/calibrate/set
        and asserts the handler receives the exact topic and payload, and that
        state is published to testapp/building/floor1/calibrate/state.

        This test exercises the real TopicRouter dispatch path — it does NOT
        use call_command(); the message travels the full
        MockMqttClient → TopicRouter → CommandRunner pipeline.

        Technique: Integration Testing + State-based Testing.

        See Also:
            ADR-044 — Public Router and composition API.
        """
        harness = AppHarness.create()
        handler_calls: list[tuple[str, str]] = []
        command_done = asyncio.Event()

        router = cosalette.Router(prefix="floor1")

        @router.command("calibrate")
        async def calibrate(topic: str, payload: str) -> dict[str, object]:
            handler_calls.append((topic, payload))
            command_done.set()
            return {"calibrated": True, "echo": payload}

        harness.app.include_router(router, prefix="building")

        async def _orchestrate() -> None:
            # Poll until subscriptions are registered before delivery.
            while not harness.mqtt.subscriptions:
                await asyncio.sleep(0)
            await harness.mqtt.deliver(
                "testapp/building/floor1/calibrate/set", "factory"
            )
            await command_done.wait()
            # Poll until state is published before triggering shutdown.
            while not harness.messages_for("testapp/building/floor1/calibrate/state"):
                await asyncio.sleep(0)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        # Handler received exact topic and payload
        assert handler_calls == [("testapp/building/floor1/calibrate/set", "factory")]

        # State published to the composed topic
        msgs = harness.messages_for("testapp/building/floor1/calibrate/state")
        assert len(msgs) == 1
        result = json.loads(msgs[0][0])
        assert result["calibrated"] is True
        assert result["echo"] == "factory"

    async def test_slash_composed_command_subtopic_live_mqtt(self) -> None:
        """Subtopic delivered below a slash-composed router command is routed.

        Same router/include_router setup as the base test; delivers to
        testapp/building/floor1/measure/fine/set (a sub-topic of 'measure')
        and asserts the handler fires with the full topic.

        Technique: Integration Testing + State-based Testing.
        """
        harness = AppHarness.create()
        handler_calls: list[tuple[str, str]] = []
        command_done = asyncio.Event()

        router = cosalette.Router(prefix="floor1")

        @router.command("measure")
        async def measure(topic: str, payload: str) -> dict[str, object]:
            handler_calls.append((topic, payload))
            command_done.set()
            return {"measured": True}

        harness.app.include_router(router, prefix="building")

        async def _orchestrate() -> None:
            while not harness.mqtt.subscriptions:
                await asyncio.sleep(0)
            await harness.mqtt.deliver(
                "testapp/building/floor1/measure/fine/set", "precise"
            )
            await command_done.wait()
            while not harness.messages_for("testapp/building/floor1/measure/state"):
                await asyncio.sleep(0)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_orchestrate())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        assert len(handler_calls) == 1
        assert handler_calls[0][0] == "testapp/building/floor1/measure/fine/set"
        assert handler_calls[0][1] == "precise"

    async def test_router_telemetry_publishes_to_prefixed_topic(self) -> None:
        """Telemetry on a router publishes to {prefix}/{name}/state.

        Registers a telemetry handler on a router with prefix='env', includes
        the router, runs the app, and asserts publication to
        testapp/env/temp/state.

        Technique: State-based Testing + Integration Testing.
        """
        harness = AppHarness.create()

        router = cosalette.Router(prefix="env")

        @router.telemetry("temp", interval=0.01)
        async def env_temp() -> dict[str, object]:
            return {"celsius": 23.5}

        harness.app.include_router(router)

        async def _shutdown() -> None:
            while not harness.messages_for("testapp/env/temp/state"):
                await asyncio.sleep(0)
            harness.trigger_shutdown()

        _task = asyncio.create_task(_shutdown())
        try:
            await asyncio.wait_for(harness.run(), timeout=5.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        msgs = harness.messages_for("testapp/env/temp/state")
        assert len(msgs) >= 1
        assert json.loads(msgs[0][0]) == {"celsius": 23.5}


# ---------------------------------------------------------------------------
# TestTriggerableTelemetryUnderRouterPrefix — triggerable under prefix
# ---------------------------------------------------------------------------


class TestTriggerableTelemetryUnderRouterPrefix:
    """Triggerable telemetry under a Router prefix fires on MQTT trigger.

    Technique:
        - Integration Testing: full lifecycle via AppHarness.
        - State-based Testing: MQTT trigger to {prefix}/{name}/set fires
          handler immediately; result published to {prefix}/{name}/state.

    See Also:
        ADR-044 — Public Router and composition API.
    """

    async def test_triggerable_under_router_prefix_fires_on_set(self) -> None:
        """MQTT message to testapp/zone/sensor/set triggers the handler.

        Router with prefix='zone' has a triggerable telemetry 'sensor'.
        After include_router the name becomes 'zone/sensor'. Delivering to
        testapp/zone/sensor/set must fire the handler immediately beyond the
        first scheduled cycle.

        Technique: Integration Testing + State-based Testing.
        """
        harness = AppHarness.create()
        call_count = 0
        triggered_fired = asyncio.Event()

        router = cosalette.Router(prefix="zone")

        @router.telemetry("sensor", interval=3600, triggerable=True)
        async def zone_sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                triggered_fired.set()
            return {"value": call_count, "prefix": "zone"}

        harness.app.include_router(router)

        async def _simulate() -> None:
            # Wait for first scheduled publish
            while not harness.mqtt.get_messages_for("testapp/zone/sensor/state"):
                await asyncio.sleep(0.01)
            # Fire trigger
            await harness.mqtt.deliver("testapp/zone/sensor/set", "")
            await triggered_fired.wait()
            harness.trigger_shutdown()

        _task = asyncio.create_task(_simulate())
        try:
            await asyncio.wait_for(harness.run(), timeout=10.0)
        finally:
            _task.cancel()
            await asyncio.gather(_task, return_exceptions=True)

        assert call_count >= 2, (
            "Handler must be called at least twice (scheduled + triggered)"
        )
        msgs = harness.messages_for("testapp/zone/sensor/state")
        assert len(msgs) >= 2
        # Last publish must contain 'prefix' key
        last_payload = json.loads(msgs[-1][0])
        assert last_payload["prefix"] == "zone"
