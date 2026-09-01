"""Unit tests for the device local trigger source — ADR-065.

Test Techniques Used:
- Equivalence Partitioning: the device ``triggerable=`` input domain
  (``False``/``"local"`` accepted; ``True``/``"mqtt"``/``"both"``/invalid
  rejected)
- Decision Table: ``triggerable=`` x presence of a ``DeviceTrigger``
  parameter -> accepted or rejected at registration time
- State Transition Testing: ``DeviceTrigger.wait()`` across the
  armed/unarmed and timeout/wake transitions
- Error Guessing: arming before the Phase-2 bind, unknown entity names,
  arming from a non-event-loop thread
- Integration Testing: an end-to-end local wake driving a device through
  its ordinary ``ctx.publish_state()`` cycle
- Comparison Testing: an untouched ``@app.device`` behaves exactly as it
  did before the feature existed (backward compatibility)

Common patterns:
- ``AppHarness`` runs a real app against a ``MockMqttClient``
- ``@harness.app.state`` captures the injected ``EntityNotifier`` so the
  test body can wake an entity from outside the handler
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

import cosalette
from cosalette import (
    App,
    DeviceContext,
    DeviceTrigger,
    EntityNotifier,
    NotifierNotReadyError,
    UnknownEntityError,
)
from cosalette._app._device_validators import (
    plan_declares_device_trigger,
    validate_device_triggerable,
)
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._registration import _DeviceRegistration
from cosalette._runners._telemetry_types import _TriggerSlot
from cosalette._wiring import TriggerConfig, start_device_tasks_for_names
from cosalette.testing import AppHarness, FakeClock, make_settings
from tests.fixtures.notifier import _NotifierHolder

pytestmark = pytest.mark.unit


# =============================================================================
# Helpers
# =============================================================================


async def _noop_device() -> AsyncIterator[None]:  # pragma: no cover
    yield


def _device_reg(
    name: str,
    triggerable: cosalette.TriggerSource | None,
) -> _DeviceRegistration:
    """Build a minimal device registration for wiring-level tests."""
    return _DeviceRegistration(
        name=name,
        func=_noop_device,
        injection_plan=[],
        triggerable=triggerable,
    )


# =============================================================================
# Registration-time validation
# =============================================================================


class TestDeviceTriggerableValidation:
    """``triggerable=`` acceptance on ``@app.device``.

    Technique: Equivalence Partitioning over the spec domain, plus a
    Decision Table over spec x DeviceTrigger-parameter presence.
    """

    def test_plan_declares_device_trigger_detects_the_parameter(self) -> None:
        """The plan predicate spots a DeviceTrigger annotation."""
        # Act & Assert
        assert plan_declares_device_trigger([("trigger", DeviceTrigger)]) is True
        assert plan_declares_device_trigger([("ctx", DeviceContext)]) is False
        assert plan_declares_device_trigger([]) is False

    def test_local_with_handle_is_accepted(self) -> None:
        """The supported combination normalises to "local"."""
        # Act
        result = validate_device_triggerable(
            "local", "gadget", [("trigger", DeviceTrigger)]
        )

        # Assert
        assert result == "local"

    def test_falsy_without_handle_is_accepted(self) -> None:
        """Opting into nothing stays valid and yields no source."""
        # Act
        result = validate_device_triggerable(False, "gadget", [])

        # Assert
        assert result is None

    @pytest.mark.parametrize("spec", [True, "mqtt", "both"])
    def test_mqtt_bearing_sources_are_rejected(
        self, spec: cosalette.TriggerableSpec
    ) -> None:
        """A device cannot take an MQTT trigger source.

        ``{prefix}/{name}/set`` is already the device command topic, so
        an MQTT arming path would collide with ``ctx.on_command``.
        """
        # Act & Assert
        with pytest.raises(ValueError, match="devices accept triggerable='local'"):
            validate_device_triggerable(spec, "gadget", [("trigger", DeviceTrigger)])

    def test_rejection_message_names_the_command_topic(self) -> None:
        """The error explains *why*, naming the conflicting topic."""
        # Act & Assert
        with pytest.raises(ValueError, match=r"\{prefix\}/gadget/set"):
            validate_device_triggerable("mqtt", "gadget", [])

    def test_unknown_source_string_is_rejected(self) -> None:
        """An unrecognised spelling still raises from the shared normaliser."""
        # Act & Assert
        with pytest.raises(ValueError, match="not a valid trigger source"):
            validate_device_triggerable(
                "Local",  # ty: ignore[invalid-argument-type]
                "gadget",
                [],
            )

    def test_handle_without_opt_in_is_rejected(self) -> None:
        """A DeviceTrigger parameter with no triggerable= fails loudly."""
        # Act & Assert
        with pytest.raises(ValueError, match="declares a DeviceTrigger parameter"):
            validate_device_triggerable(False, "gadget", [("trigger", DeviceTrigger)])

    def test_opt_in_without_handle_is_rejected(self) -> None:
        """triggerable= with no DeviceTrigger parameter fails loudly.

        Technique: Error Guessing — the silent-no-op failure mode this
        guard exists to prevent.
        """
        # Act & Assert
        with pytest.raises(ValueError, match="declares no DeviceTrigger parameter"):
            validate_device_triggerable("local", "gadget", [("ctx", DeviceContext)])


class TestDeviceDecoratorValidation:
    """The guards fire through the real decorator, not just the helper."""

    def test_decorator_accepts_local_with_handle(self) -> None:
        """The supported registration records the normalised source."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act
        @app.device("gadget", triggerable="local")
        async def _gadget(trigger: DeviceTrigger) -> AsyncIterator[None]:
            yield

        # Assert
        assert app.devices[0].triggerable == "local"

    def test_decorator_rejects_mqtt_source(self) -> None:
        """@app.device(triggerable="mqtt") raises at decoration time."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act & Assert
        with pytest.raises(ValueError, match="devices accept triggerable='local'"):

            @app.device("gadget", triggerable="mqtt")
            async def _gadget(trigger: DeviceTrigger) -> AsyncIterator[None]:
                yield

    def test_decorator_rejects_opt_in_without_handle(self) -> None:
        """Opting in without the parameter raises at decoration time."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act & Assert
        with pytest.raises(ValueError, match="declares no DeviceTrigger parameter"):

            @app.device("gadget", triggerable="local")
            async def _gadget(ctx: DeviceContext) -> AsyncIterator[None]:
                yield

    def test_add_device_applies_the_same_guards(self) -> None:
        """The imperative path validates identically to the decorator."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        async def _gadget(
            ctx: DeviceContext,
        ) -> AsyncIterator[None]:  # pragma: no cover
            yield

        # Act & Assert
        with pytest.raises(ValueError, match="declares no DeviceTrigger parameter"):
            app.add_device("gadget", _gadget, triggerable="local")


# =============================================================================
# Wiring
# =============================================================================


class TestTriggerConfigCoversDevices:
    """``TriggerConfig`` allocates and exposes device slots.

    Technique: Decision Table — registration kind x trigger source ->
    whether a slot exists and whether the notifier may arm it.
    """

    def test_build_creates_a_slot_for_a_triggerable_device(self) -> None:
        """A local device gets its own slot alongside telemetry."""
        # Arrange
        devices = [_device_reg("gadget", "local"), _device_reg("plain", None)]

        # Act
        config = TriggerConfig.build([], devices)

        # Assert
        assert set(config.slots) == {"gadget"}

    def test_local_slots_includes_the_device(self) -> None:
        """The notifier may arm a triggerable device by name."""
        # Arrange
        config = TriggerConfig.build([], [_device_reg("gadget", "local")])

        # Act
        local = config.local_slots()

        # Assert
        assert set(local) == {"gadget"}
        assert local["gadget"] is config.slots["gadget"]

    def test_devices_default_to_empty(self) -> None:
        """Omitting devices= keeps the ADR-064 telemetry-only behaviour."""
        # Act
        config = TriggerConfig.build([])

        # Assert
        assert config.devices == []
        assert config.slots == {}


# =============================================================================
# DeviceTrigger behaviour
# =============================================================================


class TestDeviceTriggerWait:
    """``DeviceTrigger.wait()`` transitions.

    Technique: State Transition Testing — unarmed/armed x
    timeout/no-timeout, including the wake-vs-timeout tie.
    """

    @pytest.fixture
    def slot(self) -> _TriggerSlot:
        """Fresh _TriggerSlot for each test."""
        return _TriggerSlot(event=asyncio.Event())

    @pytest.fixture
    def trigger(self, slot: _TriggerSlot) -> DeviceTrigger:
        """DeviceTrigger bound to *slot* with a fake clock."""
        return DeviceTrigger(slot, "gadget", FakeClock())

    def test_name_exposes_the_entity(self, trigger: DeviceTrigger) -> None:
        """The handle reports which entity it waits on."""
        # Act & Assert
        assert trigger.name == "gadget"
        assert repr(trigger) == "DeviceTrigger('gadget')"

    async def test_wait_returns_local_payload_when_armed(
        self, slot: _TriggerSlot, trigger: DeviceTrigger
    ) -> None:
        """An armed slot resolves wait() with a local payload."""
        # Arrange
        slot.arm_local()

        # Act
        payload = await asyncio.wait_for(trigger.wait(), timeout=1.0)

        # Assert
        assert payload.source == "local"
        assert payload.is_triggered is True

    async def test_wait_consumes_the_slot(
        self, slot: _TriggerSlot, trigger: DeviceTrigger
    ) -> None:
        """A consumed wake does not resolve a second wait()."""
        # Arrange
        slot.arm_local()
        await asyncio.wait_for(trigger.wait(), timeout=1.0)

        # Assert
        assert slot.event.is_set() is False

    async def test_wait_blocks_until_armed(
        self, slot: _TriggerSlot, trigger: DeviceTrigger
    ) -> None:
        """wait() with no timeout resolves only once the slot is armed."""
        # Arrange
        waiter = asyncio.create_task(trigger.wait())
        await asyncio.sleep(0)
        assert not waiter.done()

        # Act
        slot.arm_local()
        payload = await asyncio.wait_for(waiter, timeout=1.0)

        # Assert
        assert payload.source == "local"

    async def test_timeout_returns_a_scheduled_payload(
        self, trigger: DeviceTrigger
    ) -> None:
        """An elapsed heartbeat is reported as a scheduled run."""
        # Act
        payload = await asyncio.wait_for(trigger.wait(timeout=30.0), timeout=1.0)

        # Assert
        assert payload.source == "scheduled"
        assert payload.is_triggered is False

    async def test_wake_wins_a_tie_with_the_timeout(
        self, slot: _TriggerSlot, trigger: DeviceTrigger
    ) -> None:
        """A wake pending at timeout time is delivered, not swallowed.

        FakeClock.sleep() returns on the first loop iteration, so both
        branches complete together — the wake must still win.
        """
        # Arrange
        slot.arm_local()

        # Act
        payload = await asyncio.wait_for(trigger.wait(timeout=30.0), timeout=1.0)

        # Assert
        assert payload.source == "local"

    async def test_wake_arriving_after_a_timeout_is_not_lost(
        self, slot: _TriggerSlot, trigger: DeviceTrigger
    ) -> None:
        """Coalescing: a wake armed after a timed-out wait survives.

        Technique: Error Guessing — the classic lost-wakeup bug.
        """
        # Arrange
        first = await asyncio.wait_for(trigger.wait(timeout=30.0), timeout=1.0)
        assert first.source == "scheduled"

        # Act
        slot.arm_local()
        second = await asyncio.wait_for(trigger.wait(timeout=30.0), timeout=1.0)

        # Assert
        assert second.source == "local"

    async def test_cancellation_propagates_and_slot_is_not_consumed(
        self, slot: _TriggerSlot, trigger: DeviceTrigger
    ) -> None:
        """Cancelling wait(timeout=None) raises CancelledError; slot stays intact.

        Technique: Error Guessing — the normal graceful-shutdown path
        cancels device tasks while they are blocked on trigger.wait().
        Correct propagation is critical; a swallowed cancel would prevent
        clean shutdown.
        """
        # Arrange — start waiting with no timeout (the shutdown path)
        waiter = asyncio.create_task(trigger.wait())
        await asyncio.sleep(0)
        assert not waiter.done()

        # Act — simulate task cancellation (e.g. from harness.trigger_shutdown)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        # Assert — slot must not be consumed (event still clear)
        assert not slot.event.is_set()


# =============================================================================
# End-to-end execution
# =============================================================================


class TestDeviceLocalTriggerExecution:
    """A notifier wake drives a real device through the running app.

    Technique: Integration Testing — the woken run must publish through
    the device's ordinary ``ctx.publish_state()`` cycle.
    """

    async def test_local_wake_drives_the_device_publish_cycle(self) -> None:
        """A notifier call makes the device publish without a tick."""
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        woken = asyncio.Event()

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.device("gadget", triggerable="local")
        async def gadget(
            ctx: DeviceContext, trigger: DeviceTrigger
        ) -> AsyncIterator[None]:
            count = 0
            await ctx.publish_state({"value": count})
            while True:
                payload = await trigger.wait()
                count += 1
                await ctx.publish_state({"value": count, "source": payload.source})
                woken.set()
                yield

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/gadget/state"):
                await asyncio.sleep(0.01)
            holder[0].notify("gadget")
            await asyncio.wait_for(woken.wait(), timeout=5.0)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        payloads = [m[0] for m in harness.mqtt.get_messages_for("testapp/gadget/state")]
        assert payloads[0] == '{"value":0}'
        assert len(payloads) >= 2
        assert payloads[1] == '{"value":1,"source":"local"}'

    async def test_triggerable_device_subscribes_no_trigger_topic(self) -> None:
        """A local device adds no subscription beyond its command topic.

        ``{prefix}/{name}/set`` is subscribed because it is the *command*
        topic, exactly as for a non-triggerable device — the trigger
        source adds nothing to the MQTT surface (ADR-065).
        """
        # Arrange — measure the plain-device surface, do not assume it
        baseline = await _run_device_and_capture_subscriptions(triggerable=False)

        # Act
        variant = await _run_device_and_capture_subscriptions(triggerable="local")

        # Assert — positive check first so the comparison cannot vacuously pass
        assert "testapp/gadget/set" in baseline
        assert variant == baseline

    async def test_notifying_one_device_leaves_another_asleep(self) -> None:
        """Wakes are per-entity, not broadcast."""
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        woken: dict[str, int] = {"a": 0, "b": 0}
        first_awake = asyncio.Event()

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        def _make(key: str, name: str) -> None:
            @harness.app.device(name, triggerable="local")
            async def _gadget(trigger: DeviceTrigger) -> AsyncIterator[None]:
                while True:
                    await trigger.wait()
                    woken[key] += 1
                    if key == "a":
                        first_awake.set()
                    yield

        _make("a", "gadget-a")
        _make("b", "gadget-b")

        async def _simulate() -> None:
            await asyncio.sleep(0.05)
            holder[0].notify("gadget-a")
            await asyncio.wait_for(first_awake.wait(), timeout=5.0)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert woken["a"] == 1
        assert woken["b"] == 0

    async def test_notifier_wakes_a_device_from_another_thread(self) -> None:
        """Arming is thread-safe from a non-event-loop thread.

        Technique: Error Guessing — an adapter decoding frames on its own
        thread is the motivating caller (ADR-065 context).
        """
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        woken = asyncio.Event()
        seen: list[str] = []

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.device("gadget", triggerable="local")
        async def gadget(trigger: DeviceTrigger) -> AsyncIterator[None]:
            while True:
                payload = await trigger.wait()
                seen.append(payload.source)
                woken.set()
                yield

        async def _simulate() -> None:
            await asyncio.sleep(0.05)
            thread = threading.Thread(target=holder[0].notify, args=("gadget",))
            thread.start()
            await asyncio.wait_for(woken.wait(), timeout=5.0)
            thread.join(timeout=5.0)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert seen == ["local"]


class TestDeviceNotifierErrors:
    """The notifier fails loudly on the device path too.

    Technique: Error Guessing — the two ways a caller gets the handle
    wrong, both of which must raise rather than silently no-op.
    """

    def test_arming_before_bind_raises_not_ready(self) -> None:
        """A Phase-1 handle rejects arming until Phase 2 binds it."""
        # Arrange
        notifier = EntityNotifier()

        # Act & Assert
        with pytest.raises(NotifierNotReadyError):
            notifier("gadget")

    async def test_unknown_device_name_raises(self) -> None:
        """Notifying a name that is not a local entity raises."""
        # Arrange
        notifier = EntityNotifier()
        config = TriggerConfig.build([], [_device_reg("gadget", "local")])
        notifier._bind(config.local_slots())

        # Act & Assert
        with pytest.raises(UnknownEntityError):
            notifier("nope")

    async def test_non_triggerable_device_is_not_notifiable(self) -> None:
        """A device that opted into nothing is not a valid notify target."""
        # Arrange
        notifier = EntityNotifier()
        config = TriggerConfig.build([], [_device_reg("plain", None)])
        notifier._bind(config.local_slots())

        # Act & Assert
        with pytest.raises(UnknownEntityError):
            notifier("plain")

    async def test_unknown_name_raises_inside_a_running_app(self) -> None:
        """The guard holds end-to-end, not just against a hand-built config."""
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        raised: list[type[BaseException]] = []
        done = asyncio.Event()

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.device("gadget", triggerable="local")
        async def gadget(trigger: DeviceTrigger) -> AsyncIterator[None]:
            await trigger.wait()
            yield

        async def _simulate() -> None:
            await asyncio.sleep(0.05)
            try:
                holder[0].notify("typo")
            except UnknownEntityError as exc:
                raised.append(type(exc))
            done.set()
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert raised == [UnknownEntityError]


class TestDeviceBackwardCompatibility:
    """An untouched ``@app.device`` is unaffected by the feature.

    Technique: Comparison Testing — the registration, the injected
    providers and the MQTT surface must match the pre-feature behaviour.
    """

    def test_plain_device_records_no_trigger_source(self) -> None:
        """Omitting triggerable= leaves the registration field None."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act
        @app.device("gadget")
        async def _gadget() -> AsyncIterator[None]:  # pragma: no cover
            yield

        # Assert
        assert app.devices[0].triggerable is None

    async def test_plain_device_runs_and_publishes_unchanged(self) -> None:
        """A zero-parameter device still runs its generator to completion."""
        # Arrange
        harness = AppHarness.create()
        ran = asyncio.Event()

        @harness.app.device("gadget")
        async def gadget(ctx: DeviceContext) -> AsyncIterator[None]:
            await ctx.publish_state({"value": 1})
            ran.set()
            yield

        async def _simulate() -> None:
            await asyncio.wait_for(ran.wait(), timeout=5.0)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        payloads = [m[0] for m in harness.mqtt.get_messages_for("testapp/gadget/state")]
        assert payloads == ['{"value":1}']

    async def test_plain_device_gets_no_device_trigger_provider(self) -> None:
        """DeviceTrigger is unresolvable for a device that did not opt in.

        Technique: Error Guessing — the provider must not leak in by
        default, or the symmetric registration guard would be bypassable.
        """
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act & Assert — the registration guard is what stops it
        with pytest.raises(ValueError, match="declares a DeviceTrigger parameter"):

            @app.device("gadget")
            async def _gadget(trigger: DeviceTrigger) -> AsyncIterator[None]:
                yield


async def _run_device_and_capture_subscriptions(
    *, triggerable: cosalette.TriggerableSpec
) -> set[str]:
    """Run a one-device app to shutdown and return its MQTT subscriptions."""
    harness = AppHarness.create()
    started = asyncio.Event()

    if triggerable:

        @harness.app.device("gadget", triggerable=triggerable)
        async def _gadget_triggerable(
            trigger: DeviceTrigger,
        ) -> AsyncIterator[None]:
            started.set()
            await trigger.wait()
            yield

    else:

        @harness.app.device("gadget")
        async def _gadget() -> AsyncIterator[None]:
            started.set()
            await asyncio.Event().wait()
            yield

    async def _simulate() -> None:
        await asyncio.wait_for(started.wait(), timeout=5.0)
        harness.trigger_shutdown()

    _task = asyncio.create_task(_simulate())
    await asyncio.wait_for(harness.run(), timeout=10.0)
    return set(harness.mqtt.subscriptions)


class TestRestartPathForwardsSlots:
    """The adapter-restart path must not drop trigger slots.

    Technique: Error Guessing — before ADR-065,
    ``start_device_tasks_for_names`` did not accept ``trigger_slots``, so
    any triggerable entity recreated after an adapter restart silently
    lost the slot its ``EntityNotifier`` was bound to and became
    permanently unwakeable.  This is a regression test for that defect.
    """

    async def test_restarted_device_still_receives_its_slot(self) -> None:
        """A device restarted by name is rebound to the original slot."""
        # Arrange
        clock = FakeClock()
        mqtt = AsyncMock()
        reporter = HealthReporter(
            mqtt=mqtt, topic_prefix="test", version="0.1.0", clock=clock
        )
        error_pub = ErrorPublisher(mqtt=mqtt, topic_prefix="test")
        event = asyncio.Event()
        woken = asyncio.Event()

        async def handler(trigger: DeviceTrigger) -> AsyncIterator[None]:
            await trigger.wait()
            woken.set()
            yield

        reg = _DeviceRegistration(
            name="gadget",
            func=handler,
            injection_plan=[("trigger", DeviceTrigger)],
            is_root=False,
            triggerable="local",
        )
        config = TriggerConfig.build([], [reg])
        ctx = DeviceContext(
            name="gadget",
            settings=make_settings(),
            mqtt=mqtt,
            topic_prefix="test",
            shutdown_event=event,
            adapters={},
            clock=clock,
            is_root=False,
        )

        # Act — restart the device, then arm the slot the notifier holds
        tasks, _map = start_device_tasks_for_names(
            ["gadget"],
            [reg],
            [],
            None,
            {"gadget": ctx},
            error_pub,
            reporter,
            trigger_slots=config.slots,
        )
        await asyncio.sleep(0)
        config.local_slots()["gadget"].arm_local()

        # Assert
        await asyncio.wait_for(woken.wait(), timeout=5.0)

        # Cleanup
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_pre_armed_slot_wakes_device_after_restart(self) -> None:
        """A wake coalesced before restart is not lost when the device task starts.

        Technique: Error Guessing — a slot armed while the adapter was
        down must survive and deliver immediately on restart, not be
        silently dropped because the task was not yet running.
        """
        # Arrange
        clock = FakeClock()
        mqtt = AsyncMock()
        reporter = HealthReporter(
            mqtt=mqtt, topic_prefix="test", version="0.1.0", clock=clock
        )
        error_pub = ErrorPublisher(mqtt=mqtt, topic_prefix="test")
        event = asyncio.Event()
        woken = asyncio.Event()

        async def handler(trigger: DeviceTrigger) -> AsyncIterator[None]:
            await trigger.wait()
            woken.set()
            yield

        reg = _DeviceRegistration(
            name="gadget",
            func=handler,
            injection_plan=[("trigger", DeviceTrigger)],
            is_root=False,
            triggerable="local",
        )
        config = TriggerConfig.build([], [reg])
        ctx = DeviceContext(
            name="gadget",
            settings=make_settings(),
            mqtt=mqtt,
            topic_prefix="test",
            shutdown_event=event,
            adapters={},
            clock=clock,
            is_root=False,
        )

        # Act — arm the slot BEFORE starting the device task
        config.local_slots()["gadget"].arm_local()
        tasks, _map = start_device_tasks_for_names(
            ["gadget"],
            [reg],
            [],
            None,
            {"gadget": ctx},
            error_pub,
            reporter,
            trigger_slots=config.slots,
        )

        # Assert — the pre-armed wake must be delivered without a second notify
        await asyncio.wait_for(woken.wait(), timeout=5.0)

        # Cleanup
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
