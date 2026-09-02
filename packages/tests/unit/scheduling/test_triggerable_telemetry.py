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
from cosalette._runners._telemetry_runner import _TriggerSlot
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
        """from_mqtt with empty payload: blank is the bare /set trigger → data={}.

        A blank payload is the documented "just re-run" trigger form.
        """
        # Act
        payload = TriggerPayload.from_mqtt("")

        # Assert
        assert payload.is_triggered is True
        assert payload.raw == ""
        assert payload.data == {}

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
        """from_mqtt with whitespace-only payload treats it as an empty JSON object.

        Whitespace-only is blank — treated as the bare /set trigger equivalent to
        sending "{}".  raw preserves the literal string; data is {}.
        """
        # Act
        payload = TriggerPayload.from_mqtt("   ")

        # Assert
        assert payload.is_triggered is True
        assert payload.raw == "   "
        assert payload.data == {}

    @pytest.mark.parametrize("variant", ["", "   ", "\n", "\t "])
    def test_from_mqtt_blank_variants_treated_as_empty_object(
        self, variant: str
    ) -> None:
        """Blank payload variants ("", whitespace) all yield data=={} (F-1)."""
        payload = TriggerPayload.from_mqtt(variant)
        assert payload.is_triggered is True
        assert payload.data == {}

    @pytest.mark.parametrize("scalar", ["0", "false", "null", '"text"'])
    def test_from_mqtt_scalar_payloads_not_treated_as_blank(self, scalar: str) -> None:
        """Only whitespace is blank: JSON scalars parse to non-dict → data=None.

        Guards the "blank means {}" contract against over-reach — a payload
        like "0" or "false" is a valid JSON scalar (not a dict, not blank),
        so data stays None while raw preserves the literal string.
        """
        payload = TriggerPayload.from_mqtt(scalar)
        assert payload.is_triggered is True
        assert payload.data is None
        assert payload.raw == scalar

    def test_from_mqtt_with_malformed_json(self) -> None:
        """from_mqtt with malformed JSON keeps raw but sets data=None."""
        # Act
        payload = TriggerPayload.from_mqtt('{"key": broken}')

        # Assert
        assert payload.is_triggered is True
        assert payload.raw == '{"key": broken}'
        assert payload.data is None

    @pytest.mark.parametrize(
        "deep_payload",
        [
            "[" * 50_000,  # deep array nesting within the 256 KiB inbound cap
            "{" * 5_000 + '"x": 1' + "}" * 5_000,  # deep object nesting
        ],
        ids=["arrays", "objects"],
    )
    def test_from_mqtt_deeply_nested_payload_does_not_raise(
        self, deep_payload: str
    ) -> None:
        """Deeply nested payloads surface as data=None, never RecursionError (F-DP9).

        Technique: Boundary Value Analysis (depth at the inbound size cap) +
        Error Guessing — F-DP9 regression guard: unstructured RecursionError
        would kill the telemetry worker (CWE-674) instead of degrading to
        "not valid JSON".

        The payload stays within the inbound size cap, so from_mqtt must
        handle it gracefully.
        """
        payload = TriggerPayload.from_mqtt(deep_payload)

        assert payload.is_triggered is True
        assert payload.raw == deep_payload
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
        assert app._telemetry[0].triggerable == "mqtt"

    def test_triggerable_defaults_to_none(self, app: App) -> None:
        """Registration without triggerable stores no trigger source."""

        # Act
        @app.telemetry("sensor", interval=10)
        async def sensor_handler() -> dict[str, object]:
            return {"value": 42}

        # Assert
        assert len(app._telemetry) == 1
        assert app._telemetry[0].triggerable is None

    def test_triggerable_root_device_raises(self, app: App) -> None:
        """triggerable=True on root device (name=None) raises ValueError."""
        # Act & Assert
        with pytest.raises(ValueError, match="triggerable="):

            @app.telemetry(interval=10, triggerable=True)
            async def root_handler() -> dict[str, object]:
                return {"value": 42}

    def test_triggerable_with_group_is_accepted(self, app: App) -> None:
        """triggerable=True combines with group= (ADR-067)."""

        # Act
        @app.telemetry("x", interval=10, triggerable=True, group="g")
        async def grouped_handler() -> dict[str, object]:
            return {"value": 42}

        # Assert
        assert app._telemetry[0].triggerable == "mqtt"
        assert app._telemetry[0].group == "g"

    def test_triggerable_with_is_root_raises_via_add_telemetry(self, app: App) -> None:
        """add_telemetry with triggerable=True on root device raises ValueError."""

        async def root_handler() -> dict[str, object]:
            return {"value": 42}

        # Act & Assert
        with pytest.raises(ValueError, match="root"):
            app.add_telemetry(
                "sensor", root_handler, interval=10, triggerable=True, is_root=True
            )

    def test_triggerable_with_callable_name_accepted(self, app: App) -> None:
        """triggerable=True with callable name= must not raise at registration time."""

        # Act — must not raise
        @app.telemetry(
            name=lambda s: {"dev-a": "a", "dev-b": "b"},
            interval=60,
            triggerable=True,
        )
        async def handler() -> dict[str, object]:
            return {}

        # Assert — one entry stored, name_spec set, flag preserved
        assert len(app._telemetry) == 1
        reg = app._telemetry[0]
        assert reg.triggerable == "mqtt"
        assert reg.name_spec is not None

    def test_triggerable_callable_name_flag_preserved_after_expansion(
        self, app: App
    ) -> None:
        """triggerable flag survives callable name= expansion."""
        from cosalette._settings import Settings
        from cosalette._wiring import _expand_telemetry_names

        @app.telemetry(
            name=lambda s: {"x": "cfg-x", "y": "cfg-y"},
            interval=30,
            triggerable=True,
        )
        async def handler() -> dict[str, object]:
            return {}

        settings = Settings()
        _expand_telemetry_names(app._telemetry, settings)

        assert len(app._telemetry) == 2
        names = {r.name for r in app._telemetry}
        assert names == {"x", "y"}
        assert all(r.triggerable == "mqtt" for r in app._telemetry)
        assert all(r.name_spec is None for r in app._telemetry)

    def test_triggerable_callable_name_with_group_expands(self, app: App) -> None:
        """triggerable=True + group= survives callable-name expansion.

        Technique: Error Guessing — expansion once carried its own copy of
        the group guard; verify every expanded entity now keeps both axes.
        """
        from cosalette._settings import Settings
        from cosalette._wiring import _expand_telemetry_names

        @app.telemetry(
            name=lambda s: {"dev-x": "cfg", "dev-y": "cfg"},
            interval=60,
            triggerable=True,
            group="my-group",
        )
        async def handler() -> dict[str, object]:
            return {}

        _expand_telemetry_names(app._telemetry, Settings())

        assert [(r.name, r.triggerable, r.group) for r in app._telemetry] == [
            ("dev-x", "mqtt", "my-group"),
            ("dev-y", "mqtt", "my-group"),
        ]


class TestTriggerConfig:
    """TriggerConfig.build() construction and filtering.

    Techniques:
    - Equivalence Partitioning: empty, non-triggerable-only, mixed, all-triggerable
    - Boundary Value Analysis: empty list (lower bound)
    """

    def test_build_empty_telemetry_produces_empty_slots(self) -> None:
        """build([]) creates a TriggerConfig with empty slots and empty telemetry."""
        from cosalette._wiring import TriggerConfig

        config = TriggerConfig.build([])

        assert config.slots == {}
        assert config.telemetry == []

    def test_build_non_triggerable_only_produces_empty_slots(self) -> None:
        """Registrations with no trigger source produce no slots."""
        from cosalette._registration import _TelemetryRegistration
        from cosalette._wiring import TriggerConfig

        async def _noop() -> dict[str, object]:
            return {}

        reg = _TelemetryRegistration(
            name="sensor",
            func=_noop,
            injection_plan=[],
            interval=60.0,
            triggerable=None,
        )
        config = TriggerConfig.build([reg])

        assert config.slots == {}
        assert len(config.telemetry) == 1

    def test_build_mixed_only_triggerable_entries_get_slots(
        self,
    ) -> None:
        """Only entries declaring a trigger source get slots; all go into telemetry."""
        from cosalette._registration import _TelemetryRegistration
        from cosalette._wiring import TriggerConfig

        async def _noop() -> dict[str, object]:
            return {}

        reg_on = _TelemetryRegistration(
            name="on",
            func=_noop,
            injection_plan=[],
            interval=60.0,
            triggerable="mqtt",
        )
        reg_off = _TelemetryRegistration(
            name="off",
            func=_noop,
            injection_plan=[],
            interval=60.0,
            triggerable=None,
        )
        config = TriggerConfig.build([reg_on, reg_off])

        assert set(config.slots.keys()) == {"on"}
        assert len(config.telemetry) == 2

    def test_build_takes_snapshot_not_live_reference(self) -> None:
        """Mutating the source list after build() does not affect telemetry."""
        from cosalette._registration import _TelemetryRegistration
        from cosalette._wiring import TriggerConfig

        async def _noop() -> dict[str, object]:
            return {}

        reg = _TelemetryRegistration(
            name="sensor",
            func=_noop,
            injection_plan=[],
            interval=60.0,
            triggerable=None,
        )
        source: list[_TelemetryRegistration] = [reg]
        config = TriggerConfig.build(source)

        source.clear()  # mutate after build

        assert len(config.telemetry) == 1, (
            "TriggerConfig should hold a snapshot, not a live reference"
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

    async def test_trigger_payload_injected_on_blank_trigger(self) -> None:
        """Blank /set publish: TriggerPayload has is_triggered=True, data={}, raw="".

        A bare /set with empty body is the documented "just re-run" trigger.
        """
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
            # Deliver blank trigger (bare /set publish — documented "just re-run" form)
            await harness.mqtt.deliver("testapp/sensor/set", "")
            await trigger_received.wait()
            harness.trigger_shutdown()

        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Blank payload → data == {} (not None), raw preserves ""
        assert received_payload is not None
        assert received_payload.is_triggered is True
        assert received_payload.data == {}
        assert received_payload.raw == ""

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

    async def test_refreshable_behaves_identically_to_triggerable(self) -> None:
        """triggerable=True fires immediately on MQTT /set message."""
        harness = AppHarness.create()
        call_count = 0
        received_payload: TriggerPayload | None = None
        trigger_received = asyncio.Event()

        @harness.app.telemetry("sensor", interval=3600, triggerable=True)
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            nonlocal call_count, received_payload
            call_count += 1
            if trigger.is_triggered:
                received_payload = trigger
                trigger_received.set()
            return {"value": call_count}

        async def _simulate() -> None:
            # Wait for first scheduled publish
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            # Deliver trigger with JSON payload to /set topic
            await harness.mqtt.deliver("testapp/sensor/set", '{"mode": "refresh"}')
            await trigger_received.wait()
            harness.trigger_shutdown()

        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Verify triggerable=True fires immediate execution on MQTT /set message
        assert call_count >= 2, "Expected at least scheduled + triggered execution"
        messages = harness.mqtt.get_messages_for("testapp/sensor/state")
        assert len(messages) >= 2, "Expected at least 2 published messages"

        # Verify trigger payload was injected correctly
        assert received_payload is not None
        assert received_payload.is_triggered is True
        assert received_payload.data == {"mode": "refresh"}
        assert received_payload.raw == '{"mode": "refresh"}'
