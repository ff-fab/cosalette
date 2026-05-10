"""Integration tests — telemetry coalescing groups.

Validates grouped telemetry handlers via AppHarness to verify correct
batching, MQTT publishing, and coexistence with ungrouped handlers.

See Also:
    ADR-018 — Coalescing Groups.
    ADR-007 — Testing strategy (integration layer).
"""

from __future__ import annotations

import asyncio

import pytest

from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration


class TestCoalescingGroupsIntegration:
    """Integration tests for telemetry coalescing groups.

    Technique: Integration Testing — exercises grouped telemetry
    handlers via AppHarness to verify correct batching, MQTT
    publishing, and coexistence with ungrouped handlers.

    See Also:
        ADR-018 — Coalescing Groups.
    """

    async def test_grouped_telemetry_publishes_via_harness(self) -> None:
        """Grouped telemetry fires and publishes MQTT messages via AppHarness.

        Technique: State-based Testing — register grouped handler, run
        lifecycle, inspect MockMqttClient for expected MQTT messages.
        """
        harness = AppHarness.create()
        called = asyncio.Event()

        @harness.app.telemetry(name="sensor", interval=0.01, group="bus")
        async def sensor() -> dict[str, object]:
            called.set()
            return {"value": 1}

        async def trigger_shutdown() -> None:
            await called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert called.is_set()
        msgs = harness.mqtt.get_messages_for("testapp/sensor/state")
        assert len(msgs) >= 1

    async def test_grouped_and_ungrouped_coexist_via_harness(self) -> None:
        """Grouped and ungrouped handlers coexist in the same app.

        Technique: State-based Testing — register grouped and ungrouped
        handlers, run lifecycle, verify both publish independently.
        """
        harness = AppHarness.create()
        grouped_called = asyncio.Event()
        ungrouped_called = asyncio.Event()

        @harness.app.telemetry(name="grouped_sensor", interval=0.01, group="bus")
        async def grouped_sensor() -> dict[str, object]:
            grouped_called.set()
            return {"g": 1}

        @harness.app.telemetry(name="solo_sensor", interval=0.01)
        async def solo_sensor() -> dict[str, object]:
            ungrouped_called.set()
            return {"u": 2}

        async def trigger_shutdown() -> None:
            await grouped_called.wait()
            await ungrouped_called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(trigger_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert grouped_called.is_set()
        assert ungrouped_called.is_set()
        grouped_msgs = harness.mqtt.get_messages_for("testapp/grouped_sensor/state")
        assert len(grouped_msgs) >= 1
        solo_msgs = harness.mqtt.get_messages_for("testapp/solo_sensor/state")
        assert len(solo_msgs) >= 1
