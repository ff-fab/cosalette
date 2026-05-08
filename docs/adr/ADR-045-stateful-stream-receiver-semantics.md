---
status: Accepted
date: 2026-05-08
impact: high
tags: [architecture, lifecycle, di, persistence, testing, devices]
---

# ADR-045: Stateful Stream Receiver Semantics

## Status

Accepted **Date:** 2026-05-08

## Context

ADR-042 introduced `StreamablePort[T]` and `Stream[T]` as first-class framework primitives for push-callback hardware adapters (BLE, serial, HID). The design intentionally kept `@app.stream` narrower than `@app.device`: the stream runner owns the port lifecycle, injects `Stream[T]` into the handler, and the handler iterates via `async for`. No `DeviceContext`, `DeviceStore`, or concrete adapter injection was provided.

Production use of `@app.stream` (documented in the jeelink2mqtt Framework Enhancement Proposal) exposes three concrete capability gaps that prevent stateful streaming applications from using the framework as intended:

1. **DI gap** — stream handlers cannot inject `DeviceContext` (needed for publishing telemetry, state, and availability) or `DeviceStore` (needed for restoring and persisting registry state between restarts). The `start_stream_tasks` provider map in `cosalette._wiring._task_lifecycle` includes settings, adapters, state, `ClockPort`, and `Logger`, but not `DeviceContext` or `DeviceStore`.

2. **Lifecycle gap** — `StreamablePort[T]` defines five synchronous lifecycle methods (`open`, `close`, `start_scan`, `stop_scan`, `register_callback`). Hardware adapters that have already implemented async lifecycle (e.g., `async def close(self)`) cannot satisfy this protocol without adding a synchronous shim. The stream runner calls these methods synchronously via `_safe_call`, which silently drops the coroutine returned by an async implementation — causing resource leaks (e.g., serial ports not closed on shutdown).

3. **Testing gap** — `AppHarness.inject_stream` builds a provider map limited to settings, state overrides, `ClockPort`, and `Logger`. Integration tests cannot verify publishing, persistence, or concrete adapter interaction without stepping outside the framework's DI system.

The root cause is not that `@app.stream` was designed incorrectly — ADR-042's `Queue + Event` bridge is sound — but that the runtime and test harness were not extended to match the DI surface that `@app.device` provides. Without these extensions, authors of stateful streaming applications must either re-implement framework-owned concerns outside the framework (hand-rolling MQTT publish, persistence) or fall back to `@app.device` with explicit async context-manager and iterator boilerplate, defeating the purpose of the stream archetype.

## Decision

Adopt targeted stateful stream capability parity for `@app.stream`. Extend the stream runtime (`start_stream_tasks`, `run_stream`) to inject stream-scoped `DeviceContext` and `DeviceStore` into the DI provider map when the handler declares them. Introduce `AsyncStreamablePort[T]` alongside the existing sync `StreamablePort[T]`, with async lifecycle methods (`open`, `close`, `start_scan`, `stop_scan`); the stream runner detects which protocol the adapter implements and calls lifecycle methods accordingly, preserving backward compatibility for sync adapters. Allow concrete adapter injection for non-lifecycle operations (e.g., `jeelink: JeeLinkPort` for `set_led`) while the framework retains exclusive lifecycle ownership via the registered port protocol key — handlers must not call lifecycle methods on injected concrete adapters. Improve `AppHarness.inject_stream` to include `DeviceContext`, `DeviceStore`, and adapter providers, achieving production DI parity while still bypassing hardware lifecycle. Explicitly reject making `@app.stream` a full clone of `@app.device`: the async-generator / async-iterable handler model, yield-based reactor dispatch boundaries, and `Stream[T]`-mediated push-to-pull bridge are preserved unchanged.

```python
@app.stream(summary="JeeLink LaCrosse serial receiver")
async def receiver(
    ctx: DeviceContext,
    stream: Stream[SensorReading],
    store: DeviceStore,
    jeelink: JeeLinkPort,          # concrete adapter for non-lifecycle ops
    settings: AppSettings,
) -> None:
    """Stateful push-callback receiver with DI parity.

    Framework responsibilities (not visible here):
    - AsyncStreamablePort[SensorReading] lifecycle: open/start_scan on entry,
      stop_scan/close on exit
    - DeviceContext and DeviceStore constructed and injected by the runtime
    - DeviceStore saved on graceful shutdown
    """
    # Restore persisted state before the first reading arrives
    registry.restore_from(store)

    async for reading in stream:
        result = registry.record(reading)
        if result.is_mapped:
            await ctx.publish_state({"sensor": result.name, "value": reading.value})
        store.mark_dirty()
        yield  # reactor dispatch boundary (ADR-043)

    # jeelink.set_led(False) is legal — framework does NOT call lifecycle via jeelink
```

## Decision Drivers

- Stateful streaming receivers need DeviceContext for MQTT publishing and DeviceStore for persistence — without these, @app.stream cannot be used for non-trivial IoT bridges
- Hardware adapters with async lifecycle must satisfy StreamablePort without synchronous shims; silent coroutine drops in _safe_call are a resource-leak hazard
- The stream archetype's async-generator model and Stream[T] push-to-pull bridge are correct and must be preserved; capability gaps should be closed, not redesigned
- AppHarness.inject_stream must provide production-equivalent DI so integration tests can verify publishing, persistence, and adapter behaviour without stepping outside the framework
- Lifecycle ownership must remain exclusively with the framework — handlers that inject concrete adapters must not be able to call lifecycle methods on them, to avoid double-open or unclosed resources
- Backward compatibility for existing sync StreamablePort[T] adapters must be preserved unconditionally

## Considered Options

### Option 1: App-side shim (no framework change)

Leave the framework unchanged. Application code adds thin wrapper functions that create DeviceContext, DeviceStore, and async lifecycle adapters outside the DI system. Shared boilerplate is published as a utility library or documented pattern for downstream apps.

- *Advantages:* Zero framework change: no regression risk, immediately available; Application authors retain full control over lifecycle and DI wiring; No API surface expansion in the framework
- *Disadvantages:* Every stateful streaming app re-invents the same lifecycle and DI boilerplate; Lifecycle ownership is ambiguous: the framework and the shim both touch the port; AppHarness.inject_stream cannot be improved without touching the framework, so testing gaps persist; AsyncStreamablePort adapters still require a synchronous shim to satisfy StreamablePort[T], keeping the resource-leak hazard

### Option 2: Full device-equivalent @app.stream

Make @app.stream a superset of @app.device: expose the same DI surface, lifecycle management, reactor dispatch, topic management, health reporting, and restart strategy. Stream[T] becomes an optional bridge rather than the primary handler model.

- *Advantages:* Maximum capability parity: stream handlers can do everything device handlers can; Single mental model for all stateful handlers: same DI, same lifecycle, same testing idioms
- *Disadvantages:* Conflates two distinct archetypes: push-callback hardware bridges and polled device loops serve different hardware contracts; Breaks the Stream[T] async-generator boundary that ADR-042 established as the canonical pattern; @app.device already exists for full-featured stateful handlers; duplicating it adds maintenance surface; High implementation risk: lifecycle, health, restart, and topic semantics must all be replicated or generalised; Breaks backward compatibility for existing @app.stream handlers if the handler model changes from async generator to coroutine

### Option 3: Targeted stateful stream parity (chosen)

Extend the @app.stream runtime with the minimum changes needed to close the three capability gaps: (1) add DeviceContext and DeviceStore to the DI provider map; (2) introduce AsyncStreamablePort[T] alongside sync StreamablePort[T] with runtime detection; (3) allow concrete adapter injection for non-lifecycle operations; (4) improve AppHarness.inject_stream DI parity. The async-generator handler model, Stream[T] bridge, and yield-based reactor boundaries are preserved unchanged.

- *Advantages:* Closes all three concrete capability gaps with targeted, auditable changes; Preserves Stream[T] async-generator model and yield-based reactor dispatch — no handler migration required; Backward compatible: existing sync StreamablePort[T] adapters require no changes; Lifecycle ownership remains exclusively with the framework — concrete adapter injection is scope-limited to non-lifecycle operations; AppHarness.inject_stream parity enables full integration test coverage without stepping outside the DI system
- *Disadvantages:* Two port protocols (StreamablePort[T] and AsyncStreamablePort[T]) increase DI surface complexity; Partial parity with @app.device may prompt questions about remaining gaps (health reporting, restart strategy) not addressed here; Concrete adapter injection requires clear documentation to prevent misuse of lifecycle methods

### Option 4: Keep @app.stream narrow, use @app.device

Formalize @app.stream as a narrow, stateless-only archetype. Document that stateful streaming receivers belong in @app.device, which already has full DI support, and provide guidance on using async context managers and async for inside @app.device handlers for push-callback adapters.

- *Advantages:* Framework stays simple: no new protocol, no DI expansion for streams; Clear archetype boundary: @app.stream for stateless bridges, @app.device for stateful receivers; @app.device already provides DeviceContext, DeviceStore, and full DI — no new code required
- *Disadvantages:* Forces @app.device boilerplate (explicit async context manager, iterator wiring) on every push-callback receiver, eliminating the ergonomic benefit of @app.stream; StreamablePort[T] lifecycle management, the primary @app.stream value proposition, becomes opt-out rather than the default; Does not resolve the async lifecycle hazard (sync shim requirement) for adapters registered under either archetype; Contradicts the FEP's conclusion that @app.stream is the correct long-term archetype for push-callback receivers

## Decision Matrix

| Criterion | App-side shim (no framework change) | Full device-equivalent @app.stream | Targeted stateful stream parity | Keep @app.stream narrow, use @app.device |
| --- | --- | --- | --- | --- |
| Architecture fit (hexagonal, lifecycle separation) | 2 | 2 | 5 | 3 |
| Backward compatibility (existing handlers unmodified) | 5 | 1 | 5 | 5 |
| Stateful receiver support (DeviceContext, DeviceStore, adapter injection) | 2 | 5 | 4 | 2 |
| Testing ergonomics (inject_stream DI parity, no harness bypass) | 1 | 4 | 4 | 3 |
| Lifecycle clarity (framework owns lifecycle, no confusion) | 1 | 3 | 4 | 3 |
| Implementation risk (complexity, regression surface) | 5 | 1 | 4 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- @app.stream handlers can now publish telemetry, state, and availability to MQTT via injected DeviceContext without any boilerplate outside the framework
- DeviceStore injection enables stateful stream receivers to restore and persist registry state across restarts, achieving functional parity with @app.device for the common IoT bridge pattern
- AsyncStreamablePort[T] eliminates the resource-leak hazard where synchronous _safe_call silently dropped coroutines returned by async lifecycle methods
- Backward compatibility for all existing sync StreamablePort[T] adapters is unconditionally preserved — no migration required
- AppHarness.inject_stream DI parity enables complete integration test coverage (publishing, persistence, adapter interaction) within the framework's test infrastructure
- The Stream[T] async-generator model and yield-based reactor dispatch boundaries (ADR-043) are preserved, keeping the push-callback bridge pattern as the canonical @app.stream idiom

### Negative

- Two registered port protocol keys (StreamablePort[T] and AsyncStreamablePort[T]) increase adapter registration surface; documentation must clearly state which key to use for each adapter type
- Concrete adapter injection for non-lifecycle operations requires explicit documentation to prevent misuse — handlers must not call lifecycle methods on the injected adapter instance, which the framework cannot statically enforce
- Partial parity with @app.device (health reporting and restart strategy remain @app.device-only) may prompt future FEP requests to close remaining gaps

_2026-05-08_
