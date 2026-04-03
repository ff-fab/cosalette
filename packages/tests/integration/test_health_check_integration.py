"""Integration tests — adapter health check lifecycle (COS-497.4).

Validates the full health check lifecycle: adapter registration →
periodic health probing → availability transitions → telemetry
coexistence → interval disabling.

Test Techniques Used:
    - Integration Testing: end-to-end lifecycle via AppHarness.
    - State-based Testing: verify published availability messages.
    - Stateful Stub: countdown adapter transitions from healthy to
      unhealthy after N checks.

See Also:
    ADR-028 — Adapter health check protocol.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

import pytest

from cosalette._context import DeviceContext
from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Adapter stubs
# ---------------------------------------------------------------------------


@runtime_checkable
class CheckablePort(Protocol):
    """Port protocol with a single method for DI recognition."""

    def ping(self) -> bool: ...


class _CountdownAdapter:
    """Adapter that passes health checks N times, then fails.

    Implements both the port protocol (``ping``) and
    ``HealthCheckable`` (``health_check``).
    """

    def __init__(self, *, remaining: int = 3) -> None:
        self._remaining = remaining

    def ping(self) -> bool:
        return True

    async def health_check(self) -> bool:
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False


class _AlwaysUnhealthyAdapter:
    """Adapter whose health check always fails."""

    def ping(self) -> bool:
        return True

    async def health_check(self) -> bool:
        return False


class _TrackedAdapter:
    """Adapter that counts health_check calls."""

    def __init__(self) -> None:
        self.call_count = 0

    def ping(self) -> bool:
        return True

    async def health_check(self) -> bool:
        self.call_count += 1
        return True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthCheckIntegration:
    """Integration tests for the adapter health check lifecycle."""

    async def test_adapter_failing_after_countdown_publishes_offline(self) -> None:
        """Adapter transitions from online to offline after N healthy checks.

        Technique: Stateful Stub — _CountdownAdapter returns True for
        the first 3 calls, then False.  The framework publishes
        "offline" on the device availability topic once the adapter
        starts failing.
        """
        harness = AppHarness.create()
        harness.app._health_check_interval = 0.01

        # Pre-instantiate so the framework uses our stateful instance.
        countdown = _CountdownAdapter(remaining=3)
        harness.app.adapter(CheckablePort, lambda: countdown)

        device_started = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext, adapter: CheckablePort) -> None:
            device_started.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        async def _shutdown() -> None:
            await device_started.wait()
            # Give enough time for the countdown to expire and the
            # "offline" availability message to be published.
            await asyncio.sleep(0.15)
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        avail = harness.mqtt.get_messages_for("testapp/sensor/availability")
        payloads = [payload for payload, _retain, _qos in avail]

        # Must start online and transition to offline.
        assert "online" in payloads
        assert "offline" in payloads
        # The offline message must come after an online message.
        assert payloads.index("online") < payloads.index("offline")

    async def test_telemetry_continues_despite_unhealthy_adapter(self) -> None:
        """Telemetry keeps publishing even when adapter is unhealthy.

        Technique: State-based Testing — an always-unhealthy adapter
        does not block telemetry publication.  Health checks are
        informational; telemetry messages should still appear.
        """
        harness = AppHarness.create()
        harness.app._health_check_interval = 0.01

        unhealthy = _AlwaysUnhealthyAdapter()
        harness.app.adapter(CheckablePort, lambda: unhealthy)

        telemetry_published = asyncio.Event()

        @harness.app.device("monitor")
        async def monitor(ctx: DeviceContext, adapter: CheckablePort) -> None:
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        @harness.app.telemetry("temp", interval=0.01)
        async def telem(ctx: DeviceContext) -> dict[str, object]:
            telemetry_published.set()
            return {"value": 1}

        async def _shutdown() -> None:
            await telemetry_published.wait()
            # Let a few health checks and telemetry cycles run.
            await asyncio.sleep(0.1)
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Telemetry messages were published despite unhealthy adapter.
        state_msgs = harness.mqtt.get_messages_for("testapp/temp/state")
        assert len(state_msgs) >= 1

        # Adapter is indeed marked offline.
        avail = harness.mqtt.get_messages_for("testapp/monitor/availability")
        payloads = [p for p, _r, _q in avail]
        assert "offline" in payloads

    async def test_none_interval_disables_health_check(self) -> None:
        """Setting health_check_interval=None prevents health_check calls.

        Technique: Call Counter — _TrackedAdapter counts calls.  With
        interval disabled, the counter must remain at zero after the
        lifecycle completes.
        """
        harness = AppHarness.create()
        harness.app._health_check_interval = None

        tracked = _TrackedAdapter()
        harness.app.adapter(CheckablePort, lambda: tracked)

        device_done = asyncio.Event()

        @harness.app.device("sensor")
        async def sensor(ctx: DeviceContext, adapter: CheckablePort) -> None:
            device_done.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)

        async def _shutdown() -> None:
            await device_done.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        _shutdown_task = asyncio.create_task(_shutdown())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        assert tracked.call_count == 0
