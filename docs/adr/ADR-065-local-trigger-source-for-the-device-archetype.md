---
status: Accepted
date: 2026-09-01
impact: moderate
tags: [devices, telemetry, di, scheduling, lifecycle]
---

# ADR-065: Local trigger source for the device archetype

## Status

Accepted **Date:** 2026-09-01

## Context

ADR-064 gave `@app.telemetry` an in-process trigger source: `triggerable="local"` plus a DI-injectable `EntityNotifier` that wakes a named entity from anywhere in the process. Its Decision section explicitly placed the device archetype out of scope for v1, and bead cos-jntp was filed to revisit it.

The motivating consumer is jeelink2mqtt, whose serial adapter decodes frames on its own thread and hands them to a `@app.device` drain loop. That loop currently sleeps on a fixed 1 Hz cadence, so a frame that arrives just after a tick waits up to a second before it is published. The adapter already knows the instant a frame lands; the framework simply gives the device no way to be told.

The two archetypes differ in who owns the loop. A `@app.telemetry` handler is a single `async def` that the framework calls on a schedule, so ADR-064 could race the trigger against `interval=` inside `run_telemetry` and the handler never sees the mechanism. A `@app.device` handler is an async generator that owns its own `while True:` loop and its own sleeping; the framework runs it once and dispatches reactors at each `yield`. There is no framework-owned publish cycle to wake, and no `interval=` to race against. Whatever the device gets must therefore be something the handler itself awaits.

A second asymmetry constrains the arming side. Telemetry can be armed over MQTT because `{prefix}/{name}/set` is free for triggerable telemetry. For a device that topic is already the inbound **command** topic that the router subscribes on the device's behalf, and `ctx.on_command` dispatches from it. An MQTT trigger source for a device would collide with its own command surface.

## Decision

Extend the ADR-064 trigger mechanism to `@app.device` by accepting `triggerable="local"` on the device decorator and injecting a new `DeviceTrigger` handle that the handler awaits, reusing the existing `_TriggerSlot`, `EntityNotifier` and `TriggerPayload` rather than adding a second trigger path. Devices accept the local source only, because `{prefix}/{device}/set` is already the device's command topic.

```python
@app.device(name=_sensor_map, triggerable="local")
async def sensor_entity(
    ctx: DeviceContext,
    cfg: SensorConfig,
    trigger: DeviceTrigger,
) -> AsyncIterator[None]:
    while True:
        # Wake the instant the adapter notifies us; the timeout is a
        # heartbeat so the retained state topic is still refreshed if
        # the hardware goes quiet.
        await trigger.wait(timeout=60.0)
        reading = bus.take(cfg.id)
        if reading is not None:
            await ctx.publish_state(reading.as_dict())
        yield


# Anywhere in the process -- e.g. an adapter's decode thread:
notifier("living_room")
```

## Decision Drivers

- One trigger mechanism, not two: a device wake must use the same slot, the same coalescing and the same EntityNotifier as ADR-064 telemetry.
- The device handler owns its own loop, so the framework must hand it something to await rather than racing a sleep on its behalf.
- {prefix}/{device}/set is already the device command topic, so a device cannot take an MQTT trigger source without colliding with ctx.on_command.
- Nothing may perturb the ADR-059 runtime discovery path, the MQTT topic layout or AsyncAPI output.
- Failures must be loud at registration time rather than a silent no-op that leaves a device permanently unwakeable.
- Existing @app.device registrations that opt into nothing must be entirely unaffected.

## Considered Options

### Option 1: DeviceTrigger handle injected by type (chosen)

Add `triggerable="local"` to `@app.device` and inject a `DeviceTrigger` bound to the same `_TriggerSlot` the notifier arms. The handler replaces its own `await clock.sleep(...)` with `await trigger.wait(timeout=...)`, which returns a `TriggerPayload` distinguishing a notifier wake from an elapsed heartbeat.

- *Advantages:* Reuses the ADR-064 slot, coalescing semantics, EntityNotifier and TriggerPayload verbatim -- no parallel trigger path.; Fits the archetype: the device owns its loop, so it owns the await.; The timeout argument preserves the heartbeat the drain loop already needed, so migration is a one-line substitution for the existing sleep.; Adds no MQTT subscription, so discovery, topic layout and AsyncAPI output are unchanged by construction.; Purely additive: a new optional keyword and a new injectable type; devices that opt into nothing are untouched.
- *Disadvantages:* Adds a public type (`DeviceTrigger`) to the API surface.; The handler must remember to await the handle; opting in without awaiting would be a silent no-op, so it needs a registration-time guard.

### Option 2: Inject the raw asyncio.Event

Skip the wrapper and inject the slot's `asyncio.Event` directly, leaving the handler to wait on it and clear it.

- *Advantages:* No new public type at all.; Trivially small framework change.
- *Disadvantages:* `asyncio.Event` is already injectable as the shutdown event, so the providers map cannot carry two of them keyed by the same type.; Leaks the coalescing contract to the caller: the handler must clear the event itself, and a missed clear silently spins.; No TriggerPayload, so a woken run is indistinguishable from a heartbeat.; Exposes a framework-internal object as the supported API.

### Option 3: Async iterator protocol on the handle

Give the handle `__aiter__`/`__anext__` so the device writes `async for payload in trigger:` instead of an explicit `while True` with an await.

- *Advantages:* Reads elegantly for the pure event-driven case.; Makes the wake sequence explicit as a stream of payloads.
- *Disadvantages:* Collides with the archetype's own generator contract: the handler must still `yield` at each reactor boundary, so two interleaved iteration protocols appear in one body.; The heartbeat timeout has no natural expression in the iterator form.; Offers a second way to do the same thing alongside `wait()`, against the framework's one-obvious-way preference.

### Option 4: Framework-driven device publish cycle

Give devices a `publish=`/`interval=` cycle like telemetry so the framework can own the loop and race the trigger internally, exactly as ADR-064 does.

- *Advantages:* Would make the device path structurally identical to the telemetry path.; The trigger mechanism would stay entirely invisible to the handler.
- *Disadvantages:* Redefines the device archetype, whose whole point (ADR-010) is a long-lived handler owning its own lifecycle.; Breaks every existing @app.device registration.; Duplicates @app.telemetry, which ADR-041 explicitly warns against.; Far larger than the problem being solved.

## Decision Matrix

| Criterion | DeviceTrigger handle injected by type | Inject the raw asyncio.Event | Async iterator protocol on the handle | Framework-driven device publish cycle |
| --- | --- | --- | --- | --- |
| Reuses the ADR-064 mechanism | 5 | 3 | 5 | 4 |
| Fits the device archetype | 5 | 3 | 2 | 1 |
| Backward compatibility | 5 | 2 | 5 | 1 |
| Loud failure over silent no-op | 5 | 1 | 4 | 3 |
| Public API surface added | 4 | 5 | 3 | 2 |
| Supports a heartbeat alongside wakes | 5 | 2 | 2 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- A device entity publishes on frame arrival rather than on the next tick; the jeelink2mqtt drain loop drops its fixed 1 Hz latency floor to the notifier round trip.
- One trigger mechanism spans both archetypes: the same _TriggerSlot, the same coalescing, the same EntityNotifier and the same TriggerPayload, so an app can notify a telemetry entity and a device entity identically and by name.
- No MQTT topic, AsyncAPI document or Home Assistant discovery payload changes -- devices take no MQTT trigger source, so the ADR-059 chain is untouched by construction rather than by test alone.
- Purely additive: triggerable= defaults to False on @app.device, and existing device registrations are unaffected.
- TriggerConfig.slots is keyed by entity name across both archetypes, which is unambiguous because check_device_name already forbids a device and a telemetry entity sharing a name.
- Threading trigger_slots through start_device_tasks_for_names fixes a latent ADR-064 defect: triggerable telemetry recreated by the adapter-restart path silently lost its slot and became permanently unwakeable.

### Negative

- DeviceTrigger is a new public type, so the injectable surface grows by one entry.
- The device handler is responsible for its own heartbeat; a handler that passes no timeout and is never notified blocks forever, which is correct but easy to write by accident.
- triggerable= and the DeviceTrigger parameter must agree, so two symmetric registration-time errors exist that have no telemetry counterpart.
- Devices deliberately support a narrower spec than telemetry ("local" only), so the triggerable= argument does not mean quite the same thing on the two decorators.

_2026-09-01_
