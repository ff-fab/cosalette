---
status: Proposed
date: 2026-09-01
impact: moderate
tags: [telemetry, devices, scheduling, mqtt]
---

# ADR-066: Min-interval storm throttle for trigger-initiated runs

## Status

Proposed **Date:** 2026-09-01

## Context

ADR-064 shipped the local trigger source with a documented hole, recorded as its fifth Negative Consequence: *"Storm exposure: coalescing bounds the queue depth, not the handler invocation rate; v1 ships without a min-interval knob. Until `min-interval=` ships, adopters should guard the `notify()` call-site with a same-value dedup... The min-interval follow-up must be tracked by a dedicated bead before implementation begins."* Bead **cos-kkvc** is that bead, and this ADR is the decision it was filed to produce.

**What coalescing does and does not bound.** `_TriggerSlot.arm()` / `arm_local()` (`packages/src/cosalette/_runners/_telemetry_types.py`) store the pending payload and set an `asyncio.Event`. Arms that land while a run is already in flight collapse into that one pending run, so the *queue depth* is bounded at one. The *handler invocation rate* is not bounded at all: a device pushing at 50 Hz produces roughly 50 handler invocations per second, and each one is a full publish cycle — DI resolution, retry wrapper, `state_model=` validation, `publish=` strategy evaluation, persistence write, MQTT publish. Coalescing merges only the arms that arrive during the ~milliseconds a run occupies; a steady push stream lands almost entirely in the gaps.

**Three wake paths, one funnel.** Every wake reaches the handler through a `_TriggerSlot`: an inbound `{prefix}/{name}/set` message via `_register_trigger_proxy` (ADR-036, ADR-064); an in-process `EntityNotifier.__call__` → `arm_local()` (ADR-064), which may be invoked from a non-loop thread via `call_soon_threadsafe`; and, since ADR-065, a `@app.device` handler awaiting `DeviceTrigger.wait()`. ADR-064 reasoned about two wake paths when it deferred storm control; ADR-065 added the third — the device path, where the framework does not own the handler's loop — without revisiting the question. Any throttle that is bolted onto a call site therefore has to be written three times and re-written for every future source.

**Why the existing throttles do not apply.** `Every(seconds=N)` (ADR-013) is a *publish* strategy: it runs after the handler has already executed and it **drops** — a suppressed reading is simply never published, which is correct for "probe fast, publish slow" and wrong here, because a trigger arm represents an event someone asked to be acted on. `publish=OnChange()` likewise runs after the handler and suppresses only the MQTT write, not the read, the validation or the persistence. Neither makes the handler run less often. The current mitigation is the workaround ADR-064 wrote down: every adopting app compares cached state before calling `notify()` — exactly the kind of downstream bookkeeping ADR-064 set out to delete.

**Where the machinery already is.** `TriggerConfig.build()` (`packages/src/cosalette/_wiring/_context.py`) creates one slot per expanded entity name, after `expand_name_specs`, covering both archetypes. Both consumers already hold an injected `ClockPort`: `TelemetryRunner._sleep_or_trigger` sleeps on `ctx._clock`, and `DeviceTrigger` is constructed with `ctx.clock` (`packages/src/cosalette/_runners/_telemetry_runner.py`). The slot itself holds no clock. Grouped telemetry is not affected: `triggerable=` and `group=` are mutually exclusive (`validate_triggerable`), so `run_telemetry_group` never sees a slot.

**One code fact constrains the design.** `TelemetryRunner._update_trigger_kwargs` consumes the slot whenever `event.is_set()`, regardless of what woke the cycle, and its docstring explains why: an unconsumed event makes `_sleep_or_trigger` return `True` forever, producing a tight loop. Today that is safe because a set event always wins the sleep race immediately, so an arm can never outlive a cycle boundary. A throttle deliberately lets an arm stay pending across an interval tick, which puts pressure on exactly that invariant.

**Numbering note.** ADR-036 (Triggerable Telemetry) still exists at `docs/adr/ADR-036-triggerable-telemetry.md`, marked *Superseded by ADR-064*; there is no gap in the ADR sequence and nothing here needs to re-supersede it.

## Decision

Add an opt-in `min_interval=` knob to `@app.telemetry` and `@app.device` that installs a **leading-edge plus trailing-edge throttle on the `_TriggerSlot`**. It bounds the minimum wall-clock spacing between the *starts* of two trigger-initiated runs of the same entity. The default is `None` — the feature is off and behaviour is byte-for-byte what it is today.

**Semantics.**

- *Leading edge:* the first arm after a quiet window (no trigger-initiated run within the last `min_interval` seconds) runs immediately. The common single-push case pays no added latency — the property that motivated ADR-064 in the first place is preserved.
- *Trailing edge:* arms that land inside a closed window coalesce into exactly **one** run, which fires the moment the window opens. Nothing pushed is silently dropped: the last arm before the window closes still produces a run. This is the requirement that rules out plain drop semantics, and it is the explicit contrast with `Every` (ADR-013), which *does* drop, because a publish strategy discards a value that has already been read while a trigger arm is a request that has not yet been served.
- *Worst case:* handler invocations are bounded at `1/min_interval` trigger-initiated runs plus the independent `1/interval` heartbeat, regardless of the push rate.

**The throttle lives on the slot, not at the call sites.** All three wake paths — MQTT `arm()`, local `arm_local()`, and the device path where the handler awaits `DeviceTrigger.wait()` — funnel through one `_TriggerSlot`, so one enforcement point covers all of them, and a fourth source added later inherits the bound for free. Concretely the slot gains `min_interval: float | None` plus a `last_trigger_start: float | None` and two pure methods, `throttle_delay(now) -> float` and `note_trigger_start(now)`. The slot stores **no clock**: each consumer passes `now` from the `ClockPort` it already holds and does its own sleeping, so the clock that measures the window is always the same clock that sleeps it. Arming stays a plain non-blocking `set()` — the gate is on the consuming side, which is what keeps the off-loop `call_soon_threadsafe` arming path (ADR-064) unaffected and non-blocking.

**Interaction with `interval=`.** `min_interval` gates **only** trigger-initiated runs. The `interval=` heartbeat keeps its own schedule; throttling it would erode the liveness guarantee it exists to provide (retained state stays fresh, a dead push subscription is still detected). Concretely, when a slot is armed but throttled, the runner races the remaining throttle delay against the remaining interval: if the interval expires first it performs a normal heartbeat run and the arm **stays pending**. An interval run must not consume a pending arm, because that arm carries a `TriggerPayload` — an MQTT payload and a `source` — that a scheduled run has no way to supply, and consuming it would delete the trailing run the throttle promised. Only trigger-initiated runs update `last_trigger_start`, so the heartbeat neither postpones nor is postponed by the throttle window. The consequence is accepted deliberately: a heartbeat immediately followed by a trailing trigger run can still place two invocations back to back, which is bounded and is not a storm.

**Interaction with `publish=`.** Orthogonal. `min_interval` bounds how often the *handler* is invoked; the publish strategy bounds what is *published*. A throttled run evaluates `OnChange()` / `Every()` / `state_model=` exactly as any other run, and window accounting is based on run **starts** regardless of whether a publish was emitted — so a suppressed publish still costs a window, and the two knobs compose without either having to know about the other.

**Scope and rules.** `min_interval` applies to `"local"`, `"mqtt"` and `"both"` alike — an MQTT `/set` storm has the same shape as a hardware push storm. It requires a trigger source (`min_interval=` without `triggerable=` is a registration-time `ValueError`), must be a positive number, and is excluded from grouped telemetry for free because `triggerable=` and `group=` already are. The window is measured on the injected `ClockPort`, never the wall clock, so `fake_clock` drives every test with no real sleeps. It adds no new public type, no MQTT topic, no AsyncAPI change and no Home Assistant discovery change.

```python
import cosalette

# Telemetry: a bulb that pushes hard; interval= is still the heartbeat.
@app.telemetry(
    name=_bulb_map,
    interval=60,             # heartbeat -- never throttled
    triggerable="local",
    min_interval=1.0,        # at most one trigger-initiated run per second
    publish=cosalette.OnChange(),
)
async def bulb_entity(
    ctx: cosalette.DeviceContext,
    port: WizBulbPort,
    trigger: cosalette.TriggerPayload,
) -> dict[str, object] | None:
    return await read_bulb(ctx, port)


# Devices own their loop, so the gate is inside wait().
@app.device(name=_sensor_map, triggerable="local", min_interval=0.5)
async def sensor_entity(
    ctx: cosalette.DeviceContext,
    cfg: SensorConfig,
    trigger: cosalette.DeviceTrigger,
) -> AsyncIterator[None]:
    while True:
        # Returns no sooner than 0.5 s after the previous wake-driven
        # return; a heartbeat timeout still returns on time.
        await trigger.wait(timeout=60.0)
        ...
        yield


# Timeline for min_interval=1.0, arms at t = 0.0, 0.1, 0.4, 0.9, 2.5:
#   t=0.0  arm -> runs immediately        (leading edge, window opens)
#   t=0.1  arm -> pending
#   t=0.4  arm -> pending (coalesces, replaces the 0.1 payload)
#   t=0.9  arm -> pending (coalesces, replaces the 0.4 payload)
#   t=1.0  ------> ONE run, carrying the t=0.9 payload (trailing edge)
#   t=2.5  arm -> runs immediately        (quiet window elapsed)
# Five arms, two runs, nothing dropped.
```

## Decision Drivers

- Discharge the storm-exposure Negative Consequence ADR-064 shipped with, and delete the call-site same-value dedup workaround it told every adopting app to write.
- Nothing pushed may be silently dropped: the last arm before the window closes must still produce a run, which rules out Every-style drop semantics (ADR-013).
- No added latency for the common single-push case -- the very property ADR-064 existed to buy (wiz2mqtt mean 2.5 s to ~0) must survive the throttle.
- One enforcement point for all wake paths: MQTT arm(), local arm_local() and the ADR-065 DeviceTrigger.wait() path all funnel through _TriggerSlot, and a future source should inherit the bound rather than reimplement it.
- The interval= liveness guarantee must not be eroded -- a heartbeat exists precisely to fire when the push path is dead, so it cannot be gated by a knob that only push traffic winds up.
- Default off: a registration that does not opt in must behave byte-for-byte as it does today, with no new public type and no discovery/AsyncAPI/topic change.
- Testable against fake_clock with no wall-clock sleeps, which means the window must be measured on the injected ClockPort (the Every/_bind precedent, ADR-013).
- The off-loop arming path (EntityNotifier via call_soon_threadsafe, ADR-064) must stay non-blocking and thread-safe -- a throttle that sleeps in the arming call would break it.

## Considered Options

### Option 1: Leading-edge plus trailing-edge throttle on the trigger slot (chosen)

Opt-in `min_interval=` on `@app.telemetry` and `@app.device`, default `None`. `_TriggerSlot` gains `min_interval` and `last_trigger_start` plus two pure methods (`throttle_delay(now)`, `note_trigger_start(now)`); each consumer passes `now` from the `ClockPort` it already holds and sleeps the delay itself. The first arm after a quiet window runs immediately; arms inside a closed window coalesce into exactly one run that fires when the window opens, carrying the last payload. `interval=` runs on its own schedule, are never throttled, and do not consume a pending arm.

- *Advantages:* Nothing is dropped -- the last arm before the window closes still produces a run, which is the property the bead's scope statement made non-negotiable.; No added latency for an isolated push: the leading edge preserves exactly the behaviour ADR-064 was built to deliver.; One enforcement point covers all three wake paths (MQTT arm(), local arm_local(), DeviceTrigger.wait()) because they already funnel through the slot; a fourth source inherits the bound for free.; Arming stays a non-blocking set(), so the off-loop call_soon_threadsafe path from ADR-064 is untouched and no lock is introduced.; The slot holds no clock: the consumer passes now from the ClockPort it already has, so the clock that measures the window is the clock that sleeps it, and fake_clock drives every test with no wall-clock sleep.; Bounded worst case is easy to state and to test: at most 1/min_interval trigger runs plus 1/interval heartbeats, whatever the push rate.; Composes cleanly with publish=: min_interval bounds handler invocations, the strategy bounds publications, and window accounting on run starts means neither knob has to know about the other.; Default None is off, adds no public type, and changes no MQTT topic, AsyncAPI document or Home Assistant discovery payload.
- *Disadvantages:* Turns two delicate straight-line races (_sleep_or_trigger, DeviceTrigger.wait) into bounded loops with a deadline, raising cognitive complexity in the exact code the project's complexity gate watches.; Requires the runner to know why the cycle woke, so a woke_by_trigger flag has to be threaded from _sleep_cycle down to _update_trigger_kwargs, whose current contract is 'always consume a set event'.; A trailing run reads state after the arm that scheduled it, so payload/state pairing is 'latest wins' rather than per-arm.; Adds a public axis to two decorators ADR-041 already warns are wide, across four telemetry entry points and two device entry points.

### Option 2: Do nothing -- keep the ADR-064 call-site dedup workaround

Ship no framework knob. Keep ADR-064's documented mitigation: each adopting app compares cached state before calling `notify()`, so repeated identical pushes never arm the slot at all.

- *Advantages:* Zero framework change, zero new API surface, zero new timing state.; The interval= heartbeat and the publish= strategies keep their current semantics exactly.; Same-value push floods -- the most common storm shape -- really are suppressed at the source, before any framework work happens.; An app can dedup on whatever key it likes, which is strictly more expressive than a single wall-clock bound.
- *Disadvantages:* Leaves in place exactly the downstream bookkeeping ADR-064 set out to remove, and leaves its own Negative Consequence undischarged.; Does not bound anything when consecutive pushes carry genuinely different values -- a 50 Hz varying sensor still produces ~50 full publish cycles per second.; Has to be rewritten at every call site, and the ADR-065 device path added a third one; a call site that forgets it is silently unbounded.; The dedup lives in app code that cannot see the framework's run boundaries, so it cannot express 'at most one run per second' at all -- only 'not the same value twice'.

### Option 3: Pure drop inside the window, like Every (ADR-013)

Arms that land inside a closed window are discarded outright. The slot records the last run time; `arm()` / `arm_local()` become no-ops until the window opens. Mirrors `Every(seconds=N)`'s existing throttle exactly.

- *Advantages:* Simplest possible implementation -- a single comparison in the arming path, no trailing timer, no pending-arm bookkeeping.; Mirrors an existing, already-understood framework throttle (Every), so there is one throttle concept in the codebase rather than two.; Hard bound on invocation rate with no way for a burst to queue work.; No change at all to the consuming side, so _sleep_or_trigger and DeviceTrigger.wait keep their current straight-line shape.
- *Disadvantages:* Silently drops pushed values, which the bead's scope statement explicitly forbids: the last arm before the window closes must still produce a run.; A device that pushes once at t=0 and once at t=0.9 with min_interval=1.0 never publishes the second value until an unrelated heartbeat happens to fire -- the entity reports stale state and looks broken.; Drop is defensible for a publish strategy, which discards a value already read, but not for a trigger arm, which is a request that has not yet been served.; Moves enforcement into the arming path, which is the path that must stay non-blocking and thread-safe.

### Option 4: Pure trailing-edge debounce

Every arm restarts a `min_interval` timer; the run fires only once the arms stop for a full quiet period. The classic debounce used for keystroke and resize handlers.

- *Advantages:* Never drops a value and always runs on the most recent state.; Collapses a burst of any length into exactly one run, which is the strongest possible coalescing.; Conceptually simple and widely recognised -- one timer, restarted on each arm.; No leading/trailing split to reason about, so the state machine is smaller than the chosen option's.
- *Disadvantages:* Adds min_interval of latency to *every* push, including a single isolated one -- it destroys the near-zero push-to-publish latency ADR-064 was built to deliver.; A continuous push stream faster than min_interval starves the entity indefinitely: the timer never expires and the handler never runs, so only the heartbeat publishes anything.; Turns the common case (one push, publish now) into the worst case, inverting the cost model.; Still needs the same pending-arm and timer machinery as the chosen option, so it is not meaningfully cheaper to build.

### Option 5: A single global app-level throttle knob

One `App(min_trigger_interval=...)` setting applied to every triggerable entity in the process, instead of a per-registration argument.

- *Advantages:* One knob to set, one place to document, and no widening of the two decorators ADR-041 already calls overloaded.; An operator can bound a whole process without touching any registration.; Cannot be forgotten on a newly added entity, since it applies by construction.; Same slot-level enforcement and therefore the same coverage of all three wake paths.
- *Disadvantages:* Wrong granularity for the motivating registries: wiz2mqtt has 14 expanded entities on one decorator, but a serial bridge and a bulb in the same app have completely different sane bounds.; The value has to be chosen for the chattiest entity, which needlessly delays every quiet one.; Trigger sources are declared per registration, so a process-wide bound is inconsistent with where the rest of the trigger surface lives.; Does not compose with expanded names -- the natural unit of a trigger slot is one expanded entity, not one app.

### Option 6: Enforce the throttle at the arming call sites

Leave `_TriggerSlot` untouched and add the rate check to each armer: `EntityNotifier.__call__`, the MQTT `_register_trigger_proxy` handler, and the `DeviceTrigger` path, each scheduling its own trailing timer.

- *Advantages:* Requires no change to the two delicate consuming functions (_sleep_or_trigger, DeviceTrigger.wait), so their race logic keeps its current shape.; Each armer can apply a policy suited to its own source, e.g. a different bound for MQTT than for local pushes.; Rejecting a throttled arm early avoids even setting the event, which is marginally cheaper.; Keeps the slot a pure data record with no timing state.
- *Disadvantages:* Writes the same throttle three times and re-writes it for every future source -- exactly the mistake ADR-065 avoided by reusing one slot rather than adding a second trigger path.; EntityNotifier can be armed from a non-loop thread, so a trailing timer there would have to schedule work across threads; the arming path must stay a non-blocking set().; The device path has no arming call site the framework controls -- the wake is consumed inside the handler's own loop -- so it could not be covered at all.; Three enforcement points with three copies of the timing state make 'at most one run per min_interval per entity' impossible to state, let alone test, as a single invariant.

## Decision Matrix

| Criterion | Leading-edge plus trailing-edge throttle on the trigger slot | Do nothing -- keep the ADR-064 call-site dedup workaround | Pure drop inside the window, like Every (ADR-013) | Pure trailing-edge debounce | A single global app-level throttle knob | Enforce the throttle at the arming call sites |
| --- | --- | --- | --- | --- | --- | --- |
| No pushed value is silently dropped | 5 | 4 | 1 | 3 | 5 | 3 |
| Bounds handler invocation rate under a push storm | 5 | 2 | 5 | 4 | 5 | 4 |
| No added latency for an isolated push (leading edge) | 5 | 5 | 5 | 1 | 5 | 5 |
| Preserves the interval= liveness guarantee | 4 | 5 | 5 | 4 | 4 | 5 |
| One enforcement point covering every wake path | 5 | 1 | 4 | 5 | 5 | 1 |
| Per-entity tuning for mixed-rate registries | 5 | 5 | 5 | 5 | 1 | 4 |
| Default-off: no behaviour change for non-adopters | 5 | 5 | 5 | 5 | 4 | 5 |
| Implementation and test cost (5 = cheapest) | 3 | 5 | 5 | 4 | 4 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Discharges the storm-exposure Negative Consequence ADR-064 shipped with. Once implemented, adopting apps delete the same-value dedup ADR-064 told them to write around every notify() call site, and ADR-064 should get a minor amendment recording that its workaround guidance is retired.
- One enforcement point covers all three wake paths -- MQTT arm(), local arm_local() and the ADR-065 DeviceTrigger.wait() path -- because they already funnel through a single _TriggerSlot. A fourth trigger source added later inherits the bound without touching the throttle.
- Nothing pushed is silently dropped: the last arm before the window closes still produces a run. This is the explicit behavioural contrast with Every (ADR-013), which drops, and it is what makes the knob safe to apply to an entity whose state must stay correct.
- The common case is unchanged: with a quiet window, the first arm runs immediately, so the near-zero push-to-publish latency ADR-064 was built to deliver survives the throttle.
- The invocation rate is bounded and easy to state: at most 1/min_interval trigger-initiated runs plus 1/interval heartbeat runs per entity, whatever the push rate.
- Default None means the feature is off and behaviour is byte-for-byte today's. No new public type, no MQTT topic change, no AsyncAPI change, no Home Assistant discovery change -- the ADR-059 chain stays untouched.
- The window is measured on the injected ClockPort, following the Every/_bind precedent (ADR-013), so every test drives it with fake_clock and no test needs a wall-clock sleep.
- Arming stays a non-blocking event set(), so EntityNotifier's off-loop call_soon_threadsafe path (ADR-064) keeps working unchanged and no lock is added to a path that crosses threads.
- min_interval and publish= compose without knowing about each other: the first bounds handler invocations (CPU, I/O, persistence), the second bounds what reaches the broker.

### Negative

- A trailing run reads the world *after* the arm that scheduled it, so the payload/state pairing is 'latest wins', not per-arm. For an MQTT /set storm carrying distinct payloads, the coalesced run sees only the last one. Coalescing already had this property, but min_interval widens the window in which it applies from 'while a run is in flight' (milliseconds) to 'up to min_interval'.
- Per-slot timing state and a second sleep path: _TriggerSlot grows two fields, and both TelemetryRunner._sleep_or_trigger and DeviceTrigger.wait become bounded loops with a deadline rather than straight-line races. Both are already delicate concurrency code, and the change has to stay inside the project's cognitive-complexity gate.
- Two consume rules under one roof: because a throttled arm can outlive a cycle boundary, an interval run must not consume it, while with min_interval=None the existing unconditional consume must be preserved exactly. _update_trigger_kwargs therefore has one behaviour for throttled slots and another for unthrottled ones.
- min_interval bounds trigger runs against each other, not against the heartbeat. A heartbeat run immediately followed by a trailing trigger run still puts two handler invocations back to back. This is deliberate -- the alternative erodes the interval= liveness guarantee -- but it means the knob does not read as 'at most one run per min_interval'.
- 'Why did this publish when it did?' gains a third answer. ADR-064 already made it a two-answer question (tick or local wake); a throttled trailing run adds 'a wake that was deferred to the top of the window', with a start time that matches neither the tick schedule nor the push.
- On the device path a throttled arm survives across a heartbeat return, so DeviceTrigger.wait(timeout=...) can return TriggerPayload.scheduled() while a wake is still pending. A handler that reads 'scheduled' as 'nothing arrived' is subtly wrong once min_interval is set.
- Choosing a value is pushed to the app author, and a value that is too large quietly converts a push-driven entity back into a polled one -- min_interval becomes a second, less visible interval= with no framework warning that it has done so.
- The knob adds another axis to two decorators ADR-041 already warns against widening, and it has to be plumbed through six registration entry points (@app.telemetry, app.add_telemetry, Router.telemetry, Router.add_telemetry, @app.device, app.add_device) plus the MCP registry snapshot and the AI-guidance content, any one of which can drift.

_2026-09-01_
