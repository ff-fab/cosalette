"""Integration tests — transport availability signaling (end-to-end).

Runs the full framework lifecycle via AppHarness to verify that
availability MQTT messages are published correctly when handlers
raise exceptions caught by `unavailable_on`, and that the
auto-recovery path publishes "online" after a subsequent success.

Test Techniques Used:
    - Integration Testing: full _run_async lifecycle via AppHarness.
    - State-based Testing: inspect published MQTT payloads for the
      device availability topic.
    - Async Coordination: asyncio.Event for deterministic test control.

See Also:
    ADR-011 — Error handling and publishing.
    ADR-012 — Health and availability reporting.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from cosalette._context import DeviceContext
from cosalette.testing import AppHarness

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTransportAvailabilityIntegration:
    """Full-lifecycle tests for transport availability signaling."""

    async def test_unavailable_on_fires_on_matching_exception(self) -> None:
        """Matching exception → 'offline' published, error topic populated.

        The handler raises a matching exception; the framework should
        suppress it, call mark_unavailable(), and publish to the error
        topic.  The availability topic will contain 'offline'.
        """

        class TransportError(Exception):
            pass

        harness = AppHarness.create(name="testapp")
        handler_called = asyncio.Event()

        @harness.app.command("sensor", unavailable_on=(TransportError,))
        async def handle_sensor(ctx: DeviceContext) -> None:
            handler_called.set()
            raise TransportError("cannot reach device")

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await harness.inject_command("sensor", "")
            await handler_called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        avail_payloads = [
            p for p, _, _ in harness.messages_for("testapp/sensor/availability")
        ]
        assert "offline" in avail_payloads

        # Error must be published (exception was caught and forwarded)
        harness.assert_published("testapp/sensor/error")

    async def test_unavailable_on_auto_recovers(self) -> None:
        """After offline, a successful call restores 'online' availability.

        First invocation raises TransportError (→ offline).
        Second invocation succeeds (→ auto-recovery online).
        The availability sequence must contain 'offline' followed by 'online'.
        """

        class TransportError(Exception):
            pass

        harness = AppHarness.create(name="testapp")
        call_count = [0]
        first_done = asyncio.Event()
        second_done = asyncio.Event()

        @harness.app.command("sensor", unavailable_on=(TransportError,))
        async def handle_sensor(ctx: DeviceContext) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                first_done.set()
                raise TransportError("first call fails")
            second_done.set()
            # Second call succeeds → auto-recovery

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            # First command: goes offline
            await harness.inject_command("sensor", "")
            await first_done.wait()
            await asyncio.sleep(0.02)
            # Second command: recovers online
            await harness.inject_command("sensor", "")
            await second_done.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        avail_payloads = [
            p for p, _, _ in harness.messages_for("testapp/sensor/availability")
        ]
        assert "offline" in avail_payloads
        assert "online" in avail_payloads

        # 'online' must appear *after* 'offline' (recovery, not just startup)
        offline_idx = next(i for i, p in enumerate(avail_payloads) if p == "offline")
        # find 'online' that comes after the first 'offline'
        recovery_online = next(
            (
                i
                for i, p in enumerate(avail_payloads)
                if p == "online" and i > offline_idx
            ),  # noqa: E501
            None,
        )
        assert recovery_online is not None, (
            f"Expected 'online' after 'offline' in {avail_payloads}"
        )

    async def test_mark_available_from_telemetry_handler(self) -> None:
        """ctx.mark_available() from a telemetry handler publishes 'online'.

        First cycle: handler calls ctx.mark_unavailable() -> offline.
        Second cycle: handler explicitly calls ctx.mark_available() ->
        online, with no reliance on auto-recovery (telemetry handlers
        do not auto-recover; recovery must be explicit per ADR-047).
        """
        harness = AppHarness.create(name="testapp")
        call_count = [0]
        first_done = asyncio.Event()
        second_done = asyncio.Event()

        @harness.app.telemetry("sensor", interval=0.01)
        async def telem(ctx: DeviceContext) -> dict[str, object]:
            call_count[0] += 1
            if call_count[0] == 1:
                await ctx.mark_unavailable()
                first_done.set()
            elif call_count[0] == 2:
                await ctx.mark_available()
                second_done.set()
            return {"value": call_count[0]}

        async def simulate() -> None:
            await first_done.wait()
            await second_done.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        avail_payloads = [
            p for p, _, _ in harness.messages_for("testapp/sensor/availability")
        ]
        assert "offline" in avail_payloads
        assert "online" in avail_payloads

        offline_idx = next(i for i, p in enumerate(avail_payloads) if p == "offline")
        recovery_online = next(
            (
                i
                for i, p in enumerate(avail_payloads)
                if p == "online" and i > offline_idx
            ),
            None,
        )
        assert recovery_online is not None, (
            f"Expected 'online' after 'offline' in {avail_payloads}"
        )

    async def test_mark_available_from_device_handler(self) -> None:
        """ctx.mark_available() from an @app.device handler publishes 'online'.

        Also confirms telemetry/device handlers do NOT auto-recover: a
        successful iteration after mark_unavailable() without an
        explicit mark_available() call must NOT publish 'online'.
        """
        harness = AppHarness.create(name="testapp")
        marked_unavailable = asyncio.Event()
        iterated_without_recovery = asyncio.Event()
        marked_available = asyncio.Event()

        @harness.app.device("blind")
        async def blind(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.mark_unavailable()
            marked_unavailable.set()
            yield  # successful iteration -- must NOT auto-recover
            iterated_without_recovery.set()
            yield
            await ctx.mark_available()
            marked_available.set()
            while not ctx.shutdown_requested:
                await ctx.sleep(1)
                yield

        async def simulate() -> None:
            await marked_unavailable.wait()
            await iterated_without_recovery.wait()
            await marked_available.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        avail_payloads = [
            p for p, _, _ in harness.messages_for("testapp/blind/availability")
        ]
        assert "offline" in avail_payloads
        assert "online" in avail_payloads

        offline_idx = next(i for i, p in enumerate(avail_payloads) if p == "offline")
        # No auto-recovery: the successful yield between offline and the
        # explicit mark_available() call must not itself have published
        # 'online'. There should be exactly one 'online' after 'offline'
        # (the one from the explicit mark_available() call).
        online_after_offline = [
            i for i, p in enumerate(avail_payloads) if p == "online" and i > offline_idx
        ]
        assert len(online_after_offline) == 1, (
            f"Expected exactly one explicit recovery 'online', got {avail_payloads}"
        )

    async def test_non_matching_exception_publishes_error_not_offline(self) -> None:
        """Non-matching exception → error topic populated, no mid-run offline.

        When the exception type does not match unavailable_on, the
        framework should NOT call mark_unavailable().  The only
        observable effect is that the error topic receives a message.
        """

        class TransportError(Exception):
            pass

        class OtherError(Exception):
            pass

        harness = AppHarness.create(name="testapp")
        handler_called = asyncio.Event()

        @harness.app.command("sensor", unavailable_on=(TransportError,))
        async def handle_sensor(ctx: DeviceContext) -> None:
            handler_called.set()
            raise OtherError("unrelated error")

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await harness.inject_command("sensor", "")
            await handler_called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        # Non-matching exception must publish to the error topic
        harness.assert_published("testapp/sensor/error")

        # Availability sequence: only startup 'online' + shutdown 'offline'
        # (mark_unavailable was NOT called, so no mid-run offline)
        avail_payloads = [
            p for p, _, _ in harness.messages_for("testapp/sensor/availability")
        ]
        # The sequence should be ['online', 'offline'] with no extra offline
        # between them (which would indicate mark_unavailable was incorrectly called)
        online_count = avail_payloads.count("online")
        offline_count = avail_payloads.count("offline")
        assert online_count == 1, f"Expected 1 'online' but got {avail_payloads}"
        assert offline_count == 1, f"Expected 1 'offline' but got {avail_payloads}"
        assert avail_payloads[0] == "online"
        assert avail_payloads[-1] == "offline"

    async def test_mark_unavailable_called_from_handler(self) -> None:
        """Handler explicitly calling ctx.mark_unavailable() publishes 'offline'."""
        harness = AppHarness.create(name="testapp")
        handler_called = asyncio.Event()

        @harness.app.command("sensor")
        async def handle_sensor(ctx: DeviceContext) -> None:
            await ctx.mark_unavailable()
            handler_called.set()

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await harness.inject_command("sensor", "")
            await handler_called.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        avail_payloads = [
            p for p, _, _ in harness.messages_for("testapp/sensor/availability")
        ]
        assert "offline" in avail_payloads

    async def test_mark_unavailable_auto_recovers_after_success(self) -> None:
        """Handler-driven unavailability recovers on next successful invocation.

        First call: handler calls ctx.mark_unavailable() → offline.
        Second call: handler succeeds without marking → auto-recovery online.
        """
        harness = AppHarness.create(name="testapp")
        call_count = [0]
        first_done = asyncio.Event()
        second_done = asyncio.Event()

        @harness.app.command("sensor")
        async def handle_sensor(ctx: DeviceContext) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                await ctx.mark_unavailable()
                first_done.set()
            else:
                second_done.set()  # succeeds, triggers auto-recovery

        async def simulate() -> None:
            await asyncio.sleep(0.05)
            await harness.inject_command("sensor", "")
            await first_done.wait()
            await asyncio.sleep(0.02)
            await harness.inject_command("sensor", "")
            await second_done.wait()
            await asyncio.sleep(0.05)
            harness.trigger_shutdown()

        asyncio.create_task(simulate())
        await asyncio.wait_for(harness.run(), timeout=5.0)

        avail_payloads = [
            p for p, _, _ in harness.messages_for("testapp/sensor/availability")
        ]
        assert "offline" in avail_payloads
        assert "online" in avail_payloads

        offline_idx = next(i for i, p in enumerate(avail_payloads) if p == "offline")
        recovery_online = next(
            (
                i
                for i, p in enumerate(avail_payloads)
                if p == "online" and i > offline_idx
            ),  # noqa: E501
            None,
        )
        assert recovery_online is not None, (
            f"Expected 'online' after 'offline' in {avail_payloads}"
        )
