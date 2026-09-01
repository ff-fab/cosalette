---
status: Accepted
date: 2026-08-31
impact: moderate
tags: [telemetry, di, lifecycle, mqtt, devices]
---

# ADR-064: Local (in-process) trigger source for triggerable telemetry

## Status

Accepted **Date:** 2026-08-31 | Supersedes ADR-036 | Amended **Date:** 2026-09-01

## Context

ADR-036 gave `@app.telemetry` a `triggerable=True` flag: the handler subscribes to `{prefix}/{name}/set` and an inbound MQTT message runs the handler out of cycle through the identical pipeline. That closed the "periodic poll plus on-demand refresh" gap for pull-only hardware. It did **not** close a second gap that only shows up with push-capable hardware and expanded entities.

**The asymmetry.** `@app.stream` is event-driven but singular: `_StreamRegistration` carries `name: str` and no `name_spec`, so one stream owns one topic. Expanded `@app.telemetry` (callable `name=` / `NameSpec`, one registration per device after `expand_name_specs`) is plural but tick-driven: every expanded entity publishes only on its `interval=` / `schedule=` tick. Nothing today is both plural and event-driven. When a device pushes (a WiZ bulb pilot update over UDP, a JeeLink LaCrosse frame over serial), the value lands in an adapter cache and then waits for the next tick to be noticed and published.

**The machinery to close it already exists and is already keyed by the expanded name.** `TriggerConfig.build()` creates a per-registration `_TriggerSlot` *after* name expansion (`packages/src/cosalette/_wiring/_context.py`); `_TriggerSlot.arm()` sets an `asyncio.Event` and coalesces a pending trigger (`packages/src/cosalette/_runners/_telemetry_types.py`); `TelemetryRunner._sleep_or_trigger()` already races that event against the interval sleep and wakes the handler early (`packages/src/cosalette/_runners/_telemetry_runner.py`). The **only** path that arms a slot is `_register_trigger_proxy()` on `{prefix}/{name}/set` (`packages/src/cosalette/_wiring/_context.py`). `triggerable` is a plain `bool` on `TelemetryRegistration` and the decorator (`packages/src/cosalette/_registration/_model.py`, `packages/src/cosalette/_app/_telemetry.py`).

**Why the existing escape hatches do not close it.** `@app.stream` cannot expand (no `name_spec` field). Even if it could, stream channels are excluded from Home Assistant discovery generation by default (ADR-054 Q2, ADR-059), so an expanded-stream design would publish state and zero discovery configs. Stream names also collide with command names for every registry type, whereas the two affected apps rely on telemetry+command sharing a name. `triggerable=True` is MQTT-only: for a bulb the `/set` topic is already owned by its command handler, and for the serial bridge it would mean the app publishing an MQTT message to its own broker to tell itself a frame arrived. `publish=OnChange()` is orthogonal — it suppresses duplicate payloads after the handler runs; it does not make the handler run sooner or remove a wakeup.

**Downstream impact (from the upstream proposal, verified against cosalette 0.6.3).** Two of nine cosalette-apps bridges benefit. **wiz2mqtt**: 14 bulbs on a 5 s tick — 0-5 s latency (mean 2.5 s) from a bulb changing to Home Assistant seeing it, and ~2.8 idle handler invocations per second in steady state producing nothing. **jeelink2mqtt**: a `@app.stream` caches a calibrated reading and a separate expanded `@app.device` drains that cache on a 1 Hz loop, carrying ~35 lines of freshness/dedup bookkeeping whose only job is to re-derive, one tick later, what the stream handler already had. The other seven apps are static-registry pollers of pull-only hardware and are untouched.

**Origin.** Upstream enhancement proposal from github.com/ff-fab/cosalette-apps (downstream tracking bead `cap-a2e`, backlog follow-up from the wiz2mqtt epic `cap-10u`). Framework-side tracking bead: **cos-3qri**. This is a scheduled, low-priority follow-up; it is not being implemented with this ADR.

**Related decisions.** This supersedes ADR-036 (Triggerable Telemetry). ADR-041 (Periodic Background Tasks) warns against overloading `@app.telemetry` with more axes — a driver for the minimal string-enum surface here. ADR-042 (StreamablePort / Stream[T]) established the `thread_safe` + `call_soon_threadsafe` precedent for marshalling items from non-event-loop threads. ADR-043 (`@app.react`) is the closest existing alternative and is evaluated below. ADR-054 (AsyncAPI emission for the stream archetype) and ADR-059 (runtime HA discovery publication) define the discovery/AsyncAPI parity that this change must not disturb; the work is scheduled behind the ADR-059 discovery chain as a *constraint, not a dependency*.

## Decision

Supersede ADR-036. Widen `triggerable=` from `bool` to accept `bool | a trigger-source declaration`; the recommended form is the string enum `triggerable="mqtt" | "local" | "both"` (`True` retained as an alias for `"mqtt"`; `False` / omitted unchanged), chosen over a `Trigger(...)` value object to keep the surface minimal on an already 13-axis decorator (ADR-041 anti-overloading).

Add a new DI-injectable **`EntityNotifier`**: a callable `(expanded_name: str) -> None` that arms the existing per-expanded-name `_TriggerSlot`. It is loop-affine by default, with a documented thread-safe arm via `call_soon_threadsafe` for push callbacks that run off the event loop (ADR-042 `thread_safe` precedent). An unknown entity name raises a named exception -- never a silent no-op.

`interval=` stays required and is re-documented as a heartbeat / fallback: it guarantees the retained state topic is refreshed even if the device never pushes again, and it detects a dead push subscription. `TriggerPayload` gains `source: Literal["scheduled", "mqtt", "local"]` and a `local()` constructor (will be added to `packages/src/cosalette/_runners/_trigger.py`). A woken run goes through the identical existing publish cycle -- OnChange, `state_model` validation, availability, persistence, error publication -- so there is no second publish path and ticked vs woken runs are indistinguishable downstream of the handler.

Narrow `_validate_enabled_telemetry()` in `packages/src/cosalette/_wiring/_resolution.py` to allow `triggerable` + root (unnamed) device for a **local-only** source -- a local wake needs no topic segment. Keep `triggerable` + `group=` excluded for v1.

**Out of scope for v1:** `@app.device` trigger support (trigger slots live on telemetry registrations only; jeelink2mqtt's `sensor_entity` is an `@app.device` and needs a separate conversion follow-up); `triggerable` + `group=`; expandable `@app.stream` with a demux routing key (Option B -- ADR-054 delivered the AsyncAPI half of its prerequisites, but ADR-054 Q2 / ADR-059 still exclude streams from HA discovery generation, so it stays deferred).

**Status: Accepted.** Implemented by bead cos-3qri; see the 2026-09-01 amendment below for the as-built decision.

```python
import cosalette

# composition root -- interval= is now a heartbeat/fallback, not the publish path
@app.telemetry(
    name=_bulb_map,
    interval=60,
    triggerable="local",           # "mqtt" (== True) | "local" | "both"
    publish=cosalette.OnChange(),
)
async def bulb_entity(
    ctx: cosalette.DeviceContext,
    config: BulbConfig,
    port: WizBulbPort,
    state: SharedState,
    trigger: cosalette.TriggerPayload,
) -> dict[str, object] | None:
    return await bulb_entity_tick(ctx, config, port, state, trigger)


# the arming side -- anywhere DI reaches
@app.state
def shared_state(notify: cosalette.EntityNotifier) -> SharedState:
    return SharedState(notify=notify)


class WizBulbAdapter:
    # push callback (may run off the event loop): arms that bulb's slot; coalesces
    def _on_push(self, ip: str, parsers: object) -> None:
        parsed = _parse_state(parsers)
        if parsed is not None:
            self._state_cache[ip] = parsed
            self._notify(self._name_for(ip))


# distinguishing the wake source inside the handler
async def bulb_entity_tick(ctx, config, port, state, trigger: cosalette.TriggerPayload):
    if trigger.source == "local":
        ...  # woken by a hardware push
```

## Decision Drivers

- Close the asymmetry: @app.stream is event-driven but singular; expanded @app.telemetry is plural but tick-driven; nothing today is both.
- Reuse machinery that already exists and is already keyed by the expanded name (_TriggerSlot, arm() coalescing, wake-early sleep race) -- the new surface is only a trigger-source declaration, a DI provider, and a thread-safe set().
- Keep the publish path single: a woken run must flow through the identical handler cycle so publish=, state_model=, availability, persistence and error publication cannot drift between ticked and pushed publications.
- Stay purely additive and opt-in: triggerable=True keeps its exact ADR-036 meaning, no app that does not opt in changes behaviour, and it ships as a 0.7.x minor.
- Preserve discovery/AsyncAPI parity: app.asyncapi() output and the retained homeassistant/.../config topic set must be identical with and without local triggering, so the ADR-059 runtime-discovery chain is not disturbed.
- Remove the freshness/dedup bookkeeping that every push-capable downstream app otherwise reinvents to recover, one tick late, what the push handler already had.

## Considered Options

### Option 1: Local trigger source via string-enum triggerable= plus injectable EntityNotifier (chosen)

Widen triggerable= to bool | "mqtt" | "local" | "both" (bool stays, meaning mqtt). Add a DI-injectable EntityNotifier callable (expanded_name) -> None that arms the existing per-expanded-name _TriggerSlot; loop-affine by default with a documented call_soon_threadsafe path; unknown name raises a named exception. interval= becomes a heartbeat/fallback. TriggerPayload gains source and a local() constructor. Woken runs reuse the identical publish cycle. Guard narrowed to allow triggerable + root for local-only; triggerable + group stays excluded for v1.

- *Advantages:* Reuses slots, coalescing, wake-early sleeping and per-entity keying that already exist post-expansion; minimal new code; Single publish path: ticked and woken runs are indistinguishable downstream of the handler; Purely additive and opt-in; triggerable=True unchanged; ships as a 0.7.x minor; No MQTT topic, AsyncAPI or HA discovery changes -- the ADR-059 chain is untouched; Framework owns a name-validation point (unknown entity name raises), and the string enum keeps the new surface tiny
- *Disadvantages:* Widens an already 13-axis decorator; mild tension with ADR-041's don't-overload-@app.telemetry guidance; triggerable= now carries two concepts under one name (an MQTT subscription and an in-process wake); EntityNotifier is a handle callable from anywhere, including code that should not publish -- the domain-purity rule must survive it; Lifecycle ordering hazard: EntityNotifier must be a stable Phase-1 handle late-bound to trigger_config.slots after TriggerConfig.build in Phase 2; Thread-safety becomes load-bearing; storm control (min-interval) is deferred to a follow-up

### Option 2: Do nothing -- document the polling pattern

Keep tick-driven publication for expanded entities and document the cache-plus-poll pattern (adapter caches the push, the interval tick drains it with publish=OnChange()) as the recommended approach.

- *Advantages:* Zero framework change and zero new API surface; No lifecycle, thread-safety or storm-control questions to answer; Broker traffic is already bounded by publish=OnChange() in both affected apps
- *Disadvantages:* Does not close the primary gap: latency stays at mean 2.5 s (wiz2mqtt) / 0.5 s (jeelink2mqtt) and idle wakeups scale linearly with device count; Every push-capable app keeps reinventing ~35 lines of freshness/dedup bookkeeping; Lowering the tick interval trades latency for wakeups one-for-one and buys nothing the event already knows

### Option 3: Route jeelink2mqtt through @app.react (ADR-043)

Use the existing @app.react domain-event reactor: the stream handler records a domain event, a reactor drains it after the execution boundary and publishes per-sensor state directly, bypassing the expanded telemetry entity's tick.

- *Advantages:* Uses an existing, accepted primitive with no new public API; Event-driven publication for the serial bridge with no new decorator argument
- *Disadvantages:* Only helps jeelink2mqtt; wiz2mqtt's per-bulb entities stay tick-driven; Introduces a second publish path (reactor-driven) that does not share the telemetry handler cycle, so publish=/state_model=/availability behaviour can drift; Reactor publishes are not gated by the expanded entity's publish= strategy or coalescing group

### Option 4: Trigger(...) value object instead of the string enum

Same EntityNotifier and slot-arming design, but declare the source with a value object, e.g. triggerable=cosalette.Trigger(mqtt=False, local=True).

- *Advantages:* Explicit and extensible -- new trigger sources or per-source options add fields rather than enum members; Reads clearly at the call site for the both case
- *Disadvantages:* Adds a new public type to the framework namespace for a two-bit choice; Grows the surface ADR-041 explicitly warns about, for no capability the string enum lacks in v1; Two ways to say the same thing (Trigger(mqtt=True) vs triggerable=True) invite drift in docs and templates

### Option 5: Declarative wake= callable

@app.telemetry(name=..., wake=lambda cfg: cfg.event) -- the framework awaits a per-entity awaitable alongside the interval sleep, and the app owns the event object.

- *Advantages:* More declarative than an injected notifier -- the wake source is visible on the decorator; No new injectable handle that can be called from arbitrary code
- *Disadvantages:* Pushes asyncio.Event ownership into app code, which the testing guidance warns against and which fights fake_clock; Gives the framework no name-validation point -- a typo'd or stale event silently never fires; Event lifecycle (creation, thread-safety, teardown) leaks into the composition root

### Option 6: Expandable @app.stream (Option B)

Give @app.stream a name_spec and a routing key (item -> name), and have the framework demultiplex one StreamablePort[T] across expanded entities so the stream handler publishes per-device state directly.

- *Advantages:* Conceptually the cleanest fit for jeelink2mqtt -- one event-driven primitive, plural, publishing per sensor; ADR-054 already delivered the AsyncAPI half of the prerequisites
- *Disadvantages:* Stream channels are excluded from HA discovery generation by default (ADR-054 Q2, ADR-059) -- an expanded stream would publish state and zero discovery configs; Stream names collide with command names for every registry type; wiz2mqtt relies on telemetry+command name sharing and could not adopt it; Needs a routing key that can consult mutable runtime state; larger, riskier change -- deferred as a later consolidation once Option A has proven the semantics

## Decision Matrix

| Criterion | Option 1 (chosen) | Option 2 | Option 3 | Option 4 | Option 5 | Option 6 |
| --- | --- | --- | --- | --- | --- | --- |
| Closes the plural + event-driven gap for both affected apps | 5 | 1 | 2 | 5 | 4 | 5 |
| Additive / backward-compatible (no forced migration, minor release) | 5 | 5 | 4 | 5 | 4 | 2 |
| API surface minimalism (ADR-041 anti-overloading) | 4 | 5 | 4 | 2 | 3 | 3 |
| Discovery / AsyncAPI parity (ADR-054, ADR-059) | 5 | 5 | 5 | 5 | 5 | 1 |
| Testability with fake_clock and a framework name-validation point | 4 | 5 | 3 | 4 | 2 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Additive and opt-in: triggerable=True keeps its exact ADR-036 meaning, the router's subscription set for existing apps is unchanged, and the feature ships as a 0.7.x minor.
- No MQTT topic, AsyncAPI or Home Assistant discovery changes -- app.asyncapi() output and the retained homeassistant/.../config topic set are identical with and without local triggering, so the ADR-059 runtime-discovery chain is unaffected. 7 of 9 downstream apps see no change at all.
- Single publish path preserved: a woken run reuses the identical handler cycle, so publish=, state_model= validation, availability, persistence and error publication cannot drift between ticked and pushed publications.
- Removes downstream bookkeeping: ~35 lines of freshness/dedup logic in jeelink2mqtt and the staleness re-check in wiz2mqtt become the framework's heartbeat/fallback. wiz2mqtt idle wakeups drop ~92% (about 2.8/s to about 0.23/s across 14 bulbs) and push-to-publish latency drops from mean 2.5 s (wiz2mqtt) / 0.5 s (jeelink2mqtt) to approximately zero.
- Reuses machinery already keyed by the expanded name (_TriggerSlot, arm() coalescing, the wake-early sleep race) -- the net new surface is a trigger-source declaration, a DI provider and a thread-safe set().
- Local-only sources may be used on root (unnamed) devices, since a local wake needs no topic segment -- a small capability gain over ADR-036's MQTT-only guard.

### Negative

- Widens an already 13-axis decorator and puts two concepts (an MQTT subscription and an in-process wake) under the one triggerable= name -- a real, if mild, tension with ADR-041's don't-overload-@app.telemetry guidance, mitigated by the minimal string-enum surface.
- EntityNotifier is an injectable handle that can be called from anywhere, including code that should not be publishing -- the domain-purity rule (domain never imports cosalette) has to survive it. Before implementation, prefer a per-entity or otherwise narrowly scoped notifier handle as an acceptance criterion.
- Lifecycle ordering hazard (the main implementation risk): `enter_state_factories()` (Phase 1, `packages/src/cosalette/_app/_lifecycle.py`) runs before `TriggerConfig.build()` (Phase 2), so EntityNotifier must be a stable handle created in Phase 1 and late-bound to `trigger_config.slots` after build -- and must raise loudly if armed before the bind completes.
- Thread-safety is now load-bearing: push callbacks from serial / BLE / HID adapters may not run on the event loop, so the arming path must use call_soon_threadsafe and the contract must say so (criterion pinned by the proposal's validation set).
- Storm exposure: coalescing bounds the queue depth, not the handler invocation rate; v1 ships without a min-interval knob. Until `min-interval=` ships, adopters should guard the `notify()` call-site with a same-value dedup (compare cached state before calling notify) to prevent back-to-back handler runs during push bursts. The min-interval follow-up must be tracked by a dedicated bead before implementation begins.
- For an adopting app, why did this publish? becomes a two-answer question (tick vs local wake) during debugging.
- @app.device does not get a trigger source in v1, so jeelink2mqtt's sensor_entity needs a separate @app.device to @app.telemetry conversion follow-up before it can adopt this.

_2026-08-31_

## Supersedes

ADR-036 (Triggerable Telemetry).

## Amendment (2026-09-01) — Corrective

**Rationale:** Implementing bead cos-3qri turned the proposal into working code, and three details had to be settled that the Proposed record either left open or got wrong. (1) The maintainer lifted the additive/backward-compatible constraint for this stage of the framework, so the implementation was free to prefer the better API where it buys real clarity; two internal representations were normalised as a result, which is a breaking change for code touching the private registration model. (2) The proposal described EntityNotifier as 'loop-affine by default, with a documented thread-safe arm', implying an ADR-042-style opt-in flag; the framework — not the app — constructs this handle, so a per-call flag would be a silent-corruption footgun and thread detection is automatic instead. (3) The proposal named @app.state factories as the arming side but did not say where else the handle must reach; adapter factories and on_configure hooks turned out to need it too, which forced a decision on where the handle is created in Phase 1.

> **Justification for amendment (not supersession):** Supersession is not warranted: ADR-064 was Proposed and had no implementation when this amendment was written, so no downstream code depends on the details being corrected. The chosen option, the option analysis, the decision matrix and the discovery/AsyncAPI parity constraint all stand unchanged — this amendment records the as-built form of the same decision and moves the record to Accepted. The one substantive reversal (the 'purely additive, ships as a 0.7.x minor' claim) narrows to two internal representations and is recorded below with its version impact.

### Revised Decision

Supersede ADR-036. Widen `triggerable=` from `bool` to `bool | "mqtt" | "local" | "both"`, normalised at registration time by `normalize_trigger_source()` into a single internal `TriggerSource | None` field on `_TelemetryRegistration`. `True` remains an alias for `"mqtt"` and `False`/omitted remain 'no trigger source', so every existing decorator call keeps its exact ADR-036 meaning. Two arming-path predicates, `arms_via_mqtt()` and `arms_locally()`, replace every former truthiness test on `triggerable`, so each downstream branch (MQTT subscription, root-device guard, local-slot filtering) asks the one question it actually cares about.

Add the DI-injectable `EntityNotifier`: a callable `(expanded_name: str) -> None` that arms the existing per-expanded-name `_TriggerSlot`. It is a framework-owned handle, created once at the top of lifecycle Phase 1 and late-bound in Phase 2 to `TriggerConfig.local_slots()` — the slots of registrations declaring `"local"` or `"both"`. It is reachable by type from handlers, `@app.state` factories, adapter factories and `on_configure` hooks. Arming before the Phase-2 bind raises `NotifierNotReadyError`; an unknown or non-locally-triggerable name raises `UnknownEntityError`; both derive from `EntityNotifierError`. Never a silent no-op.

`interval=` stays required and is re-documented as a heartbeat / fallback. `TriggerPayload` gains `source: Literal["scheduled", "mqtt", "local"]` and a `local()` constructor; `_TriggerSlot` gains `arm_local()` alongside `arm()`, and the most recent arm decides the reported source when both coalesce into one pending run. A woken run goes through the identical existing publish cycle — `publish=`, `state_model=`, availability, persistence, error publication — so there is no second publish path.

Narrow the registration guard to allow a trigger source on a root (unnamed) device when it is local-only; `triggerable` + `group=` stays excluded for v1.

**Breaking changes (version impact).** The original record claimed the change was 'purely additive' and would ship as a 0.7.x minor. As built it is a **minor-breaking** change and belongs on a 0.8.0 line, not 0.7.x. Two internal representations changed shape: `_TelemetryRegistration.triggerable` is now `TriggerSource | None` rather than `bool` (code reading the private registration model, including tests, sees `"mqtt"`/`None` instead of `True`/`False`), and the MCP registry snapshot gains a `"trigger_source"` key while its existing `"triggerable"` key is normalised to a plain boolean so that snapshot consumers keep their current shape. No public decorator call, MQTT topic, AsyncAPI document or Home Assistant discovery payload changes.

```python
import cosalette


# composition root — interval= is now a heartbeat/fallback, not the publish path
@app.telemetry(
    name=_bulb_map,
    interval=60,
    triggerable="local",           # True == "mqtt" | "local" | "both"
    publish=cosalette.OnChange(),
)
async def bulb_entity(
    ctx: cosalette.DeviceContext,
    config: BulbConfig,
    port: WizBulbPort,
    trigger: cosalette.TriggerPayload,
) -> dict[str, object] | None:
    if trigger.source == "local":
        ...                        # woken by a hardware push
    return await bulb_entity_tick(ctx, config, port)


# the arming side — store the handle, do not call it from the factory body
@app.state
def shared_state(notify: cosalette.EntityNotifier) -> SharedState:
    return SharedState(notify=notify)


# push callback, on the event loop or any other OS thread
def _on_push(self, ip: str) -> None:
    self._state_cache[ip] = _parse_state(ip)
    self._notify(self._name_for(ip))   # coalesces; raises on an unknown name
```

### Additional Sub-Decision: Thread safety by automatic detection, not an opt-in flag

`EntityNotifier.__call__` compares the calling thread against the loop thread recorded at bind time: on the loop thread it arms the slot inline, from any other thread it marshals the arm with `loop.call_soon_threadsafe()`. There is no `thread_safe=` flag.

ADR-042 put that flag on `Stream.put` because the *app* constructs the `Stream` and therefore knows its own producer. `EntityNotifier` is constructed by the framework and handed out by DI, so the same flag would have to be set by the consumer at every call site — and getting it wrong would corrupt `asyncio.Event` state silently rather than failing. Detection costs one `threading.get_ident()` comparison per call and cannot be got wrong.

The entity name is always validated in the *calling* thread, before any marshalling, so a typo raises where it was made instead of disappearing into the loop. If the loop has already closed when an off-loop push arrives (a callback outliving shutdown), the arm is dropped with a DEBUG log rather than raising into a foreign thread.

### Additional Sub-Decision: Phase-1 handle, Phase-2 bind, named failure in between

The handle is constructed at the very top of lifecycle Phase 1 — before `resolve_adapters`, so adapter factories can receive it — and inserted into the resolved-adapter registry, which is what feeds the DI providers map for handlers. It is also passed explicitly to `enter_state_factories`, since `@app.state` factories resolve their parameters from a separate, narrower map.

It is bound in Phase 2, on the line immediately after `TriggerConfig.build(...)`, to `trigger_config.local_slots()`. Everything between construction and bind holds a handle whose `_slots` is `None`; arming there raises `NotifierNotReadyError` with a message telling the caller to store the handle and call it once the app is running. This is the ordering hazard the original record flagged, and it fails loudly by construction rather than by convention.

`EntityNotifier` is added to `KNOWN_INJECTABLE_TYPES` so the adapter→device map built for health checking does not mistake the handle for an adapter port. Its `entities` property exposes the bound name set for debugging.

### Additional Sub-Decision: Only local sources are notifiable

`TriggerConfig.build()` creates a slot for every registration declaring any trigger source, but `local_slots()` — what the notifier binds to — contains only those declaring `"local"` or `"both"`. Notifying an MQTT-only entity therefore raises `UnknownEntityError`, listing the names that *are* notifiable, rather than quietly arming a slot the author never opted into waking in-process.

Symmetrically, `_register_triggerable_telemetry` subscribes `{prefix}/{name}/set` only when `arms_via_mqtt()` holds, so a `"local"` entity adds no subscription at all and an inbound `/set` on its topic is not routed to it.

### Additional Positive Consequences

- Each arming path is now an explicit predicate (arms_via_mqtt / arms_locally) rather than a truthiness test on a bool, so adding a future source cannot silently inherit MQTT behaviour.
- The notifier is injectable into adapter factories and on_configure hooks as well as @app.state factories, so a push-capable adapter can hold the handle directly instead of routing wakes through shared state.
- Discovery/AsyncAPI parity is enforced by test, not just by inspection: the AsyncAPI document and the generated Home Assistant discovery payloads are asserted byte-identical across triggerable=False/True/"mqtt"/"local"/"both".

### Additional Negative Consequences

- The change is minor-breaking rather than purely additive: _TelemetryRegistration.triggerable is now TriggerSource | None, so it belongs on a 0.8.0 line rather than the 0.7.x minor the original record assumed.
- An invalid triggerable= string is a registration-time ValueError rather than a static type error for apps that do not run a type checker — the string enum trades some of a value object's compile-time safety for surface minimalism.
- EntityNotifier carries a loop reference for the life of the app, so a handle stored in a long-lived adapter keeps that loop object reachable until the adapter is released.
