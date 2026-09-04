---
status: Accepted
date: 2026-09-04
impact: moderate
tags: [testing, scheduling, telemetry]
---

# ADR-071: Test Clock Doubles for Tick and Throttle Timing Assertions

## Status

Accepted **Date:** 2026-09-04

## Context

The framework ships one clock double, `FakeClock`, and it cannot express the timing assertion its consumers most often need: that a scheduled tick did *not* fire.

**This decision sits under ADR-007's testing strategy.** ADR-007 chose a framework-maintained `cosalette.testing` module with a pytest plugin, and lists `FakeClock` in its component table as the test double for "deterministic time control", auto-registered as the `fake_clock` fixture. It rejected the fixtures-only option precisely because copied fixtures drift across projects and a bug fix then has to be applied in eight-plus places. ADR-007 also accepted, as a recorded negative consequence, that "projects that need highly custom test setups may outgrow the provided fixtures" — and the four hand-rolled clock subclasses downstream are that consequence arriving. Adding a second double to `cosalette.testing` is therefore the mechanism ADR-007 already chose, applied to a case it anticipated; it is not a new mechanism.

**ADR-013 set the precedent that the measuring clock is the injected clock.** Its `Every(seconds=N)` publish strategy "requires a `ClockPort` dependency for testability (injected by the framework, not the user)", supplied through `PublishStrategy._bind(clock)`, and ADR-013 records the payoff as "clock injection enables tests without asyncio or real time". ADR-066 cites that same Every/`_bind` precedent twice — as the design criterion that the `min_interval=` window "must be measured on the injected `ClockPort`", never the wall clock, "so `fake_clock` drives every test with no real sleeps". The precedent is not in question here. What is in question is whether the double on the other end of that injection is good enough to hold a sleep back, and today it is not.

**`FakeClock.sleep()` does not wait.** Its body is `await asyncio.sleep(0)` followed by `self._time += seconds` (`packages/src/cosalette/testing/_clock.py:36-46`). There is no deadline and no gate — the sleep resolves in a single event-loop iteration and then reports that the requested duration has passed.

**Three runner sites race a clock sleep against a real `asyncio.Event`.** `_runners/_device_trigger.py:170-189` (`_wake_before`), `_runners/_telemetry_runner.py:627-670` (`_race_sleep_and_trigger`) and `_runners/_telemetry_runner.py:867-902` (`_sleep_until_wake`) each await a clock sleep concurrently with an event wait, and take whichever resolves first. A sleep that resolves in one loop iteration wins all three unconditionally, for any duration. The race is not close; it is decided before the event has a chance.

**Measured at the public `AppHarness` surface, a long interval does not bound anything.** A telemetry loop registered with `interval=3600` free-runs to 215 scheduled ticks, 1,077 publishes and 825,720 virtual seconds in roughly 0.2 s of wall clock. No `interval=` value makes a scheduled tick unreachable, because the sleep that is supposed to hold the loop back never actually holds.

**The consequence is an asymmetry in what a test can prove.** A test can prove a run *was* trigger-initiated, via `TriggerPayload.is_triggered`. It cannot prove the *absence* of a scheduled tick, and it cannot assert an exact publish count — that count is an artifact of how many event-loop yields the test happens to burn, observed between 7 and 18 across boot spin counts of 5 to 50. The idioms that consumers reach for, `min_interval=` windows and trigger-versus-tick discrimination, are exactly the ones built on the assertion that cannot be written.

**Exact virtual timelines are achievable today, but only white-box.** The ADR-066 suite does assert exact virtual timelines across 47 tests — through the `_Bench` rig, whose scripted handler calls `stop()` to bound the loop. Without that call the same rig runs 10,969 cycles in 0.5 s. The bound comes from the scripted handler, not from the clock, and an `AppHarness` consumer has no way to reuse that mechanism.

**Virtual time is a shared accumulator, so multi-task timelines are unfaithful.** `FakeClock._time` is a single counter that every concurrent task's sleep adds to. An `interval=3600` loop produced run timestamps spaced 3,840 apart — a concurrent health reporter's sleeps leaking into the telemetry loop's timeline.

**The gap was raised upstream by a consumer who had already worked around it four times.** The early adopter at github.com/ff-fab/cosalette-apps had accumulated four hand-rolled `RealSleepClock` subclasses, each one writing `FakeClock._time` directly, before filing the proposals that prompted this decision.

## Decision

Add `ManualClock` to `cosalette.testing` as a sibling of `FakeClock` — a gating clock with per-sleeper deadlines whose `sleep()` never self-completes, plus `advance()` and `settle()` — and add a public `advance(seconds)` to the clock doubles, because a test must be able to assert the absence of a scheduled tick without spending wall-clock time.

`ManualClock.sleep()` registers a per-sleeper deadline and blocks until time is moved past it; nothing but an explicit `advance(seconds)` can complete it. `settle()` drains the loop so that "nothing further happened" becomes an observable state rather than a guess. Because `ClockPort` is a structural Protocol, no framework code changes to accept it, and the ADR-013 injection path that ADR-066 measures its window on is reused unchanged.

`FakeClock` keeps its name and its behaviour unchanged: it remains the default double for tests that only need virtual elapsed time, and every existing test that depends on its self-completing sleep continues to behave exactly as before. The public `advance(seconds)` gives the already-documented `_time` mutation a supported name on both doubles.

A real-sleeping clock double is not shipped. Wall-clock waiting is what ADR-066 recorded a design constraint against, and every use case cited for it is served better by `ManualClock`.

## Decision Drivers

- Asserting the absence of a scheduled tick is impossible today at the public `AppHarness` surface, and that is the assertion the `min_interval=` and trigger-versus-tick idioms depend on
- ADR-013 established that time-dependent behaviour is measured on the injected `ClockPort` and not the wall clock — the `Every`/`_bind` precedent — and ADR-066 built its `min_interval=` window on exactly that precedent; a new double must honour it, which rules out a real-sleeping clock and leaves a gating clock as the only way to hold a sleep back deterministically
- ADR-007 chose a framework-maintained `cosalette.testing` module so that test doubles do not drift across consumer projects, and named `FakeClock` as its clock double; a second double belongs in that module rather than in eight copies downstream
- ADR-066 records "testable against fake_clock with no wall-clock sleeps" as an accepted design criterion, and its `_TriggerSlot` deliberately holds no clock so that the measuring clock is the sleeping clock — a real-sleeping double argues against a decision already taken
- `FakeClock` is load-bearing for a large number of passing tests, and COS-rq3 deliberately made its `sleep()` advance virtual time to fix an elapsed-time mismatch; retargeting the name would undo a considered fix
- `_time` is de facto public API — documented as settable, demonstrated in its own docstring, taught in `docs/guides/testing.md`, used by the framework's own `_Bench` rig, and subclassed by four downstream copies — so it needs a supported name
- Wall-clock timing margins are chosen empirically and flake on loaded CI runners; the downstream copies already carry `@pytest.mark.slow` and a 0.4 s window their own comment describes as picked to "dwarf the loop overhead"
- A gating clock was prototyped during evaluation and produced the exact minimal timeline `FakeClock` cannot, needing no framework change because `ClockPort` is a structural Protocol

## Considered Options

### Option 1: Documentation only, keep FakeClock as the sole double

Ship no new clock double. Document the race asymmetry in the `FakeClock` docstring, `docs/guides/testing.md` and `cosalette ai help testing`, so that test authors know a scheduled tick can always fire and write their assertions accordingly — proving what did happen rather than what did not.

- *Advantages:* No new public API to design, support or version; Zero maintenance cost; Nothing can regress, since no shipped behaviour changes
- *Disadvantages:* Leaves the tick-absence assertion unwriteable at any surface a consumer can reach; Every consumer rediscovers the race asymmetry independently, usually after a confusing test failure; Downstream keeps four duplicated subclasses, each reaching into a private attribute — the fixture drift ADR-007 chose a shared testing module to prevent

### Option 2: Ship RealSleepClock, a FakeClock subclass whose sleep waits real time

Promote the downstream workaround into the framework: a `FakeClock` subclass whose `sleep()` awaits the real event loop for the requested duration, so that a clock sleep genuinely loses the race to an event that fires earlier. Roughly eight lines, exported from `cosalette.testing`.

- *Advantages:* Eight lines of implementation; Ships in a patch release; Removes the four duplicated downstream subclasses immediately; Makes durations genuinely observable, since the race outcome reflects real ordering
- *Disadvantages:* Contradicts ADR-066's recorded no-wall-clock-sleeps constraint; Reintroduces the wall clock as the measuring instrument, against the ADR-013 `Every`/`_bind` precedent that ADR-066 built its window on; Contradicts the shipped `cosalette ai help testing` warning that steers users away from wall-clock approaches; Buys correctness with CI flakiness: timing margins are empirical and degrade on loaded runners; Superseded for every cited use case by the gating clock, so it would ship only to be deprecated

### Option 3: ManualClock gating clock plus a public advance(), FakeClock unchanged (chosen)

Add a second double, `ManualClock`, whose `sleep()` registers a per-sleeper deadline and never self-completes — only an explicit `advance(seconds)` releases it — together with `settle()` to drain the loop to quiescence, and a public `advance(seconds)` on the clock doubles. `FakeClock` is untouched.

- *Advantages:* Makes tick absence and exact publish counts assertable at the public `AppHarness` surface; Deterministic and instant, with no wall-clock cost and no empirical timing margins; Keeps the injected `ClockPort` as the measuring instrument, so the ADR-013 precedent and ADR-066's window arithmetic are exercised rather than bypassed; Per-sleeper deadlines also remove the shared-accumulator skew in multi-task timelines; Purely additive: no existing test changes behaviour; Gives the documented `_time` mutation a supported public name
- *Disadvantages:* Quiescence is not directly observable in asyncio, so `settle()` must drain heuristically with a bounded retry; A third clock type increases the choice burden on test authors; More implementation and maintenance cost than either alternative

## Decision Matrix

| Criterion | Documentation only, keep FakeClock as the sole double | Ship RealSleepClock, a FakeClock subclass whose sleep waits real time | ManualClock gating clock plus a public advance(), FakeClock unchanged |
| --- | --- | --- | --- |
| Can assert the absence of a scheduled tick | 1 | 4 | 5 |
| Determinism on a loaded CI runner | 5 | 1 | 5 |
| Wall-clock cost | 5 | 1 | 5 |
| Backward compatibility with existing suites | 5 | 5 | 5 |
| Consistency with the ADR-066 no-wall-clock constraint | 5 | 1 | 5 |
| Fidelity to the ADR-013 injected-ClockPort precedent | 4 | 1 | 5 |
| Cross-project consistency of test doubles (ADR-007) | 1 | 4 | 5 |
| Implementation and maintenance cost | 5 | 5 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- A test written against the public `AppHarness` surface can assert that no scheduled tick fired, and can assert an exact publish count, without wall-clock sleeps or white-box access to runner internals
- `settle()` turns "no publish yet" into a real negative assertion, replacing `assert count >= 2` with `assert count == 2`
- The ADR-013 `Every`/`_bind` precedent is extended rather than weakened: time-dependent behaviour stays measured on the injected `ClockPort`, and the double at the other end can now hold a sleep back, so ADR-066's `min_interval=` window is provable on the same clock that sleeps it
- The new double lands inside the `cosalette.testing` module ADR-007 chose for exactly this purpose, so the four downstream copies converge on one framework-maintained implementation instead of drifting further
- Per-sleeper deadlines eliminate the shared-accumulator skew that makes multi-task virtual timelines unfaithful
- `advance()` gives the documented `_time` mutation a supported public name and removes the downstream private-attribute dependency
- ADR-066's `min_interval=` window becomes provable at the public surface with a mutation guard, not only through the white-box `_Bench` rig
- `FakeClock` is untouched, so no existing test changes behaviour

### Negative

- Quiescence is not directly observable in asyncio, so `settle()` drains by yielding until neither the waiter set nor the ready set changes, with a bounded retry and a loud failure at the bound; a pathological task graph can still defeat it
- Test authors now choose between three clock doubles, and the docs must make the choice obvious or the new one will be missed
- ADR-007's recorded cost — that `cosalette.testing` must evolve alongside framework internals — grows by one more double to maintain
- An `AutoJumpClock` in the style of anyio's `autojump_clock` is deliberately deferred to a spike, because `pytest-asyncio` exposes no supported loop-idle hook and the only known implementation reaches for the loop's private `_ready` queue
- The early adopter's immediate duplication problem is not solved by this ADR alone; their interim fix is `TriggerPayload.is_triggered` plus a shared in-repo fixture

_2026-09-04_
