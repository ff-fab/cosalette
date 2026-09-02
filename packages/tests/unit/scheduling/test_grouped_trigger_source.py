"""Unit tests for a trigger source on a coalescing-group member — ADR-067.

Test Techniques Used:
- Decision Table: (tick due?) x (member armed?) x (throttle window open?)
  -> which members run in the batch and what each one's
  ``TriggerPayload.source`` reports
- State Transition Testing: quiet -> armed -> released -> quiet, driven
  through the *group* scheduler rather than the per-entity loop
- Equivalence Partitioning: the MQTT and local arming paths, which
  ADR-067 makes indistinguishable to the scheduler
- Pairwise: trigger source x ``group=`` membership across the parity
  assertions (AsyncAPI, Home Assistant discovery, subscription set)
- Error Guessing: an arm landing on a member excluded by a failing init,
  and a ``min_interval=`` window that spans several heartbeats

Common patterns:
- ``_GroupBench`` drives the real
  :meth:`~cosalette._runners._telemetry_runner.TelemetryRunner.run_telemetry_group`
  on a :class:`~cosalette.testing.FakeClock`; the module contains no
  wall-clock sleeps.  Slots come from :meth:`TriggerConfig.build`, so the
  shared group wake event is wired the way the app wires it.
- Scripts arm slots from *inside* a handler.  That places the arm while
  the scheduler is between sleeps, which is what makes each timeline
  exact instead of racy.
- The scheduler seeds every member at tick 0, so run #0 for each member
  is always the bootstrap scheduled poll, never a trigger-initiated run.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest

from cosalette import (
    App,
    EntityNotifier,
    TriggerPayload,
    TriggerSource,
    UnknownEntityError,
)
from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._injection import build_injection_plan
from cosalette._registration import _TelemetryRegistration
from cosalette._runners._telemetry_runner import TelemetryRunner
from cosalette._strategies import OnChange, PublishStrategy
from cosalette._wiring import TriggerConfig
from cosalette._wiring._discovery import DiscoveryConfig, build_discovery_payloads
from cosalette.testing import AppHarness, FakeClock, MockMqttClient, make_settings
from tests.fixtures.notifier import _NotifierHolder

pytestmark = pytest.mark.unit

_FIXED_DT = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
_GROUP = "bus"


# =============================================================================
# Helpers
# =============================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _Member:
    """One member of the group under test."""

    name: str
    interval: float
    triggerable: TriggerSource | None = None
    min_interval: float | None = None
    publish: PublishStrategy | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _Run:
    """One handler invocation: which member, when, and what woke it."""

    name: str
    at: float
    source: str
    raw: str | None


_Script = Callable[["_GroupBench", str, TriggerPayload], Coroutine[Any, Any, None]]


class _GroupBench:
    """Drive the real coalescing-group scheduler with scripted handlers.

    Every member records a :class:`_Run` and then hands control to the
    test's *script*, which is where arms are placed and where the loop is
    asked to stop.  Members are declared in registration order, which is
    also the order the scheduler executes a batch in.
    """

    def __init__(self, *members: _Member) -> None:
        self.clock = FakeClock()
        self.mqtt = MockMqttClient()
        self.shutdown = asyncio.Event()
        self.runs: list[_Run] = []
        self.failing_init: str | None = None
        self._members = members

    # -- scripting helpers ---------------------------------------------

    def stop(self) -> None:
        """Ask the scheduler to exit after the current batch."""
        self.shutdown.set()

    def arm(self, name: str, raw: str = "{}") -> None:
        """Arm *name* the way an inbound MQTT ``/set`` would."""
        self.slots[name].arm(raw)

    def arm_local(self, name: str) -> None:
        """Arm *name* the way an ``EntityNotifier`` call would."""
        self.slots[name].arm_local()

    def runs_of(self, name: str) -> list[_Run]:
        """Every invocation of one member, in order."""
        return [run for run in self.runs if run.name == name]

    def timeline(self) -> list[tuple[str, float, str]]:
        """``(member, virtual time, trigger source)`` for every run."""
        return [(run.name, run.at, run.source) for run in self.runs]

    def state_payloads(self, name: str) -> list[str]:
        """Every state publish one member emitted, in order."""
        return [
            payload
            for topic, payload, _retain, _qos in self.mqtt.published
            if topic == f"test/{name}/state"
        ]

    # -- execution ------------------------------------------------------

    def _make_handler(self, member: _Member, script: _Script) -> Any:
        bench = self

        async def handler(trigger: TriggerPayload) -> dict[str, object]:
            bench.runs.append(
                _Run(
                    name=member.name,
                    at=bench.clock.now(),
                    source=trigger.source,
                    raw=trigger.raw,
                )
            )
            await script(bench, member.name, trigger)
            return {"n": len(bench.runs_of(member.name))}

        return handler

    def _context(self, name: str) -> DeviceContext:
        return DeviceContext(
            name=name,
            settings=make_settings(),
            mqtt=self.mqtt,
            topic_prefix="test",
            shutdown_event=self.shutdown,
            adapters={},
            clock=self.clock,
        )

    async def run(self, script: _Script, *, timeout: float = 5.0) -> None:
        """Run the group scheduler until the script requests shutdown."""

        def _boom() -> object:
            msg = "init failed"
            raise RuntimeError(msg)

        regs = []
        for member in self._members:
            handler = self._make_handler(member, script)
            regs.append(
                _TelemetryRegistration(
                    name=member.name,
                    func=handler,
                    injection_plan=build_injection_plan(handler),
                    interval=member.interval,
                    group=_GROUP,
                    triggerable=member.triggerable,
                    min_interval=member.min_interval,
                    publish_strategy=member.publish,
                    init=_boom if member.name == self.failing_init else None,
                    init_injection_plan=(
                        [] if member.name == self.failing_init else None
                    ),
                )
            )
        self.regs = regs
        self.config = TriggerConfig.build(regs)
        self.slots = self.config.slots

        runner = TelemetryRunner(None)
        await asyncio.wait_for(
            runner.run_telemetry_group(
                _GROUP,
                regs,
                {reg.name: self._context(reg.name) for reg in regs},
                ErrorPublisher(
                    mqtt=self.mqtt, topic_prefix="test", clock=lambda: _FIXED_DT
                ),
                HealthReporter(
                    mqtt=self.mqtt,
                    topic_prefix="test",
                    version="0.0.0",
                    clock=self.clock,
                ),
                None,
                trigger_slots=self.slots,
            ),
            timeout=timeout,
        )


async def _stop_after(bench: _GroupBench, cycles: int) -> None:
    """Stop once the bench has recorded *cycles* runs in total."""
    if len(bench.runs) >= cycles:
        bench.stop()


def _grouped_app(triggerable: TriggerSource | None) -> App:
    """Two grouped telemetry entities, optionally with a trigger source."""
    app = App(name="testapp", version="1.0.0")

    @app.telemetry("alpha", interval=10, group=_GROUP, triggerable=triggerable or False)
    async def alpha() -> dict[str, object]:
        return {"v": 1}

    @app.telemetry("beta", interval=10, group=_GROUP)
    async def beta() -> dict[str, object]:
        return {"v": 2}

    return app


# =============================================================================
# The shared group wake event
# =============================================================================


class TestGroupWakeWiring:
    """``TriggerConfig.build`` hands one wake event to each group.

    Technique: Equivalence Partitioning — grouped vs ungrouped members,
    and members of the same vs different groups.
    """

    @staticmethod
    def _reg(name: str, *, group: str | None, source: TriggerSource | None) -> Any:
        async def poll() -> dict[str, object]:
            return {}

        return _TelemetryRegistration(
            name=name,
            func=poll,
            injection_plan=[],
            interval=10.0,
            group=group,
            triggerable=source,
        )

    def test_members_of_one_group_share_a_single_wake_event(self) -> None:
        """Both members' slots point at the identical Event object."""
        # Arrange
        regs = [
            self._reg("a", group="g", source="local"),
            self._reg("b", group="g", source="mqtt"),
        ]

        # Act
        config = TriggerConfig.build(regs)

        # Assert
        assert config.slots["a"].wake is config.slots["b"].wake
        assert config.slots["a"].wake is not None

    def test_distinct_groups_get_distinct_wake_events(self) -> None:
        """A wake in one group must not disturb another group's scheduler."""
        # Arrange
        regs = [
            self._reg("a", group="g1", source="local"),
            self._reg("b", group="g2", source="local"),
        ]

        # Act
        config = TriggerConfig.build(regs)

        # Assert
        assert config.slots["a"].wake is not config.slots["b"].wake

    def test_ungrouped_member_has_no_wake_event(self) -> None:
        """The per-entity loop waits on the slot event, so wake stays None."""
        # Arrange & Act
        config = TriggerConfig.build([self._reg("a", group=None, source="local")])

        # Assert
        assert config.slots["a"].wake is None

    def test_non_triggerable_group_member_gets_no_slot(self) -> None:
        """Grouping alone never creates a slot."""
        # Arrange & Act
        config = TriggerConfig.build([self._reg("a", group="g", source=None)])

        # Assert
        assert config.slots == {}

    @pytest.mark.parametrize("arm", ["mqtt", "local"])
    def test_arming_signals_the_group_after_the_member(self, arm: str) -> None:
        """Both arming paths raise the member event and the group wake."""
        # Arrange
        config = TriggerConfig.build([self._reg("a", group="g", source="both")])
        slot = config.slots["a"]

        # Act
        slot.arm("{}") if arm == "mqtt" else slot.arm_local()

        # Assert
        assert slot.event.is_set()
        assert slot.wake is not None
        assert slot.wake.is_set()

    def test_consume_leaves_the_group_wake_alone(self) -> None:
        """The scheduler owns the wake edge; the slot must never clear it."""
        # Arrange
        config = TriggerConfig.build([self._reg("a", group="g", source="local")])
        slot = config.slots["a"]
        slot.arm_local()

        # Act
        slot.consume()

        # Assert
        assert not slot.event.is_set()
        assert slot.wake is not None
        assert slot.wake.is_set()


# =============================================================================
# Per-member wake semantics
# =============================================================================


class TestArmWakesOneMember:
    """An arm runs the armed member and nobody else (ADR-067).

    Technique: Decision Table — the batch is the union of the tick-due
    members and the released arms, so each test fixes one column.
    """

    async def test_arm_runs_only_the_armed_member(self) -> None:
        """The sibling is not invoked at all by another member's wake."""
        # Arrange
        bench = _GroupBench(
            _Member("alpha", interval=1000, triggerable="local"),
            _Member("beta", interval=1000, triggerable="local"),
        )

        async def script(b: _GroupBench, name: str, trigger: TriggerPayload) -> None:
            if name == "alpha" and trigger.source == "scheduled":
                b.arm_local("alpha")
            elif trigger.source == "local":
                b.stop()

        # Act
        await bench.run(script)

        # Assert
        assert bench.timeline() == [
            ("alpha", 0.0, "scheduled"),
            ("beta", 0.0, "scheduled"),
            ("alpha", 0.0, "local"),
        ]

    async def test_unwoken_sibling_publishes_nothing(self) -> None:
        """A member with no new input produces no state message on a wake."""
        # Arrange
        bench = _GroupBench(
            _Member("alpha", interval=1000, triggerable="local"),
            _Member("beta", interval=1000, triggerable="local"),
        )

        async def script(b: _GroupBench, name: str, trigger: TriggerPayload) -> None:
            if name == "alpha" and trigger.source == "scheduled":
                b.arm_local("alpha")
            elif trigger.source == "local":
                b.stop()

        # Act
        await bench.run(script)

        # Assert
        assert len(bench.state_payloads("alpha")) == 2
        assert len(bench.state_payloads("beta")) == 1

    async def test_simultaneous_arms_share_one_batch(self) -> None:
        """A push burst still costs a single execution window."""
        # Arrange — armed from the *last* member of the bootstrap batch, so
        # both arms land after every member of that batch has already run
        bench = _GroupBench(
            _Member("alpha", interval=1000, triggerable="local"),
            _Member("beta", interval=1000, triggerable="local"),
        )

        async def script(b: _GroupBench, name: str, trigger: TriggerPayload) -> None:
            if name == "beta" and trigger.source == "scheduled":
                b.arm_local("alpha")
                b.arm_local("beta")
            elif name == "beta" and trigger.source == "local":
                b.stop()

        # Act
        await bench.run(script)

        # Assert — both woke at the same instant, in registration order
        assert bench.timeline()[2:] == [
            ("alpha", 0.0, "local"),
            ("beta", 0.0, "local"),
        ]

    async def test_arm_landing_before_the_members_own_batch_run_is_served_by_it(
        self,
    ) -> None:
        """An arm placed mid-batch is satisfied by the run about to happen.

        This is the ungrouped cycle-boundary rule (see
        ``_update_trigger_kwargs``) reaching the group scheduler: the arm
        coalesces into the imminent run rather than queueing a second,
        back-to-back one.
        """
        # Arrange — alpha runs first in the batch and arms beta behind it
        bench = _GroupBench(
            _Member("alpha", interval=1000, triggerable="local"),
            _Member("beta", interval=1000, triggerable="local"),
        )

        async def script(b: _GroupBench, name: str, _trigger: TriggerPayload) -> None:
            if name == "alpha":
                b.arm_local("beta")
            else:
                b.stop()

        # Act
        await bench.run(script)

        # Assert — beta's one and only run reports the arm; no extra cycle
        assert bench.timeline() == [
            ("alpha", 0.0, "scheduled"),
            ("beta", 0.0, "local"),
        ]

    async def test_tick_batches_merge_with_a_released_arm(self) -> None:
        """A member woken at its own tick reports the arm, not "scheduled"."""
        # Arrange — beta's tick is due at t=1 while alpha is armed
        bench = _GroupBench(
            _Member("alpha", interval=1, triggerable="local"),
            _Member("beta", interval=1),
        )

        async def script(b: _GroupBench, name: str, trigger: TriggerPayload) -> None:
            if name == "alpha" and len(b.runs_of("alpha")) == 1:
                b.arm_local("alpha")
            if name == "beta" and len(b.runs_of("beta")) == 2:
                b.stop()

        # Act
        await bench.run(script)

        # Assert — one batch at t=0, alpha's wake, then the shared t=1 tick
        assert bench.timeline() == [
            ("alpha", 0.0, "scheduled"),
            ("beta", 0.0, "scheduled"),
            ("alpha", 0.0, "local"),
            ("alpha", 1.0, "scheduled"),
            ("beta", 1.0, "scheduled"),
        ]

    @pytest.mark.parametrize(
        ("path", "expected_source", "expected_raw"),
        [("mqtt", "mqtt", '{"depth": 3}'), ("local", "local", None)],
    )
    async def test_mqtt_and_local_arms_are_the_same_wake(
        self, path: str, expected_source: str, expected_raw: str | None
    ) -> None:
        """Both paths take the identical scheduler route; only source differs."""
        # Arrange
        bench = _GroupBench(_Member("alpha", interval=1000, triggerable="both"))

        async def script(b: _GroupBench, _name: str, trigger: TriggerPayload) -> None:
            if trigger.source == "scheduled":
                b.arm("alpha", '{"depth": 3}') if path == "mqtt" else b.arm_local(
                    "alpha"
                )
            else:
                b.stop()

        # Act
        await bench.run(script)

        # Assert
        woken = bench.runs_of("alpha")[1]
        assert (woken.source, woken.raw) == (expected_source, expected_raw)

    async def test_arm_on_a_member_excluded_by_a_failing_init_is_inert(self) -> None:
        """A dead member is never scanned for arms, and never spins the loop."""
        # Arrange
        bench = _GroupBench(
            _Member("alpha", interval=1, triggerable="local"),
            _Member("beta", interval=1, triggerable="local"),
        )
        bench.failing_init = "beta"

        async def script(b: _GroupBench, _name: str, _trigger: TriggerPayload) -> None:
            if len(b.runs) == 1:
                b.arm_local("beta")
            if len(b.runs) >= 2:
                b.stop()

        # Act
        await bench.run(script)

        # Assert — beta never runs; alpha keeps ticking on the shared epoch
        assert bench.runs_of("beta") == []
        assert [(r.at, r.source) for r in bench.runs_of("alpha")] == [
            (0.0, "scheduled"),
            (1.0, "scheduled"),
        ]


class TestTickAlignmentSurvivesATriggeredRun:
    """A wake must not rephase the group's shared epoch (ADR-067).

    Technique: State Transition Testing — the heap entry is the state
    under test, and an out-of-cycle run is the transition that must
    leave it alone.
    """

    async def test_trigger_run_does_not_move_the_next_heartbeat(self) -> None:
        """alpha's tick stays at t=10 despite an extra run at t=0."""
        # Arrange
        bench = _GroupBench(
            _Member("alpha", interval=10, triggerable="local"),
            _Member("beta", interval=10),
        )

        async def script(b: _GroupBench, name: str, trigger: TriggerPayload) -> None:
            if name == "alpha" and len(b.runs_of("alpha")) == 1:
                b.arm_local("alpha")
            if name == "beta" and len(b.runs_of("beta")) == 2:
                b.stop()

        # Act
        await bench.run(script)

        # Assert — had the wake rephased alpha, its tick would be at t=10
        # measured from the wake, and it would no longer coincide with beta
        assert bench.timeline() == [
            ("alpha", 0.0, "scheduled"),
            ("beta", 0.0, "scheduled"),
            ("alpha", 0.0, "local"),
            ("alpha", 10.0, "scheduled"),
            ("beta", 10.0, "scheduled"),
        ]


# =============================================================================
# min_interval= inside a group (ADR-066 x ADR-067)
# =============================================================================


class TestThrottleInsideAGroup:
    """The storm throttle stays per member and defers rather than drops.

    Technique: Decision Table — wake source x window state x tick expiry.
    """

    async def test_burst_inside_the_window_produces_one_trailing_run(self) -> None:
        """Two arms inside a closed window coalesce into one deferred run."""
        # Arrange
        bench = _GroupBench(
            _Member("alpha", interval=1000, triggerable="local", min_interval=5)
        )

        async def script(b: _GroupBench, _name: str, trigger: TriggerPayload) -> None:
            runs = len(b.runs)
            if runs == 1:  # bootstrap tick -> leading edge
                b.arm_local("alpha")
            elif runs == 2:  # leading edge closed the window; burst inside it
                b.arm_local("alpha")
                b.arm_local("alpha")
            else:
                b.stop()

        # Act
        await bench.run(script)

        # Assert — leading edge at t=0, exactly one trailing run at t=5
        assert [(r.at, r.source) for r in bench.runs] == [
            (0.0, "scheduled"),
            (0.0, "local"),
            (5.0, "local"),
        ]

    async def test_heartbeats_never_consume_a_pending_grouped_arm(self) -> None:
        """Ticks inside a closed window run as heartbeats; the arm survives."""
        # Arrange — the window (10 s) spans four 2 s ticks
        bench = _GroupBench(
            _Member("alpha", interval=2, triggerable="local", min_interval=10)
        )

        async def script(b: _GroupBench, _name: str, _trigger: TriggerPayload) -> None:
            runs = len(b.runs)
            if runs in (1, 2):
                b.arm_local("alpha")
            elif b.clock.now() >= 10.0:
                b.stop()

        # Act
        await bench.run(script)

        # Assert — heartbeats at 2/4/6/8 stay "scheduled"; the arm lands at 10
        assert [(r.at, r.source) for r in bench.runs] == [
            (0.0, "scheduled"),
            (0.0, "local"),
            (2.0, "scheduled"),
            (4.0, "scheduled"),
            (6.0, "scheduled"),
            (8.0, "scheduled"),
            (10.0, "local"),
        ]

    async def test_throttle_is_per_member_not_per_group(self) -> None:
        """A throttled member never delays its unthrottled sibling."""
        # Arrange
        bench = _GroupBench(
            _Member("alpha", interval=1000, triggerable="local", min_interval=50),
            _Member("beta", interval=1000, triggerable="local"),
        )

        async def script(b: _GroupBench, name: str, trigger: TriggerPayload) -> None:
            runs = len(b.runs)
            if runs == 1:
                b.arm_local("alpha")  # alpha takes its leading edge at t=0
            elif name == "alpha" and trigger.source == "local":
                b.arm_local("alpha")  # now inside alpha's 50 s window
                b.arm_local("beta")
            elif name == "beta" and trigger.source == "local":
                b.stop()

        # Act
        await bench.run(script)

        # Assert — beta runs at t=0, not held behind alpha's window
        assert bench.timeline() == [
            ("alpha", 0.0, "scheduled"),
            ("beta", 0.0, "scheduled"),
            ("alpha", 0.0, "local"),
            ("beta", 0.0, "local"),
        ]


# =============================================================================
# End-to-end through the real app wiring
# =============================================================================


class TestGroupedWakeThroughTheApp:
    """The slot mapping must actually reach the group scheduler task.

    Technique: Integration Testing — ``_start_telemetry_tasks`` builds the
    group task, so a bench that calls ``run_telemetry_group`` directly
    cannot prove the handoff.
    """

    async def test_notifier_wakes_a_grouped_member_end_to_end(self) -> None:
        """An EntityNotifier call publishes a grouped member's state.

        ``publish=OnChange()`` is what makes this an assertion about the
        wake rather than about the heartbeat: the woken payload differs
        from the ticked one, so it can only appear if the notifier
        reached the group scheduler.
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
            "alpha",
            interval=3600,
            group=_GROUP,
            triggerable="local",
            publish=OnChange(),
        )
        async def alpha(trigger: TriggerPayload) -> dict[str, object]:
            if trigger.source == "local":
                woken.set()
                return {"value": "woken"}
            return {"value": "tick"}

        @harness.app.telemetry("beta", interval=3600, group=_GROUP, publish=OnChange())
        async def beta() -> dict[str, object]:
            return {"value": "beta"}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/alpha/state"):
                await asyncio.sleep(0.01)
            holder[0].notify("alpha")
            await asyncio.wait_for(woken.wait(), timeout=5.0)
            while len(harness.mqtt.get_messages_for("testapp/alpha/state")) < 2:
                await asyncio.sleep(0.01)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert — the wake published once, and the sibling never repeated
        payloads = [m[0] for m in harness.mqtt.get_messages_for("testapp/alpha/state")]
        assert payloads[0] == '{"value":"tick"}'
        assert payloads.count('{"value":"woken"}') == 1
        assert len(harness.mqtt.get_messages_for("testapp/beta/state")) == 1

    async def test_notifier_rejects_an_mqtt_only_grouped_member(self) -> None:
        """local_slots() still gates the notifier, group or no group."""
        # Arrange
        harness = AppHarness.create()
        holder: list[_NotifierHolder] = []
        raised: list[Exception] = []

        @harness.app.state
        def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
            state = _NotifierHolder(notify=notify)
            holder.append(state)
            return state

        @harness.app.telemetry("alpha", interval=3600, group=_GROUP, triggerable="mqtt")
        async def alpha() -> dict[str, object]:
            return {"value": 1}

        async def _simulate() -> None:
            while not harness.mqtt.get_messages_for("testapp/alpha/state"):
                await asyncio.sleep(0.01)
            try:
                holder[0].notify("alpha")
            except UnknownEntityError as exc:
                raised.append(exc)
            harness.trigger_shutdown()

        # Act
        _task = asyncio.create_task(_simulate())
        await asyncio.wait_for(harness.run(), timeout=10.0)

        # Assert
        assert len(raised) == 1


# =============================================================================
# The hard constraint: no change to topics, discovery or AsyncAPI
# =============================================================================


class TestGroupedTriggerParity:
    """A trigger source on a group member changes no published contract.

    Technique: Pairwise — trigger source x grouped registration, asserted
    against the byte-identical baseline of the same app without one.
    """

    @pytest.mark.parametrize("source", ["mqtt", "local", "both"])
    def test_asyncapi_output_is_identical(self, source: TriggerSource) -> None:
        """app.asyncapi() ignores triggerable= on a grouped entity."""
        # Arrange & Act
        baseline = _grouped_app(None).asyncapi()
        with_trigger = _grouped_app(source).asyncapi()

        # Assert
        assert with_trigger == baseline

    @pytest.mark.parametrize("source", ["mqtt", "local", "both"])
    async def test_discovery_payloads_are_identical(
        self, source: TriggerSource
    ) -> None:
        """The retained homeassistant/.../config set is unchanged."""
        # Arrange
        config = DiscoveryConfig()
        baseline = await build_discovery_payloads(_grouped_app(None), config)

        # Act
        variant = await build_discovery_payloads(_grouped_app(source), config)

        # Assert
        assert [(p.topic, p.config) for p in variant] == [
            (p.topic, p.config) for p in baseline
        ]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [("mqtt", True), ("both", True), ("local", False), (None, False)],
    )
    def test_set_subscription_follows_the_source_not_the_group(
        self, source: TriggerSource | None, expected: bool
    ) -> None:
        """Grouping adds and removes no ``/set`` topic."""
        # Arrange
        app = _grouped_app(source)
        config = TriggerConfig.build(app._telemetry)

        # Act
        from cosalette._runners._trigger import arms_via_mqtt

        subscribes = arms_via_mqtt(app._telemetry[0].triggerable)

        # Assert
        assert subscribes is expected
        assert ("alpha" in config.slots) is (source is not None)
