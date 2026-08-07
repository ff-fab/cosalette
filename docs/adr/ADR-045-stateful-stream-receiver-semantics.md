---
status: Accepted
date: 2026-05-08
amended: 2026-08-07
impact: high
tags: [architecture, lifecycle, di, persistence, testing, devices]
---

# ADR-045: Stateful Stream Receiver Semantics

## Status

Accepted **Date:** 2026-05-08 | Amended **Date:** 2026-08-07
Amended **Date:** 2026-05-09 — Consolidate to single async `StreamablePort[T]`; supersedes the dual-protocol decision below.

---

## Amendment (2026-05-09): Consolidate Async-Only `StreamablePort[T]`

### Context

ADR-045 (original, below) introduced `AsyncStreamablePort[T]` alongside the existing sync `StreamablePort[T]`, with runtime detection (`is_async` flag) to call lifecycle methods via `await` or synchronously depending on which protocol the adapter implemented. This preserved backward compatibility for sync adapters.

Production experience with the dual-protocol design reveals it creates more friction than it resolves:

1. **Dual-registration surface** — authors must choose the correct protocol key (`StreamablePort[T]` vs `AsyncStreamablePort[T]`). A wrong choice registers silently and only fails at runtime with an ambiguous error.
2. **Sync lifecycle is inherently unsafe** — calling `open()` synchronously on an async adapter drops the coroutine silently. Any adapter with real I/O (serial, BLE, USB) will have async lifecycle. A sync `StreamablePort` without async shims is either trivially stateless or subtly broken.
3. **Disambiguation complexity in the runner** — `find_stream_adapter` had to inspect both protocol origins, detect ambiguity, and carry an `is_async` bool through the call chain. The resulting `_safe_call` / `push_async_callback` split added ~60 lines of branching code for a case that should not exist in well-structured code.
4. **API surface cost** — `AsyncStreamablePort` in `__all__` and docs implies a permanent, maintained interface. Keeping it increases the conceptual load for new adopters without delivering value.

All first-party streaming adapters already use async lifecycle. No downstream application has shipped a sync `StreamablePort` implementation that cannot be trivially migrated (`def open` → `async def open`).

### Decision

Remove `AsyncStreamablePort[T]` and the legacy sync `StreamablePort[T]`. Rename the async protocol to `StreamablePort[T]` — a single, async-only protocol with `async def open/close/start_scan/stop_scan` and sync `register_callback`. This is a **breaking change** scoped to the 0.4.x release boundary.

```python
class StreamablePort[T_co](Protocol):
    """Async push-callback hardware adapter protocol.

    Adapters register a callback (sync) and control the hardware lifecycle
    via awaitable methods.  The framework calls open → start_scan on entry
    and stop_scan → close on exit.  Handlers must not call lifecycle methods
    on injected concrete adapter instances.
    """

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def start_scan(self) -> None: ...
    async def stop_scan(self) -> None: ...
    def register_callback(self, callback: Callable[[T_co], None]) -> None: ...
```

`StreamablePort` is **not** `@runtime_checkable`. Adapter resolution uses generic-alias origin matching (same as before), so `isinstance` checks are not needed and are explicitly disallowed to avoid false positives on incomplete implementations.

The stream runner (`run_stream`) always `await`s lifecycle calls. The `is_async` detection branch and the `_safe_call` helper are removed. `find_stream_adapter` returns `tuple[type, object]` (no bool).

### Migration

Sync `StreamablePort[T]` adapters require one-line changes per method:

```python
# Before
def open(self) -> None:
    self._serial.open()

# After
async def open(self) -> None:
    self._serial.open()
```

The registration key is unchanged: `app.adapter(StreamablePort[SensorReading], MyAdapter)`.

### Decision Drivers (Amendment)

- All real hardware adapters use async lifecycle; the sync path served only backward compatibility, not production use cases
- Removing the sync variant eliminates the silent-coroutine-drop hazard unconditionally
- A single protocol simplifies `find_stream_adapter`, removes `_safe_call`, and reduces runner code by ~60 lines
- `AsyncStreamablePort` in `__all__` and public docs carries ongoing maintenance cost; consolidation removes it before downstream adoption grows
- Breaking changes of this scope belong at 0.4.x release boundaries; 0.4.x is the active development line

### Consequences (Amendment)

#### Positive
- Single registration key: `StreamablePort[T]`. No ambiguity between sync and async variants.
- Runner code simplified: no `is_async` detection, no `_safe_call` branching.
- Public API reduced by one export (`AsyncStreamablePort` removed from `__all__`).
- Resource-leak hazard from silent coroutine drops is eliminated at the type level.

#### Negative
- **Breaking change**: sync `StreamablePort[T]` adapters must migrate to `async def` lifecycle. Migration is mechanical but required.
- Applications that imported `AsyncStreamablePort` by name must update to `StreamablePort`.

---

## Original Decision (2026-05-08)

### Context

ADR-042 introduced `StreamablePort[T]` and `Stream[T]` as first-class framework primitives for push-callback hardware adapters (BLE, serial, HID). The design intentionally kept `@app.stream` narrower than `@app.device`: the stream runner owns the port lifecycle, injects `Stream[T]` into the handler, and the handler iterates via `async for`. No `DeviceContext`, `DeviceStore`, or concrete adapter injection was provided.

Production use of `@app.stream` (documented in the jeelink2mqtt Framework Enhancement Proposal) exposes three concrete capability gaps that prevent stateful streaming applications from using the framework as intended:

1. **DI gap** — stream handlers cannot inject `DeviceContext` (needed for publishing telemetry, state, and availability) or `DeviceStore` (needed for restoring and persisting registry state between restarts). The `start_stream_tasks` provider map in `cosalette._wiring._task_lifecycle` includes settings, adapters, state, `ClockPort`, and `Logger`, but not `DeviceContext` or `DeviceStore`.

2. **Lifecycle gap** — `StreamablePort[T]` defined five synchronous lifecycle methods (`open`, `close`, `start_scan`, `stop_scan`, `register_callback`). Hardware adapters that have already implemented async lifecycle (e.g., `async def close(self)`) could not satisfy this protocol without adding a synchronous shim. The stream runner called these methods synchronously via `_safe_call`, which silently dropped the coroutine returned by an async implementation — causing resource leaks (e.g., serial ports not closed on shutdown).

3. **Testing gap** — `AppHarness.inject_stream` built a provider map limited to settings, state overrides, `ClockPort`, and `Logger`. Integration tests could not verify publishing, persistence, or concrete adapter interaction without stepping outside the framework's DI system.

The original decision introduced `AsyncStreamablePort[T]` with async lifecycle and runtime protocol detection to address gap (2) and preserve backward compatibility. Gaps (1) and (3) were closed by injecting `DeviceContext`/`DeviceStore` and improving `inject_stream`. The 2026-05-09 amendment supersedes the `AsyncStreamablePort` introduction by consolidating to a single async protocol.

### Decision (Original)

*(Superseded by amendment above for the dual-protocol design. DI injection for `DeviceContext`, `DeviceStore`, and concrete adapters, and `AppHarness.inject_stream` parity, remain in effect as originally decided.)*

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

## Amendment (2026-08-07) — Additive

**Rationale:** ADR-045 gave `@app.stream` a `DeviceContext` and therefore the ability to publish to a static retained `{prefix}/{stream}/state` topic, but it did not revisit the decorator's contract metadata. The metadata set for `@app.stream` was fixed on 2026-04-27 (commit 0eaa52e), when streams had no `DeviceContext` and could not publish at all. The result is a capability without a contract: `@app.stream` is the only publishing archetype that accepts no `state_model`, so a stream can ship a malformed state payload to a retained topic and nothing in the framework notices. Stream handlers are async generators yielding `None`, so `get_return_annotation` can never supply a fallback type — an explicit `state_model` is the only possible contract source. The same gap exists in mirror image on `@app.device`, which has accepted `state_model` since 0.5.6 but treated it as introspection metadata only, even though device handlers publish through the same `DeviceContext.publish_state`. This amendment closes both, and records the applicability judgement for the generated artifacts that epic cos-bnq left implicit.

### Additional Sub-Decision: `state_model` on `@app.stream` and Runtime Validation in `publish_state`

`@app.stream` (and `@router.stream`, `App.add_stream`) accepts `state_model: type | None = None`. When declared, the model is threaded from `_StreamRegistration` through `build_stream_contexts` onto the stream-scoped `DeviceContext`, and every `ctx.publish_state()` call from that handler is validated and normalised against it via Pydantic `TypeAdapter`, raising `ReturnValidationError` on a mismatch. This is the same engine and the same exception type telemetry and commands already use (ADR-046), reached by a different route.

The route has to be different. Telemetry and commands validate a *handler return value* through `normalize_handler_return`, which takes an EAFP `dump_python` fast path — free for values that are already model instances. Stream handlers return nothing; they hand `publish_state` a `dict[str, object]` they assembled themselves. `dump_python` on a plain dict against a `BaseModel` adapter does **not** validate: Pydantic emits a serializer warning and passes the dict straight through. The published-state path therefore always `validate_python`s first, then dumps. A new `validate_state_payload()` helper in `_runners/_contracts.py` encapsulates this, and is deliberately separate from `normalize_return` so the two orderings cannot be conflated.

Validation errors name the offending field paths, the model, and the handler, e.g. `Published state does not match state_model 'Reading' in handler 'app.receiver': value: type=missing`. As with `PayloadValidationError`, the message is built only from framework-owned data (field location codes and Pydantic error-type codes) and never echoes the rejected payload (OWASP A03).

`state_model=None` — the default and the behaviour of every handler written before this amendment — skips the path entirely. No `TypeAdapter` is built, no branch cost beyond a single `is not None` check per publish.

Scope is the static `state` topic only. `ctx.publish(channel, payload)` is an explicit escape hatch and `ctx.sub_entity(...)` channels carry their own shapes; neither is validated by the device-level `state_model`.

### Additional Sub-Decision: `@app.device`'s `state_model` Becomes Load-Bearing Too (Breaking)

The same wiring is applied to `@app.device`: `build_contexts` installs `state_model` on the `DeviceContext` for `_DeviceRegistration` entries. Declaring `state_model` on `@app.device` therefore now validates published state, where before 0.6.0 it only typed the AsyncAPI state channel emitted by `cosalette schema init`.

Scoping this to streams alone was considered and rejected. It would have left an identically inert `state_model` on `@app.device` sitting immediately beside the one being fixed — the exact defect this work exists to close — and would have forced users to learn a per-decorator matrix instead of one rule: **if you declare `state_model`, published state is validated.**

Telemetry and command registrations deliberately contribute nothing to the context. Their `state_model` already validates the handler return value before `publish_state` is reached; re-validating the resulting JSON dict would check the same contract twice. The split is safe because device names collide with every other registration kind (`colliding_names`), so a device never shares a `DeviceContext` with telemetry or a command — the model installed on a context is always unambiguous.

This is a **breaking change** for device handlers that declare `state_model` and publish non-conforming payloads. Those handlers are shipping malformed state to a retained topic today; the break surfaces an existing defect rather than introducing one. Migration is one of two one-line choices: fix the payload to match the model, or drop `state_model=` to return to unvalidated publishing. Handlers that never declared `state_model` are unaffected.

`AppHarness._make_stream_ctx` threads `state_model` as well, preserving the production-DI parity for `inject_stream` that this ADR's original decision established — a contract violation fails in tests exactly as it would at runtime.

### Additional Sub-Decision: Applicability Judgement: Introspection Yes, AsyncAPI No

Epic cos-bnq listed "streams and reactors where applicable" in scope and shipped without them, leaving no record of the applicability judgement. `summary`/`behavior`/`effects` on `@app.stream` and `summary`/`behavior` on `@app.periodic` were consequently stored on the frozen registration dataclass and read by nothing — not `app.asyncapi()`, not the manifest, not the MCP inspect tool, not schema init. The `@app.stream` docstring's "Informational only" read as "appears in the manifest" rather than "discarded". The judgement is recorded here.

**Introspection: yes.** `build_registry_snapshot` gains `streams` and `periodic` sections, with matching `Streams` and `Periodic` tables in `format_registry_table`. Stream entries carry `summary`, `state_model`, `behavior`, `effects`, `maxsize` and `backpressure`; periodic entries carry `summary`, `behavior` and `interval`. The metadata now reaches the `cosalette_inspect_app` MCP tool and the public `build_registry_snapshot` / `format_registry_table` API. It does not reach `cosalette manifest`, which emits AsyncAPI (see below). The decorator docstrings are corrected to say so.

**AsyncAPI: no, and not because of "dynamic per-sensor topics".** That claim in `_schema/_cli.py` was false — a stream publishes to the same static `{prefix}/{name}/state` topic a device does — and it cited ADR-033, which says nothing on the subject. The real reasons: `x-cosalette-archetype` is a closed enum `{telemetry, command, device}` validated by the schema loader, so a stream channel has no representable archetype and older cosalette versions would reject a document containing one; and AsyncAPI is not documentation here but the artifact `schema check` gates against and `schema ha-discovery` derives Home Assistant entities from, so emitting stream channels would silently add HA entities on the next regeneration. Adding a fourth archetype is a defensible future change — `state_model` is precisely the static signal that would make it sound — but it is a cross-version schema-compatibility decision that needs its own ADR, not a side effect of this one. Until then, `schema check` continues to subtract stream names from its EXTRA comparison, now with an accurate comment.

**Periodic in AsyncAPI: categorically no.** Periodic tasks have no MQTT presence by design (ADR-041), which is also why they carry no `state_model`, `payload_model`, or `effects`.

### Additional Positive Consequences

- @app.stream publishes under a declarable, enforced contract: a malformed state payload fails loudly at the publish call instead of reaching a retained MQTT topic and propagating to every subscriber
- One rule across all publishing archetypes — declare state_model and published state is validated — replacing a per-decorator matrix of which contract fields are load-bearing
- Validation errors name the offending fields, the model, and the handler, while never echoing the rejected payload (OWASP A03), matching the diagnostic quality of the inbound PayloadValidationError path
- Contract metadata on @app.stream and @app.periodic reaches a real artifact (the registry snapshot, surfaced by cosalette_inspect_app) instead of being stored on the registration and discarded
- The false 'dynamic per-sensor topics (ADR-033)' rationale for excluding streams from AsyncAPI is replaced by the actual reason, and the applicability judgement epic cos-bnq left implicit is now recorded
- AppHarness.inject_stream validates published state exactly as production does, so contract violations surface in tests rather than in the field

### Additional Negative Consequences

- Breaking change: @app.device handlers that declare state_model and publish non-conforming payloads now raise ReturnValidationError where they previously published silently — migration is to fix the payload or drop state_model=
- Published state is normalised, not merely checked: a declared model applies field aliases, custom serialisers, and type coercion (an int 3 for a float field publishes as 3.0), so the wire payload can differ from the dict the handler passed in
- Two validation entry points now exist for published state — normalize_return for handler return values and validate_state_payload for publish_state payloads — and their differing dump/validate ordering is a subtlety future maintainers must respect
- state_model validation is confined to the static state topic; ctx.publish() and sub-entity channels remain unvalidated, so the contract does not cover a device's full publish surface
- Streams remain absent from AsyncAPI, so a stream's declared state_model does not yet reach schema check, ha-discovery, or openHAB generation — a follow-up ADR is required to close that asymmetry
