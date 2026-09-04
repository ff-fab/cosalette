"""Deterministic clock doubles for testing.

Both doubles satisfy ClockPort (PEP 544 structural subtyping) with a
manually controllable time value — no real time dependency:

- :class:`FakeClock` — virtual elapsed time; ``sleep()`` self-completes
  in a single event-loop iteration.
- :class:`ManualClock` — a *gating* clock; ``sleep()`` registers a
  per-sleeper deadline and blocks until :meth:`ManualClock.advance`
  moves virtual time onto it.

They are siblings over an internal base, not sub- and supertype: the
``sleep()`` contracts are deliberately incompatible.  See ADR-071.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Event-loop rounds ``ManualClock.settle()`` will spend before giving up.
_SETTLE_ROUNDS = 100

#: Consecutive unchanged rounds ``settle()`` treats as quiescence by default.
#: Two are demonstrably necessary: the ``asyncio.wait`` race in
#: ``DeviceContext.sleep`` passes through a round in which none of the three
#: observed quantities moves, and one stable round reports it quiescent too
#: early (``test_one_stable_round_under_settles_the_sleep_race``).  The
#: third is arbitrary margin.  No test pins it and none could: *N* plain
#: ``await`` hops defeat any *N*, which is why ``settle(until=...)`` exists
#: for tests needing a guarantee rather than a margin.
_STABLE_ROUNDS = 3

#: Deadline batches ``ManualClock.advance()`` will release before giving up.
_ADVANCE_WAKES = 10_000


@dataclass
class _BaseClock:
    """Shared virtual-time state for the ``cosalette.testing`` clocks.

    Holds the ``now()`` value and the monotonicity guard both doubles
    enforce.  It deliberately declares neither ``sleep()`` nor
    ``advance()``: :class:`FakeClock` self-completes a sleep and advances
    synchronously, :class:`ManualClock` gates a sleep and advances as a
    coroutine, so a shared declaration would promise a contract one of
    them breaks.
    """

    _time: float = 0.0

    def now(self) -> float:
        """Return the manually set time value."""
        return self._time

    @staticmethod
    def _reject_negative(seconds: float) -> None:
        """Raise if *seconds* would move a monotonic clock backwards."""
        if seconds < 0:
            msg = f"advance() requires a non-negative duration, got {seconds!r}"
            raise ValueError(msg)


@dataclass
class FakeClock(_BaseClock):
    """Test double for ClockPort.

    What it cannot measure: :meth:`sleep` advances virtual time with no
    real delay, so it completes in a single event-loop iteration and wins
    any race against a real ``asyncio.Event`` that another task has yet to
    set — whatever duration was requested.  A test therefore cannot use it
    to prove that a scheduled
    tick did *not* fire, and cannot assert an exact publish count (that
    count reflects how many event-loop yields the test happened to burn).
    To tell a trigger-initiated run from a scheduled tick, check
    ``TriggerPayload.is_triggered``.  Reach for :class:`ManualClock` when
    the assertion is about a tick that must *not* fire.  See ADR-071.

    Attributes:
        _time: The current "now" value returned by ``now()``.
            Assign it to set virtual time *absolutely*, or call
            :meth:`advance` to move it forward *relatively*.

    Example::

        clock = FakeClock(42.0)
        assert clock.now() == 42.0
        clock.advance(57.0)
        assert clock.now() == 99.0
    """

    def advance(self, seconds: float) -> None:
        """Move virtual time forward by *seconds*, without sleeping.

        Relative to the current value, unlike assigning ``_time``,
        which sets virtual time absolutely.  Unlike :meth:`sleep` this
        does not yield to the event loop, so no other task gets to run
        — use it to simulate work that consumed time.

        Args:
            seconds: Virtual seconds to add.  ``0`` is a no-op.

        Raises:
            ValueError: If *seconds* is negative.  A monotonic clock
                never runs backwards.

        Example::

            clock = FakeClock(10.0)
            clock.advance(5.0)
            assert clock.now() == 15.0
        """
        self._reject_negative(seconds)
        self._time += seconds

    async def sleep(self, seconds: float) -> None:
        """Advance virtual time by *seconds* with no real delay.

        Allows tests to exercise sleep-dependent code paths
        without wall-clock waiting.  The ``asyncio.sleep(0)``
        yields to the event loop so concurrent tasks interleave
        correctly.
        """
        await asyncio.sleep(0)
        if seconds > 0:
            self._time += seconds


@dataclass(eq=False)
class _Waiter:
    """One registered :meth:`ManualClock.sleep` call."""

    deadline: float
    event: asyncio.Event


@dataclass
class ManualClock(_BaseClock):
    """Gating test double for ClockPort — nothing but you moves time.

    :meth:`sleep` registers a deadline at ``now() + seconds`` and blocks
    on an ``asyncio.Event`` that only :meth:`advance` sets.  A scheduled
    tick therefore *cannot* fire unless the test asks for it, which is
    what makes "no tick happened" an assertable outcome rather than a
    guess — the thing :class:`FakeClock` cannot express.  Deadlines are
    per sleeper, so concurrent tasks do not contribute to each other's
    timelines.

    Use :class:`FakeClock` when a test only needs virtual elapsed time;
    reach for this one when the assertion is about *absence* or about an
    exact count.  See ADR-071.

    Prefer asserting state *after* :meth:`advance` (or after
    ``settle(until=...)``) over asserting absence after a bare
    :meth:`settle`: the bare form is a bounded heuristic and can report a
    still-working task as quiescent.  :meth:`settle` documents exactly
    when.

    Attributes:
        _time: The current "now" value returned by ``now()``.

    Example::

        clock = ManualClock()
        fired: list[float] = []

        async def tick() -> None:
            await clock.sleep(3600)
            fired.append(clock.now())

        task = asyncio.create_task(tick())
        await clock.settle()
        assert fired == []  # nothing releases the sleep but advance()

        await clock.advance(3600)
        await clock.settle(until=lambda: bool(fired))
        assert fired == [3600.0]
        await task
    """

    _waiters: list[_Waiter] = field(default_factory=list, repr=False, compare=False)
    _ops: int = field(default=0, repr=False, compare=False)
    _advancing: bool = field(default=False, repr=False, compare=False)

    async def sleep(self, seconds: float) -> None:
        """Block until :meth:`advance` moves time to ``now() + seconds``.

        Nothing else completes a *positive* sleep: no wall-clock time
        passes, and no number of event-loop iterations releases it.  The
        deadline is captured per call, so a concurrent sleeper's duration
        never leaks into this one's timeline.

        A non-positive *seconds* is the deliberate carve-out to that
        guarantee: it is already elapsed by definition, so it yields to
        the event loop once and returns, exactly like ``asyncio.sleep(0)``.
        The framework's own ``sleep(max(0.0, deadline - now()))`` throttle
        arithmetic depends on this — a gating ``sleep(0)`` would deadlock
        the runners this clock exists to test.  The cost is real: a
        consumer whose deadline has already gone stale computes ``0.0``
        every cycle and free-runs without any :meth:`advance`, so this
        clock does not gate it at all.  Such a loop churns the observed
        operation counter, so :meth:`settle` catches it and raises rather
        than returning a false "quiescent" — but only after the loop has
        run some cycles, and the work those cycles did has really
        happened.

        Args:
            seconds: Virtual seconds to wait.  ``<= 0`` yields only.
        """
        self._ops += 1
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        waiter = _Waiter(self._time + seconds, asyncio.Event())
        self._waiters.append(waiter)
        try:
            await waiter.event.wait()
        finally:
            self._ops += 1
            if waiter in self._waiters:  # cancelled before release
                self._waiters.remove(waiter)

    async def advance(
        self,
        seconds: float,
        *,
        max_wakes: int = _ADVANCE_WAKES,
        stable_rounds: int = _STABLE_ROUNDS,
    ) -> None:
        """Move virtual time forward by *seconds*, releasing sleepers.

        Waiters are released in deadline order, and each observes
        ``now()`` at *its own* deadline rather than at the final target:
        under ``advance(10)`` a sleeper due at ``t+1`` reads ``t+1``.
        Time therefore steps deadline by deadline and lands on the target
        last.  Sleeps registered by the tasks this wakes are honoured too,
        as long as they fall at or before the target.

        Quiescence contract: after each release the event loop is driven
        to quiescence via :meth:`settle` before time moves again, and once
        more after time reaches the target.  So on return every task this
        advance woke has run as far as it can — up to the same heuristic
        limit :meth:`settle` documents, which *stable_rounds* tunes.  This
        method is a coroutine for that reason, unlike
        :meth:`FakeClock.advance`.

        Exactly one advance may be in flight: the target is captured on
        entry and written on exit, so a nested or concurrent call would
        run virtual time backwards when the inner one returned.  A second
        entry raises instead of doing that silently.

        Args:
            seconds: Virtual seconds to add.  ``0`` releases nothing new
                but still settles the loop.
            max_wakes: Deadline batches to release before giving up.
                Guards against a task that sleeps in a tight loop across
                a very large *seconds*.
            stable_rounds: Forwarded to every :meth:`settle` this makes.
                Raise it for a task that takes several plain ``await``
                hops between waking and its observable effect.

        Raises:
            ValueError: If *seconds* is negative.  A monotonic clock
                never runs backwards.
            RuntimeError: If another ``advance()`` is already in flight,
                if *max_wakes* batches are released without reaching the
                target, or if :meth:`settle` gives up.  In the latter two
                cases the clock is left *partially advanced* — time has
                moved to the last deadline released, not to the target —
                so the instance should not be reused after the failure.
        """
        self._reject_negative(seconds)
        self._reject_reentry()
        self._advancing = True
        try:
            target = self._time + seconds
            await self._release_batches(seconds, target, max_wakes, stable_rounds)
            self._time = target
            await self.settle(stable_rounds=stable_rounds)
        finally:
            self._advancing = False

    async def settle(
        self,
        *,
        until: Callable[[], bool] | None = None,
        max_rounds: int = _SETTLE_ROUNDS,
        stable_rounds: int = _STABLE_ROUNDS,
    ) -> None:
        """Drive the event loop forward *without* moving time.

        Two modes, and the difference matters:

        - ``settle()`` — a **bounded heuristic**.  It returns when the
          loop *looks* idle, which is not the same as proving no further
          work is pending.
        - ``settle(until=predicate)`` — a **real wait**.  It returns only
          once *predicate* holds, and raises if it never does.  Use this
          whenever a test depends on some effect having landed.

        Quiescence contract: each round yields once to the event loop and
        compares an observation of (the set of pending tasks, the pending
        sleep deadlines on this clock, and a counter of sleep
        registrations and releases).  Quiescence is declared only after
        *stable_rounds* **consecutive** rounds change none of them,
        because a single quiet round is not enough: an ``asyncio.wait``
        callback chain — the shape all three of the framework's own
        runner sleep sites use — passes through a round in which none of
        the three observable quantities moves.  Virtual time never moves:
        only :meth:`advance` moves it, in either mode.

        The observation is a heuristic, because asyncio exposes no
        supported idle hook and this deliberately does not read the
        loop's private ready queue (ADR-071).  It fails in both
        directions, and both are bounded:

        - **Under-settling, silently.**  A task that only awaits plain
          ``asyncio.sleep(0)`` hops between being woken and producing its
          effect touches none of the three quantities, so it is invisible
          here.  A task taking more such hops than *stable_rounds* is
          reported quiescent while it is still working, and its effect
          lands after this returns.  Prefer asserting the state you
          expect after :meth:`advance` or ``settle(until=...)`` over
          asserting the absence of an effect after a bare ``settle()``;
          raise *stable_rounds* if a specific task needs more room.
        - **Never settling, loudly.**  A task that churns any of the
          three observed quantities forever hits *max_rounds* and raises.

        Args:
            until: Optional predicate.  When given, rounds are spent
                until it returns true (plus one further yield so the task
                that satisfied it can take its next step); *stable_rounds*
                is not consulted.
            max_rounds: Event-loop rounds to spend before giving up.
            stable_rounds: Consecutive unchanged rounds that count as
                quiescence when *until* is not given.

        Raises:
            RuntimeError: If the loop is still churning after
                *max_rounds*, or if *until* never became true within it.
        """
        if until is not None:
            await self._settle_until(until, max_rounds)
            return
        stable = 0
        for _ in range(max_rounds):
            before = self._observe()
            await asyncio.sleep(0)
            stable = stable + 1 if self._observe() == before else 0
            if stable >= stable_rounds:
                return
        msg = (
            f"ManualClock.settle() gave up after {max_rounds} event-loop rounds: "
            "the loop is still scheduling work at the current virtual time. A task "
            "is most likely spinning or creating tasks faster than they finish. "
            "Fix the task under test, or raise max_rounds=."
        )
        raise RuntimeError(msg)

    async def _settle_until(self, until: Callable[[], bool], max_rounds: int) -> None:
        """Yield to the loop until *until* holds, then once more."""
        for _ in range(max_rounds):
            if until():
                await asyncio.sleep(0)
                return
            await asyncio.sleep(0)
        msg = (
            f"ManualClock.settle(until=...) gave up after {max_rounds} event-loop "
            "rounds: the predicate never became true at the current virtual time. "
            "The work is most likely waiting on an advance() this test has not "
            "made, or on something this clock does not gate. Fix the test, or "
            "raise max_rounds=."
        )
        raise RuntimeError(msg)

    async def _release_batches(
        self, seconds: float, target: float, max_wakes: int, stable_rounds: int
    ) -> None:
        """Step time deadline by deadline up to *target*, settling between."""
        for _ in range(max_wakes):
            due = [w.deadline for w in self._waiters if w.deadline <= target]
            if not due:
                return
            self._time = min(due)
            self._release_due()
            await self.settle(stable_rounds=stable_rounds)
        msg = (
            f"ManualClock.advance({seconds!r}) released {max_wakes} deadline "
            "batches without reaching the target time. A task is most likely "
            "sleeping in a tight loop over a very long advance. Shorten the "
            "advance, lengthen the sleep, or raise max_wakes=."
        )
        raise RuntimeError(msg)

    def _reject_reentry(self) -> None:
        """Raise if an ``advance()`` is already in flight."""
        if self._advancing:
            msg = (
                "ManualClock.advance() is already running: a nested or concurrent "
                "advance() captures its own target and would run virtual time "
                "backwards when it returned. Await one advance() at a time — a "
                "sleep registered by a task this advance wakes is already honoured "
                "without a second call."
            )
            raise RuntimeError(msg)

    def _release_due(self) -> None:
        """Wake every waiter whose deadline has been reached."""
        remaining: list[_Waiter] = []
        for waiter in self._waiters:
            if waiter.deadline <= self._time:
                waiter.event.set()
                self._ops += 1
            else:
                remaining.append(waiter)
        self._waiters = remaining

    def _observe(self) -> tuple[object, ...]:
        """Snapshot the quantities :meth:`settle` watches for change."""
        return (
            self._ops,
            tuple(sorted(w.deadline for w in self._waiters)),
            frozenset(asyncio.all_tasks()),
        )
