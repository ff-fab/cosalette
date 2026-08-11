---
status: Accepted
date: 2026-08-10
impact: high
tags: [commands, mqtt, routing, architecture, error-handling, lifecycle]
---

# ADR-055: Concurrent per-entity command dispatch

## Status

Accepted **Date:** 2026-08-10

## Context

Prior to this decision, every command handler invocation was awaited inline on the single MQTT read loop. The connection loop (`_mqtt/_client.py:269-270`) awaited `_dispatch()` for each message, which awaited every registered callback sequentially. The only framework-registered callback was `TopicRouter.route()`, which awaited the matched handler directly. For `@app.command` handlers, this meant the user's command function was awaited on the critical path of the MQTT read loop. **One slow or hung handler stalled commands for EVERY entity in the app**, regardless of how unrelated they were.

This was not a performance optimization opportunity — it was a **bug with silent failure**. The symptom from the upstream report: 14 WiZ smart bulbs in a single app, one bulb powered off at the wall. WiZ bulbs use unacknowledged UDP; a bulb that stops answering causes the client push to wait indefinitely. That single bulb's handler wedges the read loop, and no other entity in the app can process commands — not the other 13 bulbs, not unrelated devices. The app does not crash, log an error, or go offline:

- The heartbeat task keeps publishing `status: "online"` because it runs on its own task.
- The MQTT Last Will and Testament never fires because the TCP connection is healthy.
- `@app.telemetry` and `@app.device` handlers keep publishing state because they get their own tasks.

To Home Assistant and other consumers, the app is alive and reporting. Commands simply have no effect. **There is no signal anywhere that indicates the app has stopped processing commands.** Scale sharpens this but does not cause it — two entities are enough; 14 just made it a daily occurrence.

Four compounding findings were identified:

**Finding 1 (the core bug):** The read loop awaits user command code inline. No `asyncio.create_task()` exists anywhere between `client.messages` and the user function.

**Finding 2:** `@app.command` has no timeout mechanism. `@app.telemetry` accepts `timeout=` and applies it via `asyncio.wait_for`; commands have no equivalent. A handler that hangs wedges the read loop for the process lifetime.

**Finding 3a:** The existing escape hatch — `@app.device` + `ctx.commands()` — decouples dispatch by enqueuing commands onto an internal queue, but that queue is unbounded. If the consuming loop is slower than the inbound rate, the queue grows without limit. The failure mode moves from "commands stall" to "memory grows and commands execute minutes late." The framework already designed this for streams (`BackpressurePolicy`, `maxsize`), but commands never inherited it.

**Finding 3b:** A `@app.device` that declares `payload_model=` subscribes to `{prefix}/{device}/set` at runtime, but emits zero receive channels in the AsyncAPI document. The app subscribes to a topic it does not document. Downstream tooling (discovery generation, ACL derivation, schema enforcement) cannot see the command surface.

## Decision

Make concurrent per-entity command dispatch the **default** for all `@app.command` handlers, with no opt-in flag. `TopicRouter` now gives each registered entity (each named device/command, plus the root) a dedicated FIFO worker task backed by its own `asyncio.Queue`. The `route()` method enqueues the message and returns immediately — the MQTT read loop never awaits user command code. Ordering is preserved within a single entity; entities run concurrently with each other. Add `timeout: float | None = None` to `@app.command`, `@app.device`, `app.add_command()`, `app.add_device()`, and the public `@router.command` / `@router.device`. A per-invocation backstop applied via `asyncio.wait_for`, reusing the telemetry timeout mechanism (`_runners/_telemetry_runner.py:983-986`). `TimeoutError` (PEP 3151, an `Exception`) flows through the existing `publish_error_safely` path to the device error topic. Composes with `unavailable_on=(TimeoutError,)` to mark a device offline on timeout. Default `None` disables it. Add `maxsize: int = 0` and `backpressure: BackpressurePolicy = "drop_newest"` to `@app.command`, `@app.device`, and the public `@router.command` / `@router.device`, reusing the existing stream vocabulary `BackpressurePolicy = Literal["drop_newest", "drop_oldest", "raise"]`. Bounds apply to the router per-entity worker queue **and** to the `ctx.commands()` queue (`ctx._command_queue`). A shared `apply_backpressure()` helper (in `_runners/_stream_types.py`) is now used by streams, the router, and the device context. Default `maxsize=0` (unbounded) is fully backward compatible; bounding is opt-in but declarable per handler. Add a `device_command` AsyncAPI channel kind. A device that declares `payload_model=` now emits a `receive` channel on `/set` (archetype `device`) in the AsyncAPI document, alongside its existing `/state` send channel. Devices without `payload_model` are unchanged. (`payload_model` remains metadata for validation — it does NOT runtime-validate inbound payloads; only `state_model` is runtime load-bearing for `publish_state`.)

```python
# Per-entity concurrency (default, no opt-in)
@app.command("valve")
async def valve_cmd(payload: str) -> dict[str, object]:
    await slow_io()  # does NOT block other entities
    return {"state": "open"}

# Timeout with availability composition
@app.command(
    "display",
    timeout=5.0,
    unavailable_on=(TimeoutError,)
)
async def display_cmd(payload: str) -> dict[str, object]:
    await update_display(payload)  # TimeoutError → offline
    return {"status": "updated"}

# Bounded queue with backpressure
@app.command(
    "relay",
    maxsize=10,
    backpressure="drop_oldest"
)
async def relay_cmd(payload: str) -> dict[str, object]:
    return {"state": payload}

# Device with input channel in AsyncAPI
@app.device(
    "thermostat",
    state_model=ThermostatState,
    payload_model=ThermostatCommand  # emits receive channel
)
async def thermostat(ctx: DeviceContext):
    async for cmd in ctx.commands():
        await ctx.publish_state({"target": cmd.payload})
        yield
```

## Decision Drivers

- One slow or hung command handler must not stall commands for every other entity — this is a bug, not a performance optimization
- The framework must not silently appear healthy while it has stopped processing commands — failure must be observable
- Command handlers must have an opt-in per-invocation timeout backstop, like telemetry
- Command queues must support declarable backpressure policies to prevent unbounded memory growth under load
- The AsyncAPI contract must accurately reflect subscribed topics — devices with payload_model subscribe to /set and must emit a receive channel

## Considered Options

### Option 1: Opt-in concurrency flag on @app.command

Add concurrency=True flag to @app.command. Default False (current behavior). When True, spawn a task for the handler.

- *Advantages:* Zero risk to existing apps — old behavior is default; Gradual migration path — enable per handler as tested
- *Disadvantages:* Leaves every existing app carrying the silent-failure bug until manually fixed; Upstream maintainer explicitly rejected this — "default must be safe, not legacy"; Command ordering within an entity becomes opt-in and potentially surprising; Two code paths to maintain and test

### Option 2: Spawn an asyncio.Task per inbound message

On each message arrival, call asyncio.create_task(handler(...)). No queues, no workers.

- *Advantages:* Simplest implementation — one line change in TopicRouter.route; No queue memory overhead
- *Disadvantages:* Destroys per-entity ordering — two commands for the same entity race; No backpressure mechanism — tasks spawn unbounded under load; Tasks are not tracked — shutdown must wait for stragglers or forcibly cancel; Timeout and error handling must be per-task instead of centralized

### Option 3: Per-entity FIFO worker tasks (chosen) (chosen)

TopicRouter gives each entity a dedicated asyncio.Queue and a single worker task. route() enqueues and returns. Worker dequeues and awaits the handler. Read loop is freed immediately.

- *Advantages:* Read loop never blocks on user code — core bug fixed by default; Per-entity ordering preserved — FIFO within an entity, concurrent across entities; Backpressure is declarable per handler via maxsize + policy; Worker tasks are tracked and can be drained (wait_idle) or cancelled cleanly (aclose); timeout= composes naturally via asyncio.wait_for in the worker; Consistent with telemetry/device task model — commands get the same treatment
- *Disadvantages:* One extra idle worker task per entity (14 bulbs = 14 workers); Dispatch is now asynchronous — handler side effects complete after message acceptance; Internal tests and custom consumers must call wait_idle() to drain before asserting effects

### Option 4: Unified command runner with shared worker pool

CommandRunner owns all command ingress. A single bounded worker pool dispatches to all handlers.

- *Advantages:* Worker count is bounded and configurable (e.g. 10 workers for 100 entities); Centralized queue monitoring and backpressure
- *Disadvantages:* Largest blast radius — one wedged handler can still wedge the entire pool; Per-entity ordering is hard to preserve without queue-per-entity anyway; Requires a dispatch scheduler to assign workers to entities; Breaking change to CommandRunner and TopicRouter interaction

## Decision Matrix

| Criterion | Opt-in concurrency flag on @app.command | Spawn an asyncio.Task per inbound message | Per-entity FIFO worker tasks (chosen) | Unified command runner with shared worker pool |
| --- | --- | --- | --- | --- |
| Fixes the core bug (read loop never blocks) | 1 | 5 | 5 | 4 |
| Preserves per-entity ordering | 5 | 1 | 5 | 4 |
| Backpressure is declarable | 1 | 1 | 5 | 5 |
| Safe default (no silent failures in existing apps) | 1 | 3 | 5 | 4 |
| Implementation complexity | 3 | 5 | 4 | 2 |
| Shutdown determinism (clean task cancellation) | 5 | 2 | 5 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- The MQTT read loop never awaits user command code — one slow handler cannot stall commands for unrelated entities
- Per-entity ordering is preserved — two commands for the same device execute in FIFO order; different devices run concurrently
- timeout= gives handlers a per-invocation backstop; composes with unavailable_on=(TimeoutError,) to mark a device offline on timeout
- Command queues are declarable-bounded (default unbounded for back-compat) — maxsize + backpressure prevent unbounded memory growth
- Reuses the existing stream BackpressurePolicy vocabulary and shared apply_backpressure() helper — no new abstractions
- Devices with payload_model now emit a receive channel in AsyncAPI — the contract accurately reflects subscribed topics
- Concurrency is the default (no opt-in) — every existing app is fixed, per the upstream maintainer's explicit requirement

### Negative

- One idle worker task per entity — 14 devices = 14 workers (small memory cost, ~10KB per asyncio.Task)
- Dispatch is now asynchronous — a command's side effects complete slightly after the message is accepted (ordering guaranteed only within entity, not across entities)
- Internal tests and custom MQTT consumers must call router.wait_idle() to drain worker queues before asserting handler side effects (breaking test change)
- Commands do not compose with coalescing groups — groups need a shared interval, commands have no interval (documented constraint)

_2026-08-10_
