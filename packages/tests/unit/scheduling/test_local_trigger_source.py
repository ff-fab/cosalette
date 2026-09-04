"""Unit tests for the local (in-process) trigger source — ADR-064.

Test Techniques Used:
- Equivalence Partitioning: the ``triggerable=`` input domain
  (``True``/``False``/``"mqtt"``/``"local"``/``"both"``/invalid)
- Decision Table: trigger source x arming path (MQTT ``/set`` vs
  :class:`EntityNotifier`) -> whether the entity wakes
- State Transition Testing: ``_TriggerSlot`` arm/arm_local/consume
- Error Guessing: arming before the Phase-2 bind, unknown entity names,
  arming from a non-event-loop thread
- Integration Testing: an end-to-end local wake running through the
  identical publish cycle used by a scheduled tick

Common patterns:
- ``normalize_trigger_source`` maps the public spec onto the internal
  ``TriggerSource`` used by every downstream branch
- ``EntityNotifier`` is a stable Phase-1 handle, late-bound in Phase 2
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

import cosalette
from cosalette import (
    App,
    DeviceTrigger,
    EntityNotifier,
    NotifierNotReadyError,
    TriggerPayload,
    UnknownEntityError,
)
from cosalette._registration import _TelemetryRegistration
from cosalette._runners._telemetry_types import _TriggerSlot
from cosalette._runners._trigger import (
    arms_locally,
    arms_via_mqtt,
    normalize_trigger_source,
)
from cosalette._wiring import TriggerConfig
from cosalette._wiring._discovery import DiscoveryConfig, build_discovery_payloads
from cosalette.schema import consumer
from cosalette.testing import AppHarness
from tests.fixtures.notifier import _NotifierHolder

pytestmark = pytest.mark.unit


# =============================================================================
# Helpers
# =============================================================================


async def _noop() -> dict[str, object]:
    return {}


def _telemetry_reg(
    name: str,
    triggerable: cosalette.TriggerSource | None,
    *,
    is_root: bool = False,
) -> _TelemetryRegistration:
    """Build a minimal telemetry registration for wiring-level tests."""
    return _TelemetryRegistration(
        name=name,
        func=_noop,
        injection_plan=[],
        interval=60.0,
        triggerable=triggerable,
        is_root=is_root,
    )


# =============================================================================
# Tests
# =============================================================================


class TestNormalizeTriggerSource:
    """Normalisation of the public ``triggerable=`` spec.

    Technique: Equivalence Partitioning — one representative per class
    of accepted value, plus the invalid partition.
    """

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            (True, "mqtt"),
            (False, None),
            ("mqtt", "mqtt"),
            ("local", "local"),
            ("both", "both"),
        ],
    )
    def test_normalize_maps_spec_to_source(
        self, spec: cosalette.TriggerableSpec, expected: str | None
    ) -> None:
        """Every accepted spec normalises to a source (or None)."""
        # Act
        result = normalize_trigger_source(spec)

        # Assert
        assert result == expected

    @pytest.mark.parametrize("spec", ["MQTT", "Local", "always", "", "none"])
    def test_normalize_rejects_unknown_source(self, spec: str) -> None:
        """An unrecognised source string raises ValueError naming the options.

        Technique: Error Guessing — near-miss spellings are the likely typo.
        """
        # Act & Assert
        with pytest.raises(ValueError, match="not a valid trigger source"):
            normalize_trigger_source(spec)  # ty: ignore[invalid-argument-type]

    @pytest.mark.parametrize(
        ("source", "via_mqtt", "locally"),
        [
            (None, False, False),
            ("mqtt", True, False),
            ("local", False, True),
            ("both", True, True),
        ],
    )
    def test_arming_path_predicates(
        self, source: cosalette.TriggerSource | None, via_mqtt: bool, locally: bool
    ) -> None:
        """Decision table: source -> which arming paths are enabled."""
        # Act & Assert
        assert arms_via_mqtt(source) is via_mqtt
        assert arms_locally(source) is locally


class TestTriggerPayloadSource:
    """``TriggerPayload.source`` discriminates the three run kinds.

    Technique: Specification-based — verify factory contracts.
    """

    def test_scheduled_payload_reports_scheduled_source(self) -> None:
        """A scheduled tick reports source="scheduled"."""
        # Act
        payload = TriggerPayload.scheduled()

        # Assert
        assert payload.source == "scheduled"
        assert payload.is_triggered is False

    def test_local_payload_reports_local_source(self) -> None:
        """local() is triggered, carries no payload, reports source="local"."""
        # Act
        payload = TriggerPayload.local()

        # Assert
        assert payload.source == "local"
        assert payload.is_triggered is True
        assert payload.raw is None
        assert payload.data is None

    def test_local_returns_same_instance(self) -> None:
        """local() returns a singleton — it carries no per-call state."""
        # Act & Assert
        assert TriggerPayload.local() is TriggerPayload.local()

    def test_from_mqtt_payload_reports_mqtt_source(self) -> None:
        """An inbound /set message reports source="mqtt"."""
        # Act
        payload = TriggerPayload.from_mqtt('{"days": 3}')

        # Assert
        assert payload.source == "mqtt"
        assert payload.data == {"days": 3}


class TestTriggerSlotLocalArming:
    """``_TriggerSlot`` transitions for local arming.

    Technique: State Transition Testing — idle -> armed -> consumed,
    including the mixed-source coalescing transitions.
    """

    @pytest.fixture
    def slot(self) -> _TriggerSlot:
        """Fresh _TriggerSlot for each test."""
        return _TriggerSlot(event=asyncio.Event())

    def test_arm_local_sets_event(self, slot: _TriggerSlot) -> None:
        """arm_local() signals the waiting runner."""
        # Act
        slot.arm_local()

        # Assert
        assert slot.event.is_set() is True
        assert slot.raw is None

    def test_consume_after_arm_local_returns_local_payload(
        self, slot: _TriggerSlot
    ) -> None:
        """consume() after a local arm yields a local TriggerPayload."""
        # Arrange
        slot.arm_local()

        # Act
        payload = slot.consume()

        # Assert
        assert payload.source == "local"
        assert payload.is_triggered is True
        assert slot.event.is_set() is False
        assert slot.source == "scheduled"

    def test_repeated_local_arms_coalesce(self, slot: _TriggerSlot) -> None:
        """Five local arms collapse into a single pending run."""
        # Act
        for _ in range(5):
            slot.arm_local()
        payload = slot.consume()

        # Assert
        assert payload.source == "local"
        assert slot.event.is_set() is False

    def test_local_arm_after_mqtt_arm_wins(self, slot: _TriggerSlot) -> None:
        """The most recent arm decides the reported source (local last)."""
        # Arrange
        slot.arm("REFRESH")

        # Act
        slot.arm_local()
        payload = slot.consume()

        # Assert
        assert payload.source == "local"
        assert payload.raw is None

    def test_mqtt_arm_after_local_arm_wins(self, slot: _TriggerSlot) -> None:
        """The most recent arm decides the reported source (mqtt last)."""
        # Arrange
        slot.arm_local()

        # Act
        slot.arm("REFRESH")
        payload = slot.consume()

        # Assert
        assert payload.source == "mqtt"
        assert payload.raw == "REFRESH"


class TestTriggerSourceRegistration:
    """Registration-time validation of the widened ``triggerable=``.

    Techniques:
    - Equivalence Partitioning: accepted sources on named/root devices
    - Error Guessing: the combinations the framework must still reject
    """

    @pytest.fixture
    def app(self) -> App:
        """Fresh App for each test."""
        return App(name="testapp", version="1.0.0")

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [(True, "mqtt"), ("mqtt", "mqtt"), ("local", "local"), ("both", "both")],
    )
    def test_source_stored_normalised_on_registration(
        self, app: App, spec: cosalette.TriggerableSpec, expected: str
    ) -> None:
        """The decorator stores the normalised source, not the raw spec."""

        # Act
        @app.telemetry("sensor", interval=10, triggerable=spec)
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        # Assert
        assert app._telemetry[0].triggerable == expected

    def test_local_source_allowed_on_root_device(self, app: App) -> None:
        """triggerable="local" needs no topic segment, so root is allowed."""

        # Act
        @app.telemetry(interval=10, triggerable="local")
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        # Assert
        assert app._telemetry[0].triggerable == "local"
        assert app._telemetry[0].is_root is True

    @pytest.mark.parametrize("spec", [True, "mqtt", "both"])
    def test_mqtt_source_still_rejected_on_root_device(
        self, app: App, spec: cosalette.TriggerableSpec
    ) -> None:
        """Any MQTT-arming source on a root device still raises."""
        # Act & Assert
        with pytest.raises(ValueError, match="root"):

            @app.telemetry(interval=10, triggerable=spec)
            async def sensor() -> dict[str, object]:
                return {"value": 1}

    @pytest.mark.parametrize("trigger_source", [True, "mqtt", "both"])
    def test_add_telemetry_rejects_mqtt_source_on_root(
        self, app: App, trigger_source: cosalette.TriggerableSpec
    ) -> None:
        """The imperative path enforces the same root guard for all MQTT sources."""

        # Arrange
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        # Act & Assert
        with pytest.raises(ValueError, match="root"):
            app.add_telemetry(
                "sensor", sensor, interval=10, triggerable=trigger_source, is_root=True
            )

    def test_add_telemetry_allows_local_source_on_root(self, app: App) -> None:
        """add_telemetry accepts triggerable="local" on a root device."""

        # Arrange
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        # Act
        app.add_telemetry(
            "sensor", sensor, interval=10, triggerable="local", is_root=True
        )

        # Assert
        assert app._telemetry[0].triggerable == "local"

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [("local", "local"), ("both", "both"), ("mqtt", "mqtt"), (True, "mqtt")],
    )
    def test_group_accepted_for_every_source(
        self, app: App, spec: cosalette.TriggerableSpec, expected: str
    ) -> None:
        """triggerable= combines with group= for every source (ADR-067)."""

        # Act
        @app.telemetry("sensor", interval=10, triggerable=spec, group="g")
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        # Assert
        assert app._telemetry[0].triggerable == expected
        assert app._telemetry[0].group == "g"

    def test_invalid_source_rejected_at_registration(self, app: App) -> None:
        """An unknown source string fails fast at registration time."""
        # Act & Assert
        with pytest.raises(ValueError, match="not a valid trigger source"):

            @app.telemetry(
                "sensor",
                interval=10,
                triggerable="udp",  # ty: ignore[invalid-argument-type]
            )
            async def sensor() -> dict[str, object]:
                return {"value": 1}


class TestTriggerConfigLocalSlots:
    """``TriggerConfig`` slot construction and local-slot filtering.

    Technique: Decision Table — source -> slot exists / notifiable.
    """

    def test_every_source_gets_a_slot(self) -> None:
        """All trigger sources get a slot; non-triggerable entries do not."""
        # Arrange
        regs = [
            _telemetry_reg("m", "mqtt"),
            _telemetry_reg("l", "local"),
            _telemetry_reg("b", "both"),
            _telemetry_reg("off", None),
        ]

        # Act
        config = TriggerConfig.build(regs)

        # Assert
        assert set(config.slots) == {"m", "l", "b"}

    def test_local_slots_excludes_mqtt_only_entities(self) -> None:
        """Only local/both entities may be armed by an EntityNotifier."""
        # Arrange
        regs = [
            _telemetry_reg("m", "mqtt"),
            _telemetry_reg("l", "local"),
            _telemetry_reg("b", "both"),
            _telemetry_reg("off", None),
        ]
        config = TriggerConfig.build(regs)

        # Act
        local_slots = config.local_slots()

        # Assert
        assert set(local_slots) == {"l", "b"}
        assert local_slots["l"] is config.slots["l"]

    def test_local_slots_are_the_same_objects_the_runner_waits_on(self) -> None:
        """Arming a local slot sets the event the telemetry runner races."""
        # Arrange
        config = TriggerConfig.build([_telemetry_reg("l", "local")])

        # Act
        config.local_slots()["l"].arm_local()

        # Assert
        assert config.slots["l"].event.is_set() is True


class TestEntityNotifierContract:
    """``EntityNotifier`` arming contract.

    Techniques:
    - Error Guessing: unbound handle, unknown name, closed loop
    - State Transition Testing: unbound -> bound -> armed
    """

    def test_unbound_notifier_raises_not_ready(self) -> None:
        """Arming before the Phase-2 bind raises NotifierNotReadyError."""
        # Arrange
        notifier = EntityNotifier()

        # Act & Assert
        with pytest.raises(NotifierNotReadyError, match="before the framework bound"):
            notifier("sensor")

    def test_unbound_notifier_reports_no_entities(self) -> None:
        """An unbound notifier exposes an empty entity set, not an error."""
        # Act & Assert
        assert EntityNotifier().entities == frozenset()

    async def test_bound_notifier_reports_its_entities(self) -> None:
        """entities lists the expanded names the notifier can wake."""
        # Arrange
        notifier = EntityNotifier()
        notifier._bind({"a": _TriggerSlot(event=asyncio.Event())})

        # Act & Assert
        assert notifier.entities == frozenset({"a"})

    async def test_unknown_entity_raises_named_error(self) -> None:
        """An unknown name raises UnknownEntityError, never a silent no-op."""
        # Arrange
        notifier = EntityNotifier()
        notifier._bind({"known": _TriggerSlot(event=asyncio.Event())})

        # Act & Assert
        with pytest.raises(UnknownEntityError, match="locally-triggerable"):
            notifier("typo")

    async def test_arm_on_loop_thread_is_immediate(self) -> None:
        """Called from the loop thread, the slot is armed inline."""
        # Arrange
        slot = _TriggerSlot(event=asyncio.Event())
        notifier = EntityNotifier()
        notifier._bind({"sensor": slot})

        # Act
        notifier("sensor")

        # Assert — no await needed, the arm already happened
        assert slot.event.is_set() is True
        assert slot.source == "local"

    async def test_arm_from_non_loop_thread_is_marshalled(self) -> None:
        """A push callback on another OS thread still arms the slot.

        Technique: Error Guessing — asyncio.Event is not thread-safe, so
        the arm must be marshalled with call_soon_threadsafe (ADR-042).
        """
        # Arrange
        slot = _TriggerSlot(event=asyncio.Event())
        notifier = EntityNotifier()
        notifier._bind({"sensor": slot})
        returned = threading.Event()

        def _push_callback() -> None:
            notifier("sensor")
            returned.set()

        # Act
        thread = threading.Thread(target=_push_callback)
        thread.start()
        await asyncio.wait_for(slot.event.wait(), timeout=2.0)
        thread.join(timeout=2.0)

        # Assert
        assert returned.is_set() is True
        assert slot.consume().source == "local"

    async def test_off_thread_local_arm_overrides_pending_mqtt_arm(self) -> None:
        """Last writer wins even when a local wake crosses from another thread.

        Technique: Error Guessing — a pending MQTT arm must not suppress a
        later local wake arriving through call_soon_threadsafe.
        """
        # Arrange
        slot = _TriggerSlot(event=asyncio.Event())
        notifier = EntityNotifier()
        notifier._bind({"sensor": slot})
        returned = threading.Event()
        slot.arm("REFRESH")

        def _push_callback() -> None:
            notifier("sensor")
            returned.set()

        # Act
        thread = threading.Thread(target=_push_callback)
        thread.start()
        await asyncio.to_thread(thread.join, 2.0)
        await asyncio.sleep(0)

        # Assert
        assert returned.is_set() is True
        payload = slot.consume()
        assert payload.source == "local"
        assert payload.raw is None

    async def test_unknown_entity_raises_in_the_calling_thread(self) -> None:
        """Name validation happens before marshalling, so bad names surface."""
        # Arrange
        notifier = EntityNotifier()
        notifier._bind({"known": _TriggerSlot(event=asyncio.Event())})
        captured: list[BaseException] = []

        def _push_callback() -> None:
            try:
                notifier("typo")
            except UnknownEntityError as exc:  # pragma: no branch
                captured.append(exc)

        # Act
        thread = threading.Thread(target=_push_callback)
        thread.start()
        await asyncio.to_thread(thread.join, 2.0)

        # Assert
        assert len(captured) == 1
        assert isinstance(captured[0], UnknownEntityError)


class _FakeStateForFix18:
    """Module-level state type for Fix-18 regression test."""


class TestEnterStateFactoriesContract:
    """Regression tests for enter_state_factories edge-cases."""

    @pytest.mark.asyncio
    async def test_enter_state_factories_raises_when_notifier_not_provided(
        self,
    ) -> None:
        """Raises RuntimeError when factory needs notifier but none was provided."""
        from cosalette._persistence._state import build_state_registration
        from cosalette._settings import Settings
        from cosalette._wiring._infra import enter_state_factories

        def factory_needing_notifier(notify: EntityNotifier) -> _FakeStateForFix18:
            return _FakeStateForFix18()

        reg = build_state_registration(factory_needing_notifier, set())

        with pytest.raises(RuntimeError, match="EntityNotifier"):
            async with enter_state_factories([reg], Settings(), notifier=None):
                pass


class TestValidateTriggerableDirectCalls:
    """Direct tests of validate_triggerable for root device and is_root combos."""

    def test_validate_triggerable_mqtt_rejects_none_name(self) -> None:
        """validate_triggerable raises for MQTT source when name is None."""
        from cosalette._app._telemetry_validators import validate_triggerable

        with pytest.raises(ValueError, match="named device"):
            validate_triggerable("mqtt", None)

    def test_validate_triggerable_both_rejects_none_name(self) -> None:
        """validate_triggerable raises for 'both' source when name is None."""
        from cosalette._app._telemetry_validators import validate_triggerable

        with pytest.raises(ValueError, match="named device"):
            validate_triggerable("both", None)

    def test_validate_triggerable_local_allows_none_name(self) -> None:
        """validate_triggerable allows 'local' when name is None (no MQTT topic)."""
        from cosalette._app._telemetry_validators import validate_triggerable

        result = validate_triggerable("local", None)
        assert result == "local"

    @pytest.mark.parametrize(
        ("source", "is_root", "expect_error"),
        [
            ("local", True, False),
            ("both", True, True),
            ("mqtt", True, True),
            ("local", False, False),
        ],
    )
    def test_validate_triggerable_is_root_combos(
        self, source: cosalette.TriggerSource, is_root: bool, expect_error: bool
    ) -> None:
        """is_root=True blocks MQTT sources but allows local."""
        from cosalette._app._telemetry_validators import validate_triggerable

        if expect_error:
            with pytest.raises(ValueError):
                validate_triggerable(source, None, is_root=is_root)
        else:
            result = validate_triggerable(source, None, is_root=is_root)
            assert result == source


class TestLocalTriggerExecution:
    """End-to-end local triggering through the running app.

    Technique: Integration Testing — a local wake must reach the handler
    through the same publish cycle a scheduled tick uses, and must not
    add or remove any MQTT subscription.
    """

    async def test_local_source_subscribes_no_set_topic(self) -> None:
        """triggerable="local" arms in-process only — no /set subscription."""
        # Arrange
        harness = AppHarness.create()

        @harness.app.telemetry("sensor", interval=3600, triggerable="local")
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert "testapp/sensor/set" not in harness.mqtt.subscriptions

    async def test_mqtt_source_still_subscribes_set_topic(self) -> None:
        """triggerable="mqtt" keeps the ADR-036 subscription unchanged."""
        # Arrange
        harness = AppHarness.create()

        @harness.app.telemetry("sensor", interval=3600, triggerable="mqtt")
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        harness.assert_subscribed("testapp/sensor/set")

    async def test_both_source_subscribes_set_topic(self) -> None:
        """triggerable="both" keeps the MQTT arming path as well."""
        # Arrange
        harness = AppHarness.create()

        @harness.app.telemetry("sensor", interval=3600, triggerable="both")
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        harness.assert_subscribed("testapp/sensor/set")

    async def test_local_wake_runs_the_normal_publish_cycle(self) -> None:
        """A notifier call publishes state through the standard cycle.

        The entity uses publish=OnChange(): the woken run's payload is
        published exactly once and identical scheduled payloads stay
        suppressed, which proves the wake reused the ordinary publish
        path rather than a second, strategy-bypassing one.
        """
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        woken = asyncio.Event()

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.telemetry(
            "sensor",
            interval=3600,
            triggerable="local",
            publish=cosalette.OnChange(),
        )
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            if trigger.source == "local":
                woken.set()
                return {"value": "woken"}
            return {"value": "tick"}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            holder[0].notify("sensor")
            await asyncio.wait_for(woken.wait(), timeout=5.0)
            while len(harness.mqtt.get_messages_for("testapp/sensor/state")) < 2:
                await asyncio.sleep(0.01)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        payloads = [m[0] for m in harness.mqtt.get_messages_for("testapp/sensor/state")]
        assert payloads[0] == '{"value":"tick"}'
        assert payloads.count('{"value":"woken"}') == 1
        # OnChange still suppresses the repeated scheduled payloads
        assert payloads.count('{"value":"tick"}') == 1

    async def test_local_wake_reports_local_trigger_source(self) -> None:
        """TriggerPayload.source is "local" for an in-process wake."""
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        seen: list[str] = []
        woken = asyncio.Event()

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.telemetry("sensor", interval=3600, triggerable="both")
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            seen.append(trigger.source)
            if trigger.source == "local":
                woken.set()
            return {"value": len(seen)}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            holder[0].notify("sensor")
            await asyncio.wait_for(woken.wait(), timeout=5.0)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert seen[0] == "scheduled"
        assert "local" in seen

    async def test_mqtt_wake_reports_mqtt_trigger_source(self) -> None:
        """TriggerPayload.source is "mqtt" for an inbound /set message."""
        # Arrange
        harness = AppHarness.create()
        seen: list[str] = []
        triggered = asyncio.Event()

        @harness.app.telemetry("sensor", interval=3600, triggerable="both")
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            seen.append(trigger.source)
            if trigger.source == "mqtt":
                triggered.set()
            return {"value": len(seen)}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            await harness.mqtt.deliver("testapp/sensor/set", "")
            await asyncio.wait_for(triggered.wait(), timeout=5.0)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert seen[0] == "scheduled"
        assert "mqtt" in seen

    async def test_local_only_entity_ignores_set_messages(self) -> None:
        """A /set publish must not wake a local-only entity."""
        # Arrange
        harness = AppHarness.create()
        sources: list[str] = []
        holder: list[_NotifierHolder] = []
        probe_woken = asyncio.Event()

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.telemetry("sensor", interval=3600, triggerable="local")
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            sources.append(trigger.source)
            return {"value": len(sources)}

        @harness.app.telemetry("probe", interval=3600, triggerable="local")
        async def probe(trigger: TriggerPayload) -> dict[str, object]:
            if trigger.source == "local":
                probe_woken.set()
            return {"probe": 1}

        async def _simulate() -> None:
            # Wait for initial scheduled run of sensor
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            # Try to wake via /set (should be ignored — no MQTT subscription)
            await harness.mqtt.deliver("testapp/sensor/set", "")
            # Wake probe locally as a deterministic synchronisation point:
            # once probe runs, the loop has processed all prior events
            holder[0].notify("probe")
            await asyncio.wait_for(probe_woken.wait(), timeout=5.0)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert sources, "handler never ran"
        assert set(sources) == {"scheduled"}

    async def test_notifier_rejects_mqtt_only_entity(self) -> None:
        """An MQTT-only entity is not notifiable — the name is unknown."""
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        captured: list[BaseException] = []

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.telemetry("sensor", interval=3600, triggerable="mqtt")
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/sensor/state"):
                await asyncio.sleep(0.01)
            try:
                holder[0].notify("sensor")
            except UnknownEntityError as exc:  # pragma: no branch
                captured.append(exc)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert len(captured) == 1
        assert "locally-triggerable" in str(captured[0])

    async def test_notifier_armed_in_state_factory_raises_not_ready(self) -> None:
        """Phase-1 arming fails loudly — the slots do not exist yet.

        Technique: Error Guessing — @app.state factories run before
        TriggerConfig.build, so this is the ordering hazard ADR-064 calls
        out.  It must raise, never silently drop the wake.
        """
        # Arrange
        harness = AppHarness.create()

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            notify("sensor")  # too early — Phase 1
            return _NotifierHolder(notify=notify)

        @harness.app.telemetry("sensor", interval=3600, triggerable="local")
        async def sensor() -> dict[str, object]:
            return {"value": 1}

        # Act & Assert
        with pytest.raises(NotifierNotReadyError):
            await asyncio.wait_for(harness.run(), timeout=10.0)

    async def test_notifier_wakes_the_named_expanded_entity_only(self) -> None:
        """With expanded names, only the notified entity runs early."""
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        runs: dict[str, list[str]] = {"sensor-a": [], "sensor-b": []}
        woken = asyncio.Event()

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.telemetry(
            name=lambda _s: ["sensor-a", "sensor-b"],
            interval=3600,
            triggerable="local",
        )
        async def sensor(
            ctx: cosalette.DeviceContext, trigger: TriggerPayload
        ) -> dict[str, object]:
            runs[ctx.name].append(trigger.source)
            if trigger.source == "local":
                woken.set()
            return {"value": len(runs[ctx.name])}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/sensor-b/state"):
                await asyncio.sleep(0.01)
            holder[0].notify("sensor-a")
            await asyncio.wait_for(woken.wait(), timeout=5.0)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert "local" in runs["sensor-a"]
        assert "local" not in runs["sensor-b"]
        assert runs["sensor-b"], "the un-notified entity still ticks"


class _TempReading(BaseModel):
    """Telemetry state model carrying a real consumer() annotation."""

    celsius: Annotated[
        float,
        Field(
            json_schema_extra=consumer(
                display_name="Temperature",
                device_class="temperature",
                unit="°C",
                state_class="measurement",
            )
        ),
    ]


def _parity_app(triggerable: cosalette.TriggerableSpec) -> App:
    """Build an identical app that differs only in its trigger source."""
    app = App(name="testapp", version="1.0.0")

    @app.telemetry(
        "sensor", interval=30, state_model=_TempReading, triggerable=triggerable
    )
    async def _sensor():  # pragma: no cover
        return {"celsius": 21.5}

    return app


def _parity_device_app(triggerable: cosalette.TriggerableSpec) -> App:
    """Build an identical device app that differs only in its trigger source.

    A triggerable device must declare a :class:`DeviceTrigger` parameter,
    so the handler signature necessarily differs between the baseline and
    the variant.  That is deliberate: the point of the comparison is that
    the *emitted* discovery and AsyncAPI documents stay byte-identical even
    though the registration and the handler signature do not (ADR-065).
    """
    app = App(name="testapp", version="1.0.0")

    if triggerable:

        @app.device("gadget", state_model=_TempReading, triggerable=triggerable)
        async def _gadget_triggerable(  # pragma: no cover
            trigger: DeviceTrigger,
        ) -> AsyncIterator[None]:
            await trigger.wait()
            yield

    else:

        @app.device("gadget", state_model=_TempReading)
        async def _gadget() -> AsyncIterator[None]:  # pragma: no cover
            yield

    return app


class TestDiscoveryAndAsyncApiParity:
    """The trigger source must not reach discovery or AsyncAPI output.

    Technique: Round-trip / Comparison Testing — the ADR-059 discovery
    chain and the ADR-054 AsyncAPI document must be byte-identical
    whatever ``triggerable=`` says (ADR-064 parity requirement).
    """

    @pytest.mark.parametrize("spec", [True, "mqtt", "local", "both"])
    def test_asyncapi_output_is_identical(
        self, spec: cosalette.TriggerableSpec
    ) -> None:
        """app.asyncapi() ignores the trigger source entirely."""
        # Arrange
        baseline = _parity_app(False)
        variant = _parity_app(spec)

        # Act & Assert
        assert variant.asyncapi() == baseline.asyncapi()

    @pytest.mark.parametrize("spec", [True, "mqtt", "local", "both"])
    async def test_discovery_payloads_are_identical(
        self, spec: cosalette.TriggerableSpec
    ) -> None:
        """Generated HA discovery topics and configs are unchanged."""
        # Arrange
        config = DiscoveryConfig()
        baseline = await build_discovery_payloads(_parity_app(False), config)
        variant = await build_discovery_payloads(_parity_app(spec), config)

        # Act
        as_pairs = [(p.topic, p.config) for p in variant]

        # Assert
        assert as_pairs == [(p.topic, p.config) for p in baseline]

    def test_device_asyncapi_output_is_identical(self) -> None:
        """app.asyncapi() ignores a device's trigger source (ADR-065)."""
        # Arrange
        baseline = _parity_device_app(False)
        variant = _parity_device_app("local")

        # Act & Assert
        assert variant.asyncapi() == baseline.asyncapi()

    async def test_device_discovery_payloads_are_identical(self) -> None:
        """A triggerable device emits unchanged HA discovery payloads."""
        # Arrange
        config = DiscoveryConfig()
        baseline = await build_discovery_payloads(_parity_device_app(False), config)
        variant = await build_discovery_payloads(_parity_device_app("local"), config)

        # Act
        as_pairs = [(p.topic, p.config) for p in variant]

        # Assert
        assert as_pairs == [(p.topic, p.config) for p in baseline]
