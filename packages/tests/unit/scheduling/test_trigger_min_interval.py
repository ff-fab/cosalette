"""Unit tests for the ``min_interval=`` storm throttle — ADR-066.

Test Techniques Used:
- Boundary Value Analysis: the throttle delay at ``0`` / just inside /
  exactly on / just outside the ``min_interval`` window
- State Transition Testing: quiet -> leading edge -> closed window ->
  trailing edge -> quiet again
- Decision Table: wake source x window state x ``interval=`` expiry ->
  which cycle runs and whether the pending arm survives
- Equivalence Partitioning: the three trigger sources ``"mqtt"`` /
  ``"local"`` / ``"both"`` share one throttle
- Error Guessing: the ``min_interval=None`` regression path and an arm
  landing from a foreign thread via ``call_soon_threadsafe``

Common patterns:
- Every timing assertion is driven by :class:`~cosalette.testing.FakeClock`;
  the module contains no wall-clock sleeps at all.  ``FakeClock.sleep(s)``
  yields once and advances virtual time by ``s``, so a full
  ``run_telemetry`` loop settles deterministically.
- ``_Bench`` drives the real :class:`TelemetryRunner` loop with a scripted
  handler.  The handler records ``clock.now()`` on every invocation, arms
  the slot to simulate wakes landing mid-run, and stops the loop, so each
  test asserts an exact ADR-066 timeline rather than a run count.
- The loop executes a cycle *before* its first sleep, so run #0 in every
  timeline is the bootstrap scheduled poll, not a trigger-initiated run.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest

from cosalette import (
    App,
    DeviceTrigger,
    EntityNotifier,
    Router,
    TriggerPayload,
    TriggerSource,
)
from cosalette._context import DeviceContext
from cosalette._errors import ErrorPublisher
from cosalette._health import HealthReporter
from cosalette._injection import build_injection_plan
from cosalette._registration import _TelemetryRegistration
from cosalette._runners._telemetry_runner import TelemetryRunner
from cosalette._runners._telemetry_types import _TriggerSlot
from cosalette._strategies import OnChange, PublishStrategy
from cosalette._wiring import TriggerConfig
from cosalette._wiring._discovery import DiscoveryConfig, build_discovery_payloads
from cosalette.testing import FakeClock, MockMqttClient, make_settings

pytestmark = pytest.mark.unit

_FIXED_DT = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


# =============================================================================
# Helpers
# =============================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class _Run:
    """One handler invocation: when it started and what woke it."""

    at: float
    triggered: bool
    source: str
    raw: str | None


class _Bench:
    """Drive the real telemetry loop with a scripted handler on a FakeClock.

    The handler is a closure the test supplies; it receives this bench and
    the :class:`TriggerPayload` for the cycle.  Arming from inside the
    handler is how a test places a wake *inside* a closed throttle window
    without ever touching a wall clock: the slot is armed while the loop
    is between sleeps, and the throttle gate then decides when it runs.
    """

    def __init__(
        self,
        *,
        interval: float,
        min_interval: float | None,
        triggerable: TriggerSource | None = "mqtt",
        publish: PublishStrategy | None = None,
        value: Callable[[int], dict[str, object]] | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.mqtt = MockMqttClient()
        self.shutdown = asyncio.Event()
        self.slot = _TriggerSlot(event=asyncio.Event(), min_interval=min_interval)
        self.runs: list[_Run] = []
        self._interval = interval
        self._triggerable = triggerable
        self._publish = publish
        self._value: Callable[[int], dict[str, object]] = value or (lambda _i: {"v": 1})

    # -- scripting helpers ---------------------------------------------

    def stop(self) -> None:
        """Ask the loop to exit after the current cycle."""
        self.shutdown.set()

    def advance(self, seconds: float) -> None:
        """Move virtual time forward without sleeping (simulates work)."""
        self.clock._time += seconds

    @property
    def trigger_runs(self) -> list[_Run]:
        """Only the trigger-initiated invocations."""
        return [run for run in self.runs if run.triggered]

    async def run(
        self,
        script: Callable[[_Bench, TriggerPayload], Coroutine[Any, Any, None]],
    ) -> None:
        """Run the telemetry loop until the script requests shutdown."""
        bench = self

        async def handler(trigger: TriggerPayload) -> dict[str, object]:
            index = len(bench.runs)
            bench.runs.append(
                _Run(
                    at=bench.clock.now(),
                    triggered=trigger.is_triggered,
                    source=trigger.source,
                    raw=trigger.raw,
                )
            )
            await script(bench, trigger)
            return bench._value(index)

        ctx = DeviceContext(
            name="gadget",
            settings=make_settings(),
            mqtt=self.mqtt,
            topic_prefix="test",
            shutdown_event=self.shutdown,
            adapters={},
            clock=self.clock,
        )
        reg = _TelemetryRegistration(
            name="gadget",
            func=handler,
            injection_plan=build_injection_plan(handler),
            interval=self._interval,
            triggerable=self._triggerable,
            min_interval=self.slot.min_interval,
            publish_strategy=self._publish,
        )
        runner = TelemetryRunner(None)
        await asyncio.wait_for(
            runner.run_telemetry(
                reg,
                ctx,
                ErrorPublisher(
                    mqtt=self.mqtt, topic_prefix="test", clock=lambda: _FIXED_DT
                ),
                HealthReporter(
                    mqtt=self.mqtt,
                    topic_prefix="test",
                    version="0.0.0",
                    clock=self.clock,
                ),
                self.slot,
            ),
            timeout=5.0,
        )

    def state_payloads(self) -> list[str]:
        """Every state publish the loop emitted, in order."""
        return [
            payload
            for topic, payload, _retain, _qos in self.mqtt.published
            if topic.endswith("/gadget/state")
        ]


# =============================================================================
# Slot-level throttle arithmetic
# =============================================================================


class TestThrottleArithmetic:
    """Pure ``_TriggerSlot`` window arithmetic.

    Technique: Boundary Value Analysis — the delay is probed at the
    window edges, where an off-by-one would either drop the trailing
    run or emit two runs at the same instant.
    """

    def test_slot_without_min_interval_never_delays(self) -> None:
        """min_interval=None keeps throttle_delay at 0.0 for every now."""
        # Arrange
        slot = _TriggerSlot(event=asyncio.Event())
        slot.last_trigger_start = 100.0

        # Act & Assert
        assert slot.throttle_delay(0.0) == 0.0
        assert slot.throttle_delay(100.0) == 0.0
        assert slot.throttle_delay(1e9) == 0.0

    def test_leading_edge_delay_is_zero_after_a_quiet_window(self) -> None:
        """A slot that has never run a trigger cycle is open immediately."""
        # Arrange
        slot = _TriggerSlot(event=asyncio.Event(), min_interval=1.0)

        # Act & Assert
        assert slot.throttle_delay(0.0) == 0.0

    def test_boundary_arm_exactly_at_window_edge_fires_immediately(self) -> None:
        """BVA: now - last == min_interval is open, not a trailing run."""
        # Arrange
        slot = _TriggerSlot(event=asyncio.Event(), min_interval=1.0)
        slot.note_trigger_start(10.0)

        # Act & Assert — just inside, exactly on, just outside
        assert slot.throttle_delay(10.9) == pytest.approx(0.1)
        assert slot.throttle_delay(11.0) == 0.0
        assert slot.throttle_delay(11.1) == 0.0

    def test_quiet_period_reopens_the_window(self) -> None:
        """A gap longer than min_interval resets the window with no extra state."""
        # Arrange
        slot = _TriggerSlot(event=asyncio.Event(), min_interval=2.0)
        slot.note_trigger_start(0.0)

        # Act
        delay_inside = slot.throttle_delay(0.5)
        delay_after_quiet = slot.throttle_delay(60.0)

        # Assert
        assert delay_inside == pytest.approx(1.5)
        assert delay_after_quiet == 0.0


# =============================================================================
# Loop-level timeline
# =============================================================================


class TestThrottledTelemetryLoop:
    """The ADR-066 timeline as the real telemetry loop produces it.

    Technique: State Transition Testing — every test walks the slot
    through quiet -> leading edge -> closed window -> trailing edge and
    asserts the exact virtual timestamps of the resulting runs.
    """

    async def test_leading_edge_run_fires_immediately_after_quiet_window(
        self,
    ) -> None:
        """The first arm after a quiet window runs at once, with no clock advance."""
        # Arrange
        bench = _Bench(interval=1000.0, min_interval=1.0)

        async def script(b: _Bench, _trigger: TriggerPayload) -> None:
            if len(b.runs) == 1:
                b.slot.arm('{"days": 1}')
            else:
                b.stop()

        # Act
        await bench.run(script)

        # Assert — bootstrap poll then the leading edge, both at t=0
        assert [run.at for run in bench.runs] == [0.0, 0.0]
        assert bench.runs[0].triggered is False
        assert bench.runs[1].triggered is True
        assert bench.slot.last_trigger_start == 0.0

    async def test_burst_inside_window_produces_exactly_one_trailing_run(
        self,
    ) -> None:
        """Four arms inside one window collapse to a single trailing run."""
        # Arrange
        bench = _Bench(interval=1000.0, min_interval=1.0)

        async def script(b: _Bench, _trigger: TriggerPayload) -> None:
            if len(b.runs) == 1:  # bootstrap poll -> open the leading edge
                b.slot.arm('{"n": 0}')
                return
            if len(b.runs) == 2:  # leading edge: storm arrives while busy
                for offset, n in ((0.1, 1), (0.3, 2), (0.5, 3)):
                    b.advance(offset)
                    b.slot.arm(f'{{"n": {n}}}')
                return
            b.stop()

        # Act
        await bench.run(script)

        # Assert — leading edge at t=0, exactly one trailing run at t=1.0
        assert [run.triggered for run in bench.runs] == [False, True, True]
        assert bench.runs[1].at == 0.0
        assert bench.runs[2].at == pytest.approx(1.0)

    async def test_trailing_run_carries_the_last_payload(self) -> None:
        """Coalesced arms keep the most recent payload, not the first."""
        # Arrange
        bench = _Bench(interval=1000.0, min_interval=1.0)

        async def script(b: _Bench, _trigger: TriggerPayload) -> None:
            if len(b.runs) == 1:
                b.slot.arm('{"days": 1}')
                return
            if len(b.runs) == 2:
                b.slot.arm('{"days": 3}')
                b.slot.arm('{"days": 7}')
                return
            b.stop()

        # Act
        await bench.run(script)

        # Assert
        assert bench.runs[1].raw == '{"days": 1}'
        assert bench.runs[2].raw == '{"days": 7}'
        assert bench.runs[2].at == pytest.approx(1.0)

    async def test_quiet_period_reopens_the_window_in_the_loop(self) -> None:
        """An arm after a quiet gap fires at once instead of waiting again."""
        # Arrange
        bench = _Bench(interval=1000.0, min_interval=1.0)

        async def script(b: _Bench, _trigger: TriggerPayload) -> None:
            if len(b.runs) == 1:
                b.slot.arm("{}")
                return
            if len(b.runs) == 2:  # leading edge; go quiet for 5 s, then arm
                b.advance(5.0)
                b.slot.arm("{}")
                return
            b.stop()

        # Act
        await bench.run(script)

        # Assert — no additional delay was slept after the quiet period
        assert bench.runs[2].triggered is True
        assert bench.runs[2].at == pytest.approx(5.0)
        assert bench.slot.last_trigger_start == pytest.approx(5.0)


async def _run_under_permanent_arm_pressure() -> tuple[_Bench, list[bool]]:
    """Drive a 1 s heartbeat against a 10 s throttle with a standing arm.

    Shared by the heartbeat tests so each asserts one property of the
    same timeline rather than re-deriving it.
    """
    bench = _Bench(interval=1.0, min_interval=10.0)
    seen_after_heartbeat: list[bool] = []

    async def script(b: _Bench, trigger: TriggerPayload) -> None:
        if len(b.runs) <= 2:  # bootstrap arms; the leading edge re-arms
            b.slot.arm("{}")
            return
        if not trigger.is_triggered:
            seen_after_heartbeat.append(b.slot.event.is_set())
        if b.clock.now() >= 10.0:
            b.stop()

    await bench.run(script)
    return bench, seen_after_heartbeat


class TestHeartbeatIsNotThrottled:
    """``interval=`` keeps its own deadline while a throttled arm waits.

    Technique: Decision Table — wake source x window state x interval
    expiry.  The heartbeat row must fire on time, must not consume the
    pending arm, and must not move the throttle window.
    """

    async def test_interval_heartbeat_is_not_throttled(self) -> None:
        """A 1 s heartbeat still fires every second inside a 10 s window."""
        # Act
        bench, _armed = await _run_under_permanent_arm_pressure()

        # Assert — bootstrap + leading edge at t=0, then one run per second
        assert [run.at for run in bench.runs] == pytest.approx(
            [0.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        )

    async def test_interval_run_does_not_consume_a_pending_arm(self) -> None:
        """Heartbeat runs see scheduled(); the arm survives for the trailing run."""
        # Act
        bench, armed_during_heartbeats = await _run_under_permanent_arm_pressure()

        # Assert — every heartbeat is scheduled and leaves the slot armed
        heartbeats = bench.runs[2:-1]
        assert heartbeats, "expected at least one heartbeat run"
        assert all(run.triggered is False for run in heartbeats)
        assert armed_during_heartbeats == [True] * len(heartbeats)
        # ...and the trailing trigger run finally collects it at the window edge
        assert bench.runs[-1].triggered is True
        assert bench.runs[-1].at == pytest.approx(10.0)

    async def test_interval_run_does_not_move_the_throttle_window(self) -> None:
        """last_trigger_start only advances on trigger-initiated runs."""
        # Act
        bench, _armed = await _run_under_permanent_arm_pressure()

        # Assert — the window moved at t=0 (leading) and t=10 (trailing) only
        assert bench.slot.last_trigger_start == pytest.approx(10.0)
        assert bench.runs[-1].at == pytest.approx(bench.slot.last_trigger_start)


# =============================================================================
# publish= is orthogonal to the throttle
# =============================================================================


async def _run_burst_with_onchange(
    values: Callable[[int], dict[str, object]],
) -> _Bench:
    """Leading edge plus one trailing run, under ``publish=OnChange()``."""
    bench = _Bench(
        interval=1000.0,
        min_interval=1.0,
        publish=OnChange(),
        value=values,
    )

    async def script(b: _Bench, _trigger: TriggerPayload) -> None:
        if len(b.runs) == 1:
            b.slot.arm("{}")
            return
        if len(b.runs) == 2:
            b.slot.arm("{}")
            return
        b.stop()

    await bench.run(script)
    return bench


class TestPublishStrategyIsOrthogonal:
    """Window accounting is on run *starts*, not on emitted publishes.

    Technique: Decision Table — publish suppressed / emitted x throttle
    window open / closed.  A suppressed publish must still spend the
    window, or a storm of unchanged readings would bypass the throttle.
    """

    async def test_onchange_suppressed_publish_still_consumes_the_window(
        self,
    ) -> None:
        """Identical values publish once, yet the window still advances."""
        # Act
        bench = await _run_burst_with_onchange(lambda _i: {"v": 1})

        # Assert — only the first cycle published, but the window moved to t=1
        assert len(bench.state_payloads()) == 1
        assert bench.runs[2].at == pytest.approx(1.0)
        assert bench.slot.last_trigger_start == pytest.approx(1.0)

    async def test_onchange_publishes_normally_on_a_trailing_run(self) -> None:
        """A changed value on the trailing run publishes exactly once."""
        # Act
        bench = await _run_burst_with_onchange(lambda i: {"v": i})

        # Assert — one publish per distinct value, the last from the trailing run
        payloads = bench.state_payloads()
        assert len(payloads) == 3
        assert '"v":2' in payloads[-1].replace(" ", "")
        assert bench.runs[2].at == pytest.approx(1.0)


# =============================================================================
# All three trigger sources share one throttle
# =============================================================================


class TestEveryTriggerSourceIsThrottled:
    """The throttle sits on the slot, so every wake path funnels through it.

    Technique: Equivalence Partitioning — ``"mqtt"``, ``"local"`` and
    ``"both"`` are the three classes of the ``triggerable=`` domain and
    must produce the identical two-run shape.
    """

    @pytest.mark.parametrize(
        ("triggerable", "arms"),
        [
            ("mqtt", ("mqtt", "mqtt", "mqtt")),
            ("local", ("local", "local", "local")),
            ("both", ("mqtt", "local", "mqtt")),
        ],
        ids=["mqtt", "local", "both"],
    )
    async def test_min_interval_applies_to_every_source(
        self, triggerable: TriggerSource, arms: tuple[str, ...]
    ) -> None:
        """A burst on any source collapses into one trailing run at the edge."""
        # Arrange
        bench = _Bench(interval=1000.0, min_interval=1.0, triggerable=triggerable)

        def _arm(b: _Bench, kind: str) -> None:
            if kind == "mqtt":
                b.slot.arm("{}")
            else:
                b.slot.arm_local()

        async def script(b: _Bench, _trigger: TriggerPayload) -> None:
            if len(b.runs) == 1:
                _arm(b, arms[0])
                return
            if len(b.runs) == 2:  # storm inside the closed window
                _arm(b, arms[1])
                _arm(b, arms[2])
                return
            b.stop()

        # Act
        await bench.run(script)

        # Assert
        assert [run.triggered for run in bench.runs] == [False, True, True]
        assert [run.at for run in bench.runs] == pytest.approx([0.0, 0.0, 1.0])
        assert bench.runs[2].source == arms[2]

    async def test_off_loop_arm_is_throttled_and_not_lost(self) -> None:
        """A threadsafe arm inside a closed window still yields one trailing run."""
        # Arrange
        bench = _Bench(interval=1000.0, min_interval=1.0, triggerable="local")
        notifier = EntityNotifier()
        notifier._bind({"gadget": bench.slot})

        async def _arm_off_loop(b: _Bench) -> None:
            thread = threading.Thread(target=notifier, args=("gadget",))
            thread.start()
            thread.join()
            # call_soon_threadsafe only queues the arm; FakeClock.sleep(0)
            # yields to the loop so it runs, without advancing virtual time.
            await b.clock.sleep(0)

        async def script(b: _Bench, _trigger: TriggerPayload) -> None:
            if len(b.runs) == 1:
                await _arm_off_loop(b)
                return
            if len(b.runs) == 2:  # arm from a foreign thread mid-window
                await _arm_off_loop(b)
                return
            b.stop()

        # Act
        await bench.run(script)

        # Assert — ADR-064 thread safety survives the ADR-066 throttle
        assert [run.triggered for run in bench.runs] == [False, True, True]
        assert [run.at for run in bench.runs] == pytest.approx([0.0, 0.0, 1.0])
        assert bench.runs[2].source == "local"


# =============================================================================
# Regression: min_interval=None is today's behaviour
# =============================================================================


async def _run_arm_on_every_cycle(min_interval: float | None) -> _Bench:
    """Arm once per cycle until four runs have happened."""
    bench = _Bench(interval=1000.0, min_interval=min_interval)

    async def script(b: _Bench, _trigger: TriggerPayload) -> None:
        if len(b.runs) >= 4:
            b.stop()
            return
        b.slot.arm("{}")

    await bench.run(script)
    return bench


class TestUnthrottledPathIsUnchanged:
    """``min_interval=None`` must be byte-for-byte today's behaviour.

    Technique: Error Guessing — the likeliest regression is the throttle
    leaking into the default path, so the identical arming script is run
    with and without the feature and the timelines are compared.
    """

    async def test_min_interval_none_matches_todays_behaviour(self) -> None:
        """Without a throttle every arm runs immediately, with no clock advance."""
        # Act
        bench = await _run_arm_on_every_cycle(None)

        # Assert — one run per arm, all at t=0, and the window state stays unused
        assert [run.at for run in bench.runs] == [0.0, 0.0, 0.0, 0.0]
        assert [run.triggered for run in bench.runs] == [False, True, True, True]
        assert bench.slot.last_trigger_start is None

    async def test_the_same_script_is_spaced_out_when_throttled(self) -> None:
        """The identical script under min_interval=1.0 spaces runs one second apart."""
        # Act
        bench = await _run_arm_on_every_cycle(1.0)

        # Assert
        assert [run.at for run in bench.runs] == pytest.approx([0.0, 0.0, 1.0, 2.0])
        assert [run.triggered for run in bench.runs] == [False, True, True, True]


# =============================================================================
# Registration-time validation
# =============================================================================


async def _poll() -> dict[str, object]:
    """Minimal telemetry handler."""
    return {"v": 1}


async def _gadget(trigger: DeviceTrigger) -> AsyncIterator[None]:
    """Minimal triggerable device handler."""
    await trigger.wait()
    yield


async def _plain_gadget() -> AsyncIterator[None]:
    """Device handler with no DeviceTrigger parameter."""
    yield


def _device_handler(kw: dict[str, Any]) -> Callable[..., AsyncIterator[None]]:
    """Pick the handler whose shape matches *kw*.

    ``triggerable=`` and the ``DeviceTrigger`` parameter must agree
    (ADR-065), and that guard runs before the ADR-066 one — so the
    poll-only case has to use a handler without the handle to reach the
    ``min_interval=`` rule at all.
    """
    return _gadget if kw.get("triggerable") else _plain_gadget


def _register_telemetry_decorator(app: App, **kw: Any) -> None:
    app.telemetry("gadget", interval=60.0, **kw)(_poll)


def _register_telemetry_imperative(app: App, **kw: Any) -> None:
    app.add_telemetry("gadget", _poll, interval=60.0, **kw)


def _register_telemetry_deferred(app: App, **kw: Any) -> None:
    # A callable enabled= takes the deferred registration path, which
    # builds the registration through its own code branch.
    app.telemetry("gadget", interval=60.0, enabled=lambda _s: True, **kw)(_poll)


def _register_router_telemetry(app: App, **kw: Any) -> None:
    router = Router(prefix="r")
    router.telemetry("gadget", interval=60.0, **kw)(_poll)
    app.include_router(router)


def _register_device_decorator(app: App, **kw: Any) -> None:
    app.device("gadget", **kw)(_device_handler(kw))


def _register_device_imperative(app: App, **kw: Any) -> None:
    app.add_device("gadget", _device_handler(kw), **kw)


def _register_router_device(app: App, **kw: Any) -> None:
    router = Router(prefix="r")
    router.device("gadget", **kw)(_device_handler(kw))
    app.include_router(router)


_TELEMETRY_ENTRY_POINTS = {
    "app.telemetry": _register_telemetry_decorator,
    "app.add_telemetry": _register_telemetry_imperative,
    "app.telemetry (deferred)": _register_telemetry_deferred,
    "Router.telemetry": _register_router_telemetry,
}
_DEVICE_ENTRY_POINTS = {
    "app.device": _register_device_decorator,
    "app.add_device": _register_device_imperative,
    "Router.device": _register_router_device,
}
_ALL_ENTRY_POINTS = _TELEMETRY_ENTRY_POINTS | _DEVICE_ENTRY_POINTS


class TestMinIntervalValidation:
    """``min_interval=`` is rejected at registration time, never at runtime.

    Technique: Equivalence Partitioning over the argument domain
    (unset / positive / zero / negative / non-numeric / bool) crossed
    with Boundary Value Analysis at zero, plus Error Guessing on the
    "throttle with nothing to throttle" mistake.  Every registration
    entry point is parametrized so a new one cannot skip the rule.
    """

    @pytest.mark.parametrize("entry_point", sorted(_ALL_ENTRY_POINTS))
    def test_min_interval_without_triggerable_raises(self, entry_point: str) -> None:
        """A throttle on a poll-only entity is a registration error."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act & Assert
        with pytest.raises(ValueError, match="requires a trigger source") as exc:
            _ALL_ENTRY_POINTS[entry_point](app, min_interval=5.0)
        message = str(exc.value)
        assert "triggerable=" in message
        assert "mqtt" not in message
        assert "both" not in message

    @pytest.mark.parametrize("bad", [0, 0.0, -1, -0.5, float("inf"), float("nan")])
    def test_min_interval_non_positive_raises(self, bad: float) -> None:
        """BVA: only a finite, strictly positive number of seconds is accepted."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act & Assert
        with pytest.raises(ValueError, match="finite positive number"):
            _register_telemetry_decorator(app, triggerable="local", min_interval=bad)

    @pytest.mark.parametrize("bad", [True, False, "1", [1.0]])
    def test_min_interval_rejects_bool_and_non_numeric(self, bad: object) -> None:
        """A bool is not a duration, and neither is a string or a list."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act & Assert
        with pytest.raises(ValueError, match="must be a number"):
            _register_telemetry_decorator(app, triggerable="local", min_interval=bad)

    @pytest.mark.parametrize("entry_point", sorted(_TELEMETRY_ENTRY_POINTS))
    def test_min_interval_accepted_with_a_trigger_source(
        self, entry_point: str
    ) -> None:
        """A positive throttle alongside triggerable= registers cleanly."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act
        _TELEMETRY_ENTRY_POINTS[entry_point](app, triggerable="local", min_interval=2.5)

        # Assert
        assert app._telemetry[0].min_interval == 2.5

    def test_min_interval_defaults_to_none(self) -> None:
        """Unset means unthrottled — the distinction the validator must keep."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act
        _register_telemetry_decorator(app, triggerable="local")

        # Assert
        assert app._telemetry[0].min_interval is None

    @pytest.mark.parametrize("entry_point", sorted(_DEVICE_ENTRY_POINTS))
    def test_min_interval_accepted_on_a_triggerable_device(
        self, entry_point: str
    ) -> None:
        """Devices carry the same field through their own validator."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act
        _DEVICE_ENTRY_POINTS[entry_point](app, triggerable="local", min_interval=2.5)

        # Assert
        assert app._devices[0].min_interval == 2.5

    def test_min_interval_survives_a_coalescing_group(self) -> None:
        """A grouped member keeps its own throttle window (ADR-067)."""
        # Arrange
        app = App(name="testapp", version="1.0.0")

        # Act
        _register_telemetry_decorator(
            app, triggerable="local", group="g", min_interval=1.0
        )

        # Assert
        assert app._telemetry[0].min_interval == 1.0
        assert app._telemetry[0].group == "g"


class TestMinIntervalReachesTheSlot:
    """The registration field must survive wiring and land on the slot.

    Technique: Integration Testing — the value is only useful if the
    object the runner actually consults carries it.
    """

    def test_min_interval_reaches_the_slot(self) -> None:
        """TriggerConfig.build copies min_interval onto each entity's slot."""
        # Arrange
        reg = _TelemetryRegistration(
            name="gadget",
            func=_poll,
            injection_plan=[],
            interval=60.0,
            triggerable="local",
            min_interval=2.5,
        )

        # Act
        config = TriggerConfig.build([reg], [])

        # Assert
        assert config.slots["gadget"].min_interval == 2.5

    def test_unthrottled_registration_leaves_the_slot_unthrottled(self) -> None:
        """No min_interval= means a slot that behaves exactly as it does today."""
        # Arrange
        reg = _TelemetryRegistration(
            name="gadget",
            func=_poll,
            injection_plan=[],
            interval=60.0,
            triggerable="local",
        )

        # Act
        config = TriggerConfig.build([reg], [])

        # Assert
        assert config.slots["gadget"].min_interval is None
        assert config.slots["gadget"].throttle_delay(1e9) == 0.0


# =============================================================================
# Parity: the throttle is invisible to the emitted contracts
# =============================================================================


def _parity_app(min_interval: float | None) -> App:
    """Build an identical triggerable app that differs only in its throttle."""
    app = App(name="testapp", version="1.0.0")

    @app.telemetry(
        "sensor", interval=30, triggerable="local", min_interval=min_interval
    )
    async def _sensor() -> dict[str, object]:  # pragma: no cover
        return {"celsius": 21.5}

    return app


class TestMinIntervalParity:
    """``min_interval=`` is a runtime concern, not a published contract.

    Technique: Comparison Testing — the ADR-054 AsyncAPI document and the
    ADR-059 discovery payloads must be byte-identical with and without a
    throttle, exactly as ADR-064 requires for ``triggerable=`` itself.
    """

    def test_asyncapi_output_is_unchanged_by_min_interval(self) -> None:
        """app.asyncapi() ignores the throttle entirely."""
        # Arrange
        baseline = _parity_app(None)
        variant = _parity_app(2.5)

        # Act & Assert
        assert variant.asyncapi() == baseline.asyncapi()

    async def test_discovery_payloads_are_unchanged_by_min_interval(self) -> None:
        """Generated HA discovery topics and configs are unchanged."""
        # Arrange
        config = DiscoveryConfig()
        baseline = await build_discovery_payloads(_parity_app(None), config)
        variant = await build_discovery_payloads(_parity_app(2.5), config)

        # Act
        as_pairs = [(p.topic, p.config) for p in variant]

        # Assert
        assert as_pairs == [(p.topic, p.config) for p in baseline]
