---
status: Accepted
date: 2026-05-04
impact: high
tags: [architecture, lifecycle, di, devices]
---

# ADR-043: Domain-Event Reactors for State Objects

## Status

Accepted **Date:** 2026-05-04

## Context

cosalette's `@app.state` decorator (ADR-039) solves state construction and DI injection but leaves state objects responsible for their own I/O side-effects. In practice, shared state classes accumulate MQTT publish methods and persistence calls that are injected at call time, violating the Single Responsibility Principle and coupling the domain model to I/O infrastructure.

The concrete consequence: a `SharedState` class must accept `ctx: DeviceContext` and `store: DeviceStore` on every method that has side-effects, making it impossible to test the domain state without mocking I/O. Scattering those call sites across device handlers violates the framework's own DI philosophy — handlers declare dependencies, the framework injects them.

The framework already provides a clear dispatch boundary: `@app.device` handlers are long-running async generators that yield after each unit of work. `@app.telemetry`, `@app.command`, and `@app.stream` handlers complete atomically. These boundaries are natural reactor-dispatch points.

Breaking change: device handlers that previously returned coroutines (`async def`) must now be converted to async generators (`async def` with `yield`). This is a deliberate simplification — the single execution model reduces runner complexity and enables uniform reactor dispatch.

## Decision

Add `@app.react(StateType, drain=callable | None)` as a new registration decorator. A reactor function is called by the framework after each execution boundary (yield in `@app.device`/`@app.stream`; successful return in framework-owned single-call handlers) when the drained event list is non-empty. The `drain` callable (or the structural `drain_events()` method on the state instance when `drain=None`) is called to collect pending events. The `events` parameter is reserved: if the reactor function declares a parameter named `events`, the framework injects the drained list directly and skips normal type-based DI for that name. All other parameters follow standard DI rules. Failures propagate through the existing runner error path. No manual dispatch public API is exposed.

```python
@app.state
def shared_state() -> SharedState:
    return SharedState(registry=Registry())


# drain= points to the method that returns pending events
@app.react(SharedState, drain=lambda s: s.registry.drain_events())
async def on_registry_events(
    events: list[RegistryEvent],  # reserved name — injected by framework
    ctx: cosalette.DeviceContext,
    store: DeviceStore,
    state: SharedState,
) -> None:
    for event in events:
        await ctx.publish("registry/event", event.to_dict())
    store["registry"] = state.registry.to_dict()


@app.device("receiver")
async def receiver(ctx: cosalette.DeviceContext, state: SharedState):
    """Async generator — framework dispatches reactors after each yield."""
    while not ctx.shutdown_requested:
        reading = await read_sensor()
        state.registry.record(reading)
        yield  # reaction boundary: on_registry_events fires here
        await ctx.sleep(1.0)
```

## Decision Drivers

- State objects must be pure domain models — no MQTT or persistence knowledge
- Reactor I/O logic must be testable as plain async functions without mocking device context
- The framework must own the dispatch lifecycle; no manual flush calls in user code
- Reactor registration must integrate with the existing DI system without adding generic type support
- Device handler async-generator semantics must serve as the uniform reaction boundary

## Considered Options

### Option 1: @app.react external reactor (chosen)

Add `@app.react(StateType, drain=callable|None)` as a top-level decorator. The framework calls the drain on the state instance after each execution boundary; if events are returned, it resolves DI kwargs and invokes the reactor. Device handlers become async generators; `yield` marks the reaction boundary. The `events` parameter name is reserved — injected directly, bypassing type-DI.

- *Advantages:* State objects remain pure domain models — no I/O parameters on any method; Reactor functions are plain async callables testable without running the app; Follows the existing @app.state / @app.periodic registration idiom; Framework owns all dispatch timing — no user-side flush calls; DI injection covers all parameters except the reserved `events` name
- *Disadvantages:* Breaking change: @app.device handlers must be converted from coroutines to async generators; events parameter reservation is implicit; naming conflict with user DI types named events is unlikely but possible; drain= callable adds a small ceremony for non-standard drain method names

### Option 2: Flushable protocol on state

Define a `Flushable` protocol with `async def flush(ctx, store)`. State objects implement it. The framework detects `Flushable` state types and calls `flush()` after each execution boundary, passing ctx and store.

- *Advantages:* No new decorator API surface; Flush logic stays co-located with the state class
- *Disadvantages:* State objects still receive I/O parameters — SRP violation remains; Testing flush logic still requires mocking ctx and store; Framework must plumb ctx and store into Flushable.flush, coupling infrastructure to the domain protocol; Composing multiple Flushable sub-objects on one state becomes awkward

### Option 3: Internal asyncio.Queue event bus

State objects push events onto a shared asyncio.Queue. Background workers consume the queue and call registered handler functions with DI injection, decoupled from the device task lifecycle.

- *Advantages:* State objects push events without I/O knowledge — pure domain; Reactor execution is fully decoupled from the device loop
- *Disadvantages:* Adds concurrency complexity: queue workers, shutdown ordering, backpressure; Reactor execution is asynchronous from device updates — ordering guarantees are lost; Events can accumulate between reactor invocations with no bound, risking unbounded queue growth; No correlation between device iteration and reactor dispatch — debugging is harder; Substantially more framework code than the dispatcher approach

### Option 4: Inject AppPublisher into @app.state factory

Pass an `AppPublisher` handle to `@app.state` factories, allowing state objects to publish directly without accepting ctx on every call.

- *Advantages:* State factories can capture the publisher at construction — no per-call ctx; No new decorator syntax
- *Disadvantages:* State class still holds an I/O reference — not a pure domain object; AppPublisher must be available at state-factory time (before devices start), complicating bootstrap ordering; Publisher scope is unclear: device topic, root topic, or arbitrary topic?; Testing still requires a mock publisher injected into the factory

## Decision Matrix

| Criterion | @app.react external reactor | Flushable protocol on state | Internal asyncio.Queue event bus | Inject AppPublisher into @app.state factory |
| --- | --- | --- | --- | --- |
| Domain purity (state objects free from I/O knowledge) | 5 | 2 | 4 | 2 |
| Test ergonomics (reactor logic testable without framework) | 5 | 2 | 3 | 2 |
| Framework dispatch control (timing owned by framework, no manual flush) | 5 | 4 | 3 | 1 |
| DI integration (reactor receives same DI as other handlers) | 5 | 1 | 4 | 2 |
| Composition root clarity (reactor visible at module top-level) | 5 | 2 | 3 | 2 |
| Implementation complexity (framework code required) | 4 | 3 | 1 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- State objects become pure domain models: no I/O parameters, no MQTT knowledge, fully unit-testable
- Reactor functions are plain async callables — call them directly in tests with real or fake arguments
- The composition root (main.py) is the single place where domain events are wired to I/O effects
- Consistent registration idiom: @app.react follows the same pattern as @app.state, @app.periodic, and @app.command
- Framework owns all dispatch timing — device handlers lose the explicit flush call, reducing boilerplate

### Negative

- Breaking change: all @app.device handlers must convert from plain coroutines to async generators (add yield after each unit of work)
- The events parameter name is reserved by the framework; user DI types named events would conflict
- Dispatch adds one drain call and zero-or-more reactor invocations per execution boundary, increasing per-iteration overhead slightly

_2026-05-04_
