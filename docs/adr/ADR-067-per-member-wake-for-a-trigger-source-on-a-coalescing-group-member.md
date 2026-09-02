---
status: Accepted
date: 2026-09-02
impact: moderate
tags: [telemetry, scheduling, mqtt, devices]
---

# ADR-067: Per-member wake for a trigger source on a coalescing-group member

## Status

Accepted **Date:** 2026-09-02

## Context

ADR-064 gave `@app.telemetry` a trigger source (`triggerable="mqtt" | "local" | "both"`) and an injectable `EntityNotifier` that arms a per-expanded-name `_TriggerSlot`; ADR-066 added the `min_interval=` storm throttle on that same slot. Both landed with one axis explicitly excluded: *"Narrow the registration guard to allow a trigger source on a root (unnamed) device when it is local-only; `triggerable` + `group=` stays excluded for v1."*

The exclusion was not a capability judgement — it was an undecided-semantics judgement. `group=` (ADR-018) batches several telemetry handlers into one tick-aligned execution window so they can share a serial bus, an SPI interface or a rate-limited API; a trigger arms a *single* expanded entity. Nothing in either record said what the intersection means, and neither of the two motivating downstream apps (wiz2mqtt, jeelink2mqtt) needed it, so v1 rejected the pair at three separate guards rather than guessing.

**The four questions the exclusion left open.** (1) Does arming one member wake only that member, or the whole group? (2) If the whole group, what does the group publish for members whose handlers had no new input, and how does that interact with `publish=OnChange()`? (3) Does `TriggerPayload.source` describe the armed member or every member of the woken group? (4) Does an MQTT `/set` on one member's topic behave the same as a local wake? A fifth question fell out during implementation: does an out-of-cycle run rephase the member's `interval=` heartbeat, as it does on the ungrouped path?

**What the two mechanisms actually are.** A coalescing group is *one* `asyncio.Task` running a heapq of `(fire_time_ms, member_index)` anchored to a shared epoch, so 300 s x 12 == 3600 s exactly and members that share a tick share a batch (`TelemetryRunner.run_telemetry_group`, `packages/src/cosalette/_runners/_telemetry_runner.py`). A trigger source is a per-entity `asyncio.Event` on a `_TriggerSlot`, raced against the sleep by the *ungrouped* per-entity loop (`_sleep_or_trigger`). The group scheduler never looked at slots at all: before this record, lifting the registration guard alone would have produced a registration that type-checks, wires, subscribes its `/set` topic — and never wakes.

**What a group is for.** ADR-018's whole rationale is reducing resource sessions from N to 1 per coinciding tick. That rationale answers question (1) both ways depending on where you point it: waking the whole group is one session for N handler invocations, most with no new input; waking one member is one session for the one invocation that has input. Only the second scales with push rate, which is the regime a trigger source exists to serve.

**Origin.** Framework bead **cos-7ymv**, discovered from cos-3qri (the ADR-064 implementation), filed P4 with no known consumer. The scope it fixed is unchanged here: decide and document the semantics, lift the guard, and disturb neither the ADR-059 runtime Home Assistant discovery path, the MQTT topic layout, nor AsyncAPI output.

## Decision

Give a coalescing-group member a **per-member wake**: arming one member's `_TriggerSlot` runs that member alone, inside the group's shared scheduler, and never rephases anybody's tick.

Every triggerable member of one group is handed the same `asyncio.Event` by `TriggerConfig.build()`, stored as `_TriggerSlot.wake` and raised by `arm()` / `arm_local()` *after* the per-member event. The group scheduler races its next tick deadline against that one event (plus shutdown), clears it before scanning its members, and executes a batch that is the **union of the tick-due members and the members whose pending arm the throttle gate released**. Clearing before scanning can only over-wake, never drop an arm, so the whole group costs two raced tasks per cycle rather than one per member.

This settles the four open questions as follows. **(1)** Only the armed member runs; a sibling is invoked only when its own tick comes due, in which case it joins the same batch. Several members armed at once therefore still share one execution window, which is the group's reason to exist under push load. **(2)** Members with no new input are not invoked at all, so there is nothing for them to publish and `publish=OnChange()` never sees a no-op cycle — a group has never had a group-level payload. **(3)** `TriggerPayload.source` is read from each member's own slot: the armed member sees `"mqtt"` or `"local"`, a sibling batched in by its tick sees `"scheduled"`. **(4)** An MQTT `/set` and an `EntityNotifier` call are the same wake through the same slot; only `source` and `raw` differ, and `arms_via_mqtt()` still decides the subscription set, so grouping adds and removes no topic.

The fifth answer is the one that diverges from the ungrouped path: **a trigger-initiated run does not move the member's heap entry.** The ungrouped loop restarts its sleep after every run, so a wake there rephases the heartbeat. Doing that inside a group would drift one member off the shared epoch permanently and destroy the tick coincidence the group exists to create. The cost is a heartbeat that may fire shortly after a triggered run; `publish=OnChange()` suppresses the duplicate payload, and the run count stays bounded by `1/interval` per member.

`min_interval=` (ADR-066) keeps its per-slot meaning and gains its third consuming end in the group scheduler's sleep, which waits out whichever comes first — the window reopening or this cycle's tick. A throttled member's trailing run is deferred, never dropped; a tick inside a closed window runs as a heartbeat that neither consumes the pending arm nor moves the window.

All three registration guards are lifted: `validate_triggerable()` (which loses its now-unused `group` parameter), the copy in `_expand_telemetry_names()` that re-checked each expanded entity, and the deferred check in `_validate_enabled_telemetry()` for callable `enabled=`.

```python
import cosalette


# Six sensors behind one serial bus.  The bus is the reason for group=;
# the push capability is the reason for triggerable=.  Both now hold.
@app.telemetry(
    name=_sensor_map,
    interval=300,               # heartbeat/fallback, on the shared epoch
    group="optolink",           # shares the bus session at coinciding ticks
    triggerable="local",        # a push wakes THIS sensor, not the group
    min_interval=5,             # ADR-066, still per member
    publish=cosalette.OnChange(),
)
async def sensor(
    ctx: cosalette.DeviceContext,
    port: OptolinkPort,
    trigger: cosalette.TriggerPayload,
) -> dict[str, object] | None:
    # "local"/"mqtt" only ever on the member that was armed;
    # a sibling batched in by its own tick sees "scheduled".
    return await read_one(ctx, port, fresh=trigger.is_triggered)


# The arming side is unchanged from ADR-064.
class BusAdapter:
    def _on_frame(self, sensor_id: str) -> None:
        self._cache[sensor_id] = _parse(sensor_id)
        self._notify(self._name_for(sensor_id))   # wakes that member alone
```

## Decision Drivers

- Answer the four semantic questions ADR-064 deferred, rather than lifting a guard and letting the first adopter discover the semantics by experiment.
- Preserve the property a coalescing group exists for: tick alignment on a shared epoch, which a rephasing wake would permanently destroy for the rephased member.
- Keep the publish path single -- a woken run must reuse the identical handler cycle, so publish=, state_model=, availability, persistence and error publication cannot drift between ticked and woken publications.
- Scale with push rate, not with group size: a wake must cost one handler invocation, not N, or the trigger source makes a grouped app worse than an ungrouped one under load.
- Preserve discovery/AsyncAPI parity: the ADR-059 runtime-discovery chain, the MQTT topic layout and app.asyncapi() output must be byte-identical with and without a trigger source on a group member.
- Add no third throttle enforcement point: min_interval= is already a per-slot contract with two consuming ends, and the group scheduler must become the third consumer rather than a second policy.

## Considered Options

### Option 1: Per-member wake, epoch-preserving (chosen)

Arming a member runs that member alone inside the group scheduler. The scheduler races its tick deadline against one wake event shared by the group's triggerable members; the batch is the union of tick-due members and released arms. A trigger-initiated run leaves the member's heap entry untouched, so interval= heartbeats stay anchored to the shared group epoch. TriggerPayload.source, min_interval= and the /set subscription set all stay exactly per member.

- *Advantages:* Cost of a wake is one handler invocation, so it scales with push rate rather than with group size; Members with no new input are never invoked, so publish=OnChange() never sees a no-op cycle and there is no 'what does an unwoken member publish' question to answer at all; Tick alignment survives: an out-of-cycle run cannot drift a member off the shared epoch, so 300 s x 12 == 3600 s still holds after any number of wakes; A burst that arms several members still collapses into one batch and one adapter session -- the group keeps working as a group under push load; min_interval=, TriggerPayload.source and the /set subscription set keep their exact per-entity ADR-064/066 meanings; the group adds no new policy to any of them; One shared wake event means two raced tasks per cycle regardless of group size, hoisted across cycles like the ungrouped path's trigger task
- *Disadvantages:* A triggered run opens its own resource session rather than waiting for the next shared window -- the same cost the ungrouped triggerable path already pays, but a real cost for a group whose members share a slow bus; Heartbeat behaviour diverges from the ungrouped path (which rephases after every run), so 'when does the next tick fire' now has two answers depending on group membership; A heartbeat may fire shortly after a triggered run; the handler invocation is real even though publish=OnChange() suppresses the duplicate payload; _TriggerSlot gains a field whose lifecycle (set by the slot, cleared only by the scheduler) is a contract that lives in two files

### Option 2: Group wake

Arming any member runs the whole group in one batch, exactly as a coinciding tick does. The armed member's payload is delivered to it; every other member runs with TriggerPayload.scheduled().

- *Advantages:* One resource session per wake, which is the strongest possible reading of ADR-018's session-sharing rationale; Naturally right for hardware where one physical read serves several entities -- a single serial frame carrying six values; The batch composition rule stays identical to the tick path, so the scheduler needs no notion of a partial batch
- *Disadvantages:* Costs N handler invocations per push, N-1 of them with no new input -- the exact waste ADR-064 set out to delete, reintroduced and multiplied by group size; Storm exposure is amplified N-fold, and min_interval= is a per-slot contract that cannot express 'throttle the group', so the throttle would become inconsistent the moment members differ; Makes 'what does an unwoken member publish' a question that must be answered, and every answer is bad: publish stale state, run a handler that has nothing to report, or special-case OnChange(); TriggerPayload.source becomes ambiguous for the N-1 members that were not armed but did wake because of an arm

### Option 3: A wake= policy knob offering both

Ship both semantics behind a new parameter, e.g. wake="member" | "group", and let the registration choose.

- *Advantages:* Serves the one-frame-many-entities hardware shape that per-member wake serves badly; Makes the semantics explicit at the call site rather than implicit in a record
- *Disadvantages:* Adds a fifteenth axis to @app.telemetry, against ADR-041's explicit don't-overload guidance, for a combination the bead itself files at P4 with no known consumer; Two semantics means two scheduler paths to test, document and keep correct against every future change to either mechanism; The group-wake half carries all of that option's disadvantages, now permanently, rather than as a rejected alternative

### Option 4: Keep the exclusion and document the workaround

Leave the three guards in place and document that an author who needs both must drop group= and accept N independent per-entity loops.

- *Advantages:* Zero framework change and zero new semantics to get wrong; Honest about the P4 priority: no downstream app has asked for the combination
- *Disadvantages:* The workaround costs exactly what ADR-018 exists to prevent -- N resource sessions per coinciding tick on a shared bus -- so it is not a workaround so much as a regression the author is asked to accept; Leaves three guards, three error messages and four documentation sites carrying a restriction whose only justification was that nobody had decided the semantics; Pushes the decision onto the first adopter under deadline, which is when it will be decided worst

## Decision Matrix

| Criterion | Per-member wake, epoch-preserving | Group wake | A wake= policy knob offering both | Keep the exclusion and document the workaround |
| --- | --- | --- | --- | --- |
| Answers all four deferred questions unambiguously | 5 | 3 | 4 | 1 |
| Cost of a wake scales with push rate, not group size | 5 | 1 | 3 | 2 |
| Preserves tick alignment on the shared epoch | 5 | 5 | 5 | 5 |
| Reuses min_interval= and TriggerPayload.source unchanged | 5 | 2 | 3 | 5 |
| API surface minimalism (ADR-041 anti-overloading) | 5 | 5 | 2 | 5 |
| Discovery / AsyncAPI / topic parity (ADR-054, ADR-059) | 5 | 5 | 5 | 5 |
| Serves one-physical-read-many-entities hardware | 2 | 5 | 5 | 1 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- The last axis ADR-064 deferred is closed. `triggerable=`, `min_interval=`, `group=`, `schedule=`-vs-`interval=` and root-vs-named devices now compose with exactly one remaining restriction between them (`schedule=` still cannot combine with `group=`, since groups require a shared `interval=`).
- A push-capable app on a shared bus no longer has to choose between event-driven publication and session coalescing. Under load it gets both: one wake runs one member, and simultaneous wakes still collapse into one batch and one adapter session.
- Purely additive at the public surface. Every existing decorator call keeps its meaning, a group with no triggerable member takes the pre-ADR-067 sleep path verbatim, and topics, AsyncAPI output and Home Assistant discovery payloads are asserted byte-identical across `triggerable=False/"mqtt"/"local"/"both"` on a grouped entity.
- The wake is one shared `asyncio.Event` per group, hoisted across cycles alongside the shutdown task, so the scheduler races two tasks per cycle no matter how many members a group has.
- Three duplicated guards and their three error messages are deleted rather than reworded, and `validate_triggerable()` loses a parameter it no longer reads.
- `min_interval=` gains its third consuming end without gaining a third policy: the group scheduler asks the same `throttle_delay()` / `note_trigger_start()` pair the other two ends ask, so a grouped member throttles exactly like an ungrouped one.

### Negative

- Heartbeat phase now depends on group membership: an ungrouped triggerable entity rephases its `interval=` sleep after every run, while a grouped one keeps ticking on the shared epoch. Both behaviours are correct for their scheduler and neither can adopt the other's, but 'when is the next tick' is a two-answer question for a reader who does not know which one they are looking at.
- A triggered run inside a group opens its own resource session instead of waiting for the next shared window. For a group whose members share a slow serial bus, a high push rate therefore erodes the coalescing benefit -- `min_interval=` is the bound, and adopters on genuinely slow buses should set it.
- Hardware where one physical read serves several entities is served badly: each entity must be armed separately, and the six arms that one frame produces coalesce into one batch only if they land while the scheduler is between cycles. The rejected group-wake option was the right shape for that hardware and remains available as a future `wake=` follow-up if a real consumer appears.
- `_TriggerSlot.wake` is a field the slot sets and only the group scheduler clears. That split ownership is a real contract spanning `_telemetry_types.py` and `_telemetry_runner.py`, held together by the ordering rule that an arm raises the member event before the group wake.
- The group scheduler's sleep is no longer a single `sleep_until_fire` call but a loop over three deadlines (tick, throttle window, wake), so a future change to group timing has more surface to be correct about than it had before.

_2026-09-02_
