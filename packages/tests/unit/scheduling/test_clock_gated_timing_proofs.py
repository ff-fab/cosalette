"""Clock-gated timing proofs against the public AppHarness surface (cos-cali.6).

These tests exist to prove that the three runner sleep/trigger race sites are
*observable* under a gating :class:`ManualClock`, driven only through the public
``AppHarness`` API — no white-box ``_Bench`` rig, no wall-clock sleeps, and no
``@pytest.mark.slow``.  They are the harness-level counterpart to the white-box
``test_trigger_min_interval.py`` suite (ADR-066) and the ``ManualClock`` unit
tests (ADR-071).

Test Techniques Used:
- Specification-based Testing: the ADR-066 leading/trailing throttle contract
  and the ADR-071 "a gated sleep never self-completes" guarantee.
- Mutation Testing: the ``min_interval`` proofs fail when the knob is dropped
  from the registration — a paired "no throttle" test pins the contrast, and
  the parametrized ``removed`` case asserts the same body breaks without it.
- Boundary Value Analysis: the throttle window edge (``advance_time`` to exactly
  ``min_interval``) and the tick deadline.
- Comparison Testing: tick absence is asserted as an exact publish count that is
  independent of how many boot event-loop yields the test happens to burn.

Race sites covered (from the cos-cali epic):
- ``_telemetry_runner.py`` ``_race_sleep_and_trigger`` — ungrouped triggerable
  telemetry.
- ``_telemetry_runner.py`` ``_sleep_until_wake`` — the grouped (ADR-067) path.
- ``_device_trigger.py`` ``_wake_before`` — the ``@app.device`` path.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from cosalette import DeviceContext, DeviceTrigger, EntityNotifier, TriggerPayload
from cosalette.testing import AppHarness, ManualClock
from tests.fixtures.notifier import _NotifierHolder

pytestmark = pytest.mark.unit

_STATE = "testapp/sensor/state"
_SET = "testapp/sensor/set"


async def _drain(task: asyncio.Task[None], harness: AppHarness) -> None:
    """Shut the harness down and await the runner.

    ``trigger_shutdown`` sets the event both race sites already await, so the
    parked sleep loses its race *without* any ``advance`` — no virtual time
    moves here and a healthy runner returns in the same event-loop turn.  A
    regression that hangs the runner is caught by the suite-wide ``--timeout``
    (pyproject ``addopts``), which fails naming the test.
    """
    harness.trigger_shutdown()
    await task


# =============================================================================
# Ungrouped triggerable telemetry — _race_sleep_and_trigger / _sleep_or_trigger
# =============================================================================


class TestUngroupedTelemetryGate:
    """The ungrouped triggerable-telemetry race site under ManualClock."""

    @pytest.mark.parametrize("boot_yields", [0, 5, 20])
    async def test_scheduled_tick_stays_gated_regardless_of_boot_spins(
        self, boot_yields: int
    ) -> None:
        """No ``interval=`` tick fires until the test advances — spin-count free.

        Technique: Comparison Testing — under the old self-completing
        ``FakeClock`` the observed publish count was an artefact of how many
        event-loop yields boot happened to burn (7–18).  Here the count is
        exactly one (the startup run) for every extra-yield budget, because the
        gated ``clock.sleep(interval)`` never self-completes.
        """
        clock = ManualClock()
        harness = AppHarness.create(clock=clock)

        @harness.app.telemetry("sensor", interval=3600, triggerable=True)
        async def sensor() -> dict[str, object]:
            return {"n": len(harness.messages_for(_STATE)) + 1}

        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_STATE, 1)
            for _ in range(boot_yields):
                await asyncio.sleep(0)
            await clock.settle()

            assert len(harness.messages_for(_STATE)) == 1
        finally:
            await _drain(task, harness)

    async def test_min_interval_holds_second_trigger_until_window_opens(self) -> None:
        """A trailing trigger is gated by ``min_interval`` and observable as such.

        Technique: Specification-based + Mutation Testing — the leading arm runs
        at once; a second arm inside the closed window coalesces and does *not*
        run until ``advance_time`` reaches the window edge.  Dropping
        ``min_interval=`` from the registration makes the second arm run
        immediately, so the mid-test ``== 2`` assertion fails — see
        :meth:`test_without_min_interval_second_trigger_runs_immediately` for
        the pinned contrast.
        """
        clock = ManualClock()
        harness = AppHarness.create(clock=clock)

        @harness.app.telemetry(
            "sensor", interval=3600, triggerable="mqtt", min_interval=10.0
        )
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            return {"triggered": trigger.is_triggered}

        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_STATE, 1)  # startup (scheduled)

            # Leading edge: the first arm after a quiet window runs immediately.
            await harness.mqtt.deliver(_SET, "")
            await harness.wait_for_publish_count(_STATE, 2)

            # Trailing edge: a second arm inside the window stays pending.
            await harness.mqtt.deliver(_SET, "")
            await clock.settle()
            assert len(harness.messages_for(_STATE)) == 2

            # The window opens exactly at min_interval — the trailing run fires.
            await harness.advance_time(10.0)
            await harness.wait_for_publish_count(_STATE, 3)

            assert len(harness.messages_for(_STATE)) == 3
        finally:
            await _drain(task, harness)

    async def test_without_min_interval_second_trigger_runs_immediately(self) -> None:
        """The mutation-guard contrast: no throttle means no held trailing run.

        Technique: Mutation Testing — identical to the throttle proof but with
        ``min_interval`` absent.  Both arms run without any ``advance``, which
        is exactly the behaviour that makes the throttle test's ``== 2``
        assertion fail if the knob is ever removed.
        """
        clock = ManualClock()
        harness = AppHarness.create(clock=clock)

        @harness.app.telemetry("sensor", interval=3600, triggerable="mqtt")
        async def sensor(trigger: TriggerPayload) -> dict[str, object]:
            return {"triggered": trigger.is_triggered}

        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_STATE, 1)
            await harness.mqtt.deliver(_SET, "")
            await harness.wait_for_publish_count(_STATE, 2)
            await harness.mqtt.deliver(_SET, "")
            await harness.wait_for_publish_count(_STATE, 3)

            assert len(harness.messages_for(_STATE)) == 3
        finally:
            await _drain(task, harness)


# =============================================================================
# Triggerable @app.device — DeviceTrigger.wait / _wake_before
# =============================================================================

_GADGET_STATE = "testapp/gadget/state"


def _capture_notifier(harness: AppHarness, sink: list[_NotifierHolder]) -> None:
    """Register a lifespan state that captures the injected ``EntityNotifier``."""

    @harness.app.state
    def notifier_holder(notify: EntityNotifier) -> _NotifierHolder:
        holder = _NotifierHolder(notify=notify)
        sink.append(holder)
        return holder


class TestDeviceTriggerGate:
    """The ``@app.device`` wake race site (``_wake_before``) under ManualClock."""

    @pytest.mark.parametrize("boot_yields", [0, 5, 20])
    async def test_heartbeat_timeout_stays_gated_regardless_of_boot_spins(
        self, boot_yields: int
    ) -> None:
        """A ``trigger.wait(timeout=…)`` heartbeat cannot fire until time moves.

        Technique: Comparison Testing — the device parks in ``_wake_before``,
        racing a gated ``clock.sleep(timeout)`` against the wake event.  With no
        wake and no ``advance``, the publish count stays at the single startup
        run for any boot-yield budget.
        """
        clock = ManualClock()
        harness = AppHarness.create(clock=clock)

        @harness.app.device("gadget", triggerable="local")
        async def gadget(
            ctx: DeviceContext, trigger: DeviceTrigger
        ) -> AsyncIterator[None]:
            count = 0
            await ctx.publish_state({"n": count})
            while True:
                await trigger.wait(timeout=60.0)
                count += 1
                await ctx.publish_state({"n": count})
                yield

        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_GADGET_STATE, 1)
            for _ in range(boot_yields):
                await asyncio.sleep(0)
            await clock.settle()

            assert len(harness.messages_for(_GADGET_STATE)) == 1
        finally:
            await _drain(task, harness)

    async def test_min_interval_holds_second_wake_until_window_opens(self) -> None:
        """A device wake inside the throttle window is held until it reopens.

        Technique: Specification-based + Mutation Testing — the device-side twin
        of the telemetry throttle proof (ADR-066 enforces ``min_interval`` inside
        :meth:`DeviceTrigger.wait`).  The second notifier wake produces no run
        until ``advance_time`` reaches the window edge; dropping ``min_interval``
        makes it run at once, breaking the mid-test ``== 2`` assertion.
        """
        clock = ManualClock()
        harness = AppHarness.create(clock=clock)
        holders: list[_NotifierHolder] = []
        _capture_notifier(harness, holders)

        @harness.app.device("gadget", triggerable="local", min_interval=10.0)
        async def gadget(
            ctx: DeviceContext, trigger: DeviceTrigger
        ) -> AsyncIterator[None]:
            count = 0
            await ctx.publish_state({"n": count})
            while True:
                await trigger.wait(timeout=3600.0)
                count += 1
                await ctx.publish_state({"n": count})
                yield

        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_GADGET_STATE, 1)
            notify = holders[0].notify

            # Leading edge: the first wake after a quiet window runs at once.
            notify("gadget")
            await harness.wait_for_publish_count(_GADGET_STATE, 2)

            # Trailing edge: a second wake inside the window stays pending.
            notify("gadget")
            await clock.settle()
            assert len(harness.messages_for(_GADGET_STATE)) == 2

            # Window opens at min_interval — the held wake produces its run.
            await harness.advance_time(10.0)
            await harness.wait_for_publish_count(_GADGET_STATE, 3)

            assert len(harness.messages_for(_GADGET_STATE)) == 3
        finally:
            await _drain(task, harness)


# =============================================================================
# Grouped telemetry (ADR-067) — _sleep_until_wake
# =============================================================================

_ALPHA_STATE = "testapp/alpha/state"
_BETA_STATE = "testapp/beta/state"


class TestGroupedTelemetryGate:
    """The coalescing-group wake race site (``_sleep_until_wake``).

    A group with a triggerable member drives ``_await_group_cycle`` through
    ``_sleep_until_wake`` (the ``gs.wake is not None`` branch) rather than the
    plain ``_sleep_until_fire`` path.
    """

    async def test_grouped_tick_stays_gated_until_advance(self) -> None:
        """No grouped ``interval=`` tick fires until the test advances time.

        Technique: Specification-based — both members publish once at the shared
        epoch, then the group parks in ``_sleep_until_wake``.  ``settle`` proves
        the tick is gated; ``advance_time`` to the interval fires it for every
        member in one batch.
        """
        clock = ManualClock()
        harness = AppHarness.create(clock=clock)
        holders: list[_NotifierHolder] = []
        _capture_notifier(harness, holders)

        @harness.app.telemetry("alpha", interval=3600, group="sensors")
        async def alpha() -> dict[str, object]:
            return {"n": len(harness.messages_for(_ALPHA_STATE)) + 1}

        @harness.app.telemetry(
            "beta", interval=3600, group="sensors", triggerable="local"
        )
        async def beta() -> dict[str, object]:
            return {"n": len(harness.messages_for(_BETA_STATE)) + 1}

        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_ALPHA_STATE, 1)
            await harness.wait_for_publish_count(_BETA_STATE, 1)
            await clock.settle()
            assert len(harness.messages_for(_ALPHA_STATE)) == 1
            assert len(harness.messages_for(_BETA_STATE)) == 1

            # The shared tick fires both members in one batch.
            await harness.advance_time(3600.0)
            await harness.wait_for_publish_count(_ALPHA_STATE, 2)
            await harness.wait_for_publish_count(_BETA_STATE, 2)

            assert len(harness.messages_for(_ALPHA_STATE)) == 2
            assert len(harness.messages_for(_BETA_STATE)) == 2
        finally:
            await _drain(task, harness)

    async def test_group_trigger_wakes_only_the_armed_member(self) -> None:
        """A notifier wake runs the armed member out of cycle, not its neighbour.

        Technique: Specification-based — the ADR-067 group trigger releases only
        the armed member through ``_sleep_until_wake``; the tick-anchored
        neighbour stays put because no tick has been advanced to.
        """
        clock = ManualClock()
        harness = AppHarness.create(clock=clock)
        holders: list[_NotifierHolder] = []
        _capture_notifier(harness, holders)

        @harness.app.telemetry("alpha", interval=3600, group="sensors")
        async def alpha() -> dict[str, object]:
            return {"n": len(harness.messages_for(_ALPHA_STATE)) + 1}

        @harness.app.telemetry(
            "beta", interval=3600, group="sensors", triggerable="local"
        )
        async def beta() -> dict[str, object]:
            return {"n": len(harness.messages_for(_BETA_STATE)) + 1}

        task = asyncio.create_task(harness.run())
        try:
            await harness.wait_for_publish_count(_ALPHA_STATE, 1)
            await harness.wait_for_publish_count(_BETA_STATE, 1)

            # Arm beta out of cycle; alpha's heartbeat is untouched.
            holders[0].notify("beta")
            await harness.wait_for_publish_count(_BETA_STATE, 2)
            await clock.settle()

            assert len(harness.messages_for(_BETA_STATE)) == 2
            assert len(harness.messages_for(_ALPHA_STATE)) == 1
        finally:
            await _drain(task, harness)
