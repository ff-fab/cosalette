"""Unit tests for triggerable telemetry feature — trigger payloads and execution.

Test Techniques Used:
- Specification-based Testing: Verifying TriggerPayload constructor contracts
- Error Guessing: Anticipating registration validation failures
- State Transition Testing: Verifying trigger slot event transitions
- Integration Testing: End-to-end triggerable telemetry execution via AppHarness

Common patterns:
- TriggerPayload dataclass behavior and JSON parsing
- Registration validation for triggerable telemetry
- _TriggerSlot arm/consume/coalescing behavior
- MQTT-triggered telemetry execution with payload injection
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from cosalette import App, TriggerPayload
from cosalette._telemetry_runner import _TriggerSlot
from cosalette.testing import AppHarness

pytestmark = pytest.mark.unit


# =============================================================================
# Tests
# =============================================================================


class TestTriggerPayload:
    """TriggerPayload dataclass behavior.

    Technique: Specification-based — verify constructor contracts,
    factory methods, and accessor behavior.
    """

    def test_scheduled_returns_non_triggered_instance(self) -> None:
        """TriggerPayload.scheduled() returns instance with is_triggered=False."""
        # Act
        payload = TriggerPayload.scheduled()

        # Assert
        assert payload.is_triggered is False
        assert payload.raw is None
        assert payload.data is None

    def test_scheduled_returns_same_instance(self) -> None:
        """TriggerPayload.scheduled() returns singleton instance."""
        # Act
        payload1 = TriggerPayload.scheduled()
        payload2 = TriggerPayload.scheduled()

        # Assert
        assert payload1 is payload2

    def test_from_mqtt_with_json_payload(self) -> None:
        """from_mqtt with JSON payload sets is_triggered=True and parses data."""
        # Arrange
        json_payload = '{"days": 3, "mode": "fast"}'

        # Act
        payload = TriggerPayload.from_mqtt(json_payload)

        # Assert
        assert payload.is_triggered is True
        assert payload.raw == json_payload
        assert payload.data == {"days": 3, "mode": "fast"}

    def test_from_mqtt_with_non_json_payload(self) -> None:
        """from_mqtt with non-JSON payload sets raw but data=None."""
        # Arrange
        text_payload = "REFRESH"

        # Act
        payload = TriggerPayload.from_mqtt(text_payload)

        # Assert
        assert payload.is_triggered is True
        assert payload.raw == "REFRESH"
        assert payload.data is None

    def test_from_mqtt_with_empty_payload(self) -> None:
        """from_mqtt with empty payload sets is_triggered=True, raw=None."""
        # Act
        payload = TriggerPayload.from_mqtt("")

        # Assert
        assert payload.is_triggered is True
        assert payload.raw is None
        assert payload.data is None

    def test_from_mqtt_with_json_array_payload(self) -> None:
        """from_mqtt with JSON array sets raw but data=None (only dicts accepted)."""
        # Arrange
        array_payload = "[1, 2, 3]"

        # Act
        payload = TriggerPayload.from_mqtt(array_payload)

        # Assert
        assert payload.is_triggered is True
        assert payload.raw == "[1, 2, 3]"
        assert payload.data is None

    def test_get_returns_value_from_data(self) -> None:
        """get() returns value from parsed data dict."""
        # Arrange
        payload = TriggerPayload(is_triggered=True, data={"days": 3, "mode": "slow"})

        # Act & Assert
        assert payload.get("days") == 3
        assert payload.get("mode") == "slow"

    def test_get_returns_default_when_data_is_none(self) -> None:
        """get() returns default when data is None (scheduled run)."""
        # Arrange
        payload = TriggerPayload.scheduled()

        # Act & Assert
        assert payload.get("days", 7) == 7
        assert payload.get("mode", "normal") == "normal"

    def test_get_returns_default_for_missing_key(self) -> None:
        """get() returns default for missing key in data dict."""
        # Arrange
        payload = TriggerPayload(is_triggered=True, data={"x": 1})

        # Act & Assert
        assert payload.get("y", 99) == 99
        assert payload.get("z") is None

    def test_from_mqtt_with_whitespace_only_payload(self) -> None:
        """from_mqtt with whitespace-only payload treats it as non-empty raw."""
        # Act
        payload = TriggerPayload.from_mqtt("   ")

        # Assert
        assert payload.is_triggered is True
        assert payload.raw == "   "
        assert payload.data is None

    def test_from_mqtt_with_malformed_json(self) -> None:
        """from_mqtt with malformed JSON keeps raw but sets data=None."""
        # Act
        payload = TriggerPayload.from_mqtt('{"key": broken}')

        # Assert
        assert payload.is_triggered is True
        assert payload.raw == '{"key": broken}'
        assert payload.data is None

    def test_from_mqtt_with_large_payload(self) -> None:
        """from_mqtt handles large payloads (>1 KB) without truncation."""
        # Arrange
        large_payload = '{"data": "' + "x" * 1200 + '"}'

        # Act
        payload = TriggerPayload.from_mqtt(large_payload)

        # Assert
        assert payload.is_triggered is True
        assert payload.data is not None
        assert len(payload.data["data"]) == 1200

    def test_frozen_immutability(self) -> None:
        """TriggerPayload is frozen and cannot be mutated."""
        # Arrange
        payload = TriggerPayload(is_triggered=True)

        # Act & Assert
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            payload.is_triggered = False  # ty: ignore[invalid-assignment]


class TestTriggerableRegistration:
    """Registration validation for triggerable telemetry.

    Technique: Error Guessing — verify invalid combinations are rejected.
    """

    def test_triggerable_stored_on_registration(self, app: App) -> None:
        """Registration with triggerable=True stores flag correctly."""

        # Act
        @app.telemetry("sensor", interval=10, triggerable=True)
        async def sensor_handler() -> dict[str, object]:
            return {"value": 42}

        # Assert
        assert len(app._telemetry) == 1
        assert app._telemetry[0].triggerable is True

    def test_triggerable_defaults_to_false(self, app: App) -> None:
        """Registration without triggerable defaults to False."""

        # Act
        @app.telemetry("sensor", interval=10)
        async def sensor_handler() -> dict[str, object]:
            return {"value": 42}

        # Assert
        assert len(app._telemetry) == 1
        assert app._telemetry[0].triggerable is False

    def test_triggerable_root_device_raises(self, app: App) -> None:
        """triggerable=True on root device (name=None) raises ValueError."""
        # Act & Assert
        with pytest.raises(ValueError, match="requires a named device"):

            @app.telemetry(interval=10, triggerable=True)
            async def root_handler() -> dict[str, object]:
                return {"value": 42}

    def test_triggerable_with_group_raises(self, app: App) -> None:
        """triggerable=True with group= raises ValueError."""
        # Act & Assert
        with pytest.raises(ValueError, match="cannot be combined"):

            @app.telemetry("x", interval=10, triggerable=True, group="g")
            async def grouped_handler() -> dict[str, object]:
                return {"value": 42}

    def test_triggerable_with_is_root_raises_via_add_telemetry(self, app: App) -> None:
        """add_telemetry with triggerable=True on root device raises ValueError."""

        async def root_handler() -> dict[str, object]:
            return {"value": 42}

        # Act & Assert
        with pytest.raises(ValueError, match="root"):
            app.add_telemetry(
                "sensor", root_handler, interval=10, triggerable=True, is_root=True
            )


class TestTriggerSlot:
    """_TriggerSlot arm/consume/coalescing behavior.

    Technique: State Transition Testing — verify event set/clear
    transitions and payload replacement.
    """

    @pytest.fixture
    def trigger_slot(self) -> _TriggerSlot:
        """Fresh _TriggerSlot for each test."""
        return _TriggerSlot(event=asyncio.Event())

    def test_arm_sets_event(self, trigger_slot: _TriggerSlot) -> None:
        """arm() sets the event to signal waiting consumers."""
        # Act
        trigger_slot.arm("test-payload")

        # Assert
        assert trigger_slot.event.is_set() is True

    def test_consume_clears_event_and_returns_payload(
        self, trigger_slot: _TriggerSlot
    ) -> None:
        """consume() clears event and returns a TriggerPayload from the raw string."""
        # Arrange
        trigger_slot.arm("test-payload")

        # Act
        result = trigger_slot.consume()

        # Assert
        assert trigger_slot.event.is_set() is False
        assert result.is_triggered is True
        assert result.raw == "test-payload"

    def test_arm_twice_coalesces(self, trigger_slot: _TriggerSlot) -> None:
        """arm() called twice replaces raw payload (coalescing behavior)."""
        # Act
        trigger_slot.arm("first")
        trigger_slot.arm("second")
        result = trigger_slot.consume()

        # Assert
        assert result.raw == "second"

    def test_arm_burst_coalesces(self, trigger_slot: _TriggerSlot) -> None:
        """arm() called 5 times rapidly retains only the last raw payload."""
        # Act
        for i in range(1, 6):
            trigger_slot.arm(f"payload-{i}")
        result = trigger_slot.consume()

        # Assert
        assert result.raw == "payload-5"

    def test_consume_returns_scheduled_after_clear(
        self, trigger_slot: _TriggerSlot
    ) -> None:
        """After consume(), the slot's raw is reset to None."""
        # Arrange
        trigger_slot.arm("test")

        # Act
        trigger_slot.consume()

        # Assert
        assert trigger_slot.raw is None


class TestTriggerableExecution:
    """End-to-end triggerable telemetry execution.

    Technique: Integration Testing — verify that MQTT messages on
    {prefix}/{device}/set trigger immediate handler execution and
    that TriggerPayload is correctly injected.
    """

    async def test_trigger_fires_handler_immediately(self) -> None:
        """MQTT trigger on /set topic fires handler immediately."""
        harness = AppHarness.create()
        call_count = 0
        trigger_received = asyncio.Event()

        @harness.app.telemetry("sensor", interval=3600, triggerable=True)
        async def sensor() -> dict[str, object]:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                trigger_received.set()
            return {"value": call_count}

        async def _simulate() -> None:
            # Wait for first scheduled publish
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            # Deliver trigger
            await harness.mqtt.deliver("testapp/sensor/set", "")
            await trigger_received.wait()
            harness.trigger_shutdown()

        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # At least 2 calls: 1 scheduled + 1 triggered
        assert call_count >= 2
        messages = harness.mqtt.get_messages_for("testapp/sensor/state")
        assert len(messages) >= 2

    async def test_trigger_payload_injected_on_trigger(self) -> None:
        """TriggerPayload is injected with correct data on MQTT trigger."""
        harness = AppHarness.create()
        received_payload: TriggerPayload | None = None
        trigger_received = asyncio.Event()

        @harness.app.telemetry("sensor", interval=3600, triggerable=True)
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            nonlocal received_payload
            if trigger.is_triggered:
                received_payload = trigger
                trigger_received.set()
            return {"value": 42}

        async def _simulate() -> None:
            # Wait for first scheduled publish
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            # Deliver trigger with JSON payload
            await harness.mqtt.deliver("testapp/sensor/set", '{"days": 3}')
            await trigger_received.wait()
            harness.trigger_shutdown()

        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Verify trigger payload was injected correctly
        assert received_payload is not None
        assert received_payload.is_triggered is True
        assert received_payload.data == {"days": 3}
        assert received_payload.raw == '{"days": 3}'

    async def test_trigger_payload_is_scheduled_on_normal_cycle(self) -> None:
        """TriggerPayload shows is_triggered=False on scheduled runs."""
        harness = AppHarness.create()
        received_payload: TriggerPayload | None = None
        scheduled_received = asyncio.Event()

        @harness.app.telemetry("sensor", interval=0.01, triggerable=True)
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            nonlocal received_payload
            if not trigger.is_triggered:
                received_payload = trigger
                scheduled_received.set()
            return {"value": 42}

        async def _simulate() -> None:
            await scheduled_received.wait()
            harness.trigger_shutdown()

        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Verify scheduled payload
        assert received_payload is not None
        assert received_payload.is_triggered is False
        assert received_payload.raw is None
        assert received_payload.data is None

    async def test_non_triggerable_telemetry_ignores_set_messages(self) -> None:
        """Non-triggerable telemetry doesn't respond to /set messages."""
        harness = AppHarness.create()
        call_timestamps: list[float] = []

        @harness.app.telemetry("sensor", interval=1.0, triggerable=False)
        async def sensor() -> dict[str, object]:
            call_timestamps.append(asyncio.get_running_loop().time())
            return {"value": len(call_timestamps)}

        async def _simulate() -> None:
            # Wait for first call
            while not call_timestamps:
                await asyncio.sleep(0.01)

            start_time = call_timestamps[0]

            # Try to trigger - should have no immediate effect
            await harness.mqtt.deliver("testapp/sensor/set", "")

            # Wait a bit and check that no immediate call happened
            await asyncio.sleep(0.1)
            calls_after_trigger = len(call_timestamps)

            harness.trigger_shutdown()

            # Verify trigger didn't cause immediate execution
            # (there might be scheduled calls but no immediate trigger response)
            immediate_calls = [
                t
                for t in call_timestamps[1:]
                if t - start_time < 0.2  # Within 200ms of trigger
            ]
            assert len(immediate_calls) == 0, (
                f"Trigger caused immediate calls: {immediate_calls}"
            )

        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)
