---
status: Accepted
date: 2026-05-06
impact: high
tags: [architecture, mqtt, devices, lifecycle, di, documentation]
---

# ADR-044: Public Router and Composition API

## Status

Accepted **Date:** 2026-05-06 | Amended **Date:** 2026-08-07

## Context

cosalette is a decorator-first MQTT framework. All operation registrations currently attach directly to an `App` instance (`@app.telemetry`, `@app.command`, `@app.device`, `@app.stream`, `@app.periodic`, `@app.react`). This model works well for single-file or small applications but has no composition primitive for multi-module projects. There is no way to define a group of related operations in a separate module and attach them to an App with a shared topic prefix, tags, or adapter declarations without importing the `App` instance directly, which creates circular import risk and makes operation grouping an informal convention.

The upcoming breaking release (`cos-s2q` epic) targets an idiomatic FastAPI-for-MQTT experience. FastAPI's `APIRouter` / `app.include_router` pattern is the dominant ergonomic model for composing route groups in Python web frameworks and is well understood by the target audience. Adopting it for cosalette closes the ergonomic gap without requiring users to learn a new mental model.

The internal `TopicRouter` class (`cosalette._mqtt._router.TopicRouter`) already exists as a subscription-dispatch primitive but is not suitable for exposure as the public composition API: it carries MQTT subscription semantics and internal wiring that would become public contract obligations if exported.

Key constraints captured in decision bead `cos-s2q.1`:
- App-level decorators must remain first-class and must not be deprecated in this release.
- A Router prefix must be a single MQTT topic segment (no `/` character, no wildcards).
- Tags are additive across three layers: Router constructor, `include_router` call, and operation decorator.
- The `dependencies=` parameter is reserved for the dependency-injection epic (`cos-ebc`) and must raise `NotImplementedError` if passed before that epic ships.
- Stream adapter validation moves from decoration time to application startup (implemented in `cos-s2q.4`).
- Nested router composition (`Router.include_router`) is deferred out of scope.
- The public validation entry-point name is deferred to `cos-s2q.4`.

## Decision

Introduce `cosalette.Router` as the public composition primitive and `App.include_router(router, *, prefix=None, tags=None, dependencies=None, adapters=None)` as the inclusion method on `App`. All MQTT-native operation decorators present on `App` (`telemetry`, `command`, `device`, `stream`, `periodic`, `react`) are replicated on `Router` with identical signatures and semantics. App-level decorators remain first-class and are not deprecated. The Router prefix is restricted to a single MQTT topic segment, validated by the existing `validate_mqtt_name` rules. Tags accumulate additively from Router constructor through `include_router` call to operation decorator, deduplicated while preserving insertion order. The `dependencies=` parameter is reserved and raises `NotImplementedError` until `cos-ebc` ships. Adapter declarations in `adapters=` are merged into the app's registry at include time with conflict detection. `include_router` uses snapshot semantics: registrations are captured at call time. Stream adapter validation is deferred to application startup (`cos-s2q.4`). Nested routers are deferred.

```python
# sensors.py — define operations in a separate module
router = cosalette.Router(prefix="sensors", tags=["environment"])

@router.telemetry("temperature", interval=30)
async def read_temperature() -> dict:
    return {"celsius": await sensor.read()}

@router.command("calibrate")
async def calibrate(ctx: cosalette.CommandContext) -> None:
    await sensor.calibrate()

# main.py — compose modules into the app
app = cosalette.App("bridge")
app.include_router(router)
# → publishes to: bridge/sensors/temperature/state
# → subscribes to: bridge/sensors/calibrate/cmd

# Small-app pattern: still idiomatic, unchanged
@app.telemetry("heartbeat", interval=60)
async def heartbeat() -> dict:
    return {"up": True}
```

## Decision Drivers

- Multi-module cosalette applications have no composition primitive; each module must import the `App` instance directly, creating circular-import risk and tight coupling
- FastAPI's `APIRouter` / `app.include_router` pattern is the recognised ergonomic model for Python framework composition and matches the target audience's mental model
- MQTT-native operation archetypes (`telemetry`, `command`, `device`, `stream`, `periodic`, `react`) must remain first-class rather than being replaced by generic HTTP-style route objects
- App-level decorators service the single-file small-app pattern and must not be removed or deprecated in a breaking release that is otherwise additive
- Topic composition must be deterministic and MQTT-safe: prefixes are validated segments, tags accumulate without surprising overrides, and adapter conflicts surface at include time rather than at startup
- The `dependencies=` boundary with the `cos-ebc` epic must be explicit in the public API so that implementers and downstream AI tooling have a clear, authoritative reference

## Considered Options

### Option 1: Public Router with MQTT-native decorators (chosen)

Introduce `cosalette.Router` as an explicit public class and `App.include_router(...)` as the composition method. Router exposes the full MQTT-native decorator surface (`telemetry`, `command`, `device`, `stream`, `periodic`, `react`) identical to App. Prefix is a single topic segment. Tags are additive. App-level decorators remain unchanged and first-class.

- *Advantages:* Matches the FastAPI `APIRouter` mental model exactly — low learning curve for the target audience; MQTT-native decorator names are preserved on Router, keeping the framework vocabulary consistent; Additive change: single-file apps using only App decorators require no changes; Prefix-as-topic-segment constraint is MQTT-safe and easy to document with a concrete composition example; Snapshot semantics at include time make behaviour deterministic and consistent with FastAPI; Clear boundary with `cos-ebc` (`dependencies=` reserved) prevents premature API surface expansion
- *Disadvantages:* Two registration surfaces (`App` and `Router`) share decorator logic; refactor to a shared mixin is required in `cos-s2q.3` to avoid divergence; Multi-level topic namespacing requires multiple routers rather than a single multi-segment prefix string; Nested router composition is a user expectation from FastAPI that is explicitly deferred, which may frustrate early adopters

### Option 2: Generic topic-route operations

Replace all MQTT-native decorators on both App and Router with a single generic `@app.route(topic_pattern, ...)` or `@app.topic(...)` decorator. Operation type (telemetry, command, etc.) is inferred from the handler signature or specified via a `mode=` parameter.

- *Advantages:* Single registration surface reduces API surface area; Familiar to developers coming from generic HTTP frameworks
- *Disadvantages:* Destroys the MQTT-native vocabulary that is a core cosalette differentiator (`telemetry`, `command`, `device` map directly to MQTT archetypes in ADR-010); Loses static type information carried by archetype-specific decorator signatures; Breaking change removes the existing App decorator API, violating the compatibility stance; Degrades docs/AI teachability: generic topic routes do not communicate intent or archetype to human readers or AI tooling

### Option 3: App-only with improved docs and contracts

Keep the existing app-level decorator API unchanged. Improve documentation, add explicit module-organisation guides, and publish a pattern library showing how to structure multi-module apps using App injection via dependency injection or module-level accessors.

- *Advantages:* No new API surface — zero migration cost; Eliminates the implementation risk of the Router and include_router changes
- *Disadvantages:* Does not solve the circular-import problem for multi-module apps; Lacks a testable composition boundary: each module still requires an App reference at import time; Misses the FastAPI-for-MQTT ergonomic target that is the stated goal of the `cos-s2q` epic; Downstream frameworks and AI tooling (`cos-zo3`) cannot generate router-composition scaffolding without a canonical API

### Option 4: Expose internal TopicRouter as public primitive

Promote the existing internal `cosalette._mqtt._router.TopicRouter` to a public `cosalette.TopicRouter` and document it as the composition entry point.

- *Advantages:* Reuses existing code with minimal new implementation; No parallel decorator surface to maintain
- *Disadvantages:* `TopicRouter` carries MQTT subscription-dispatch internals that are not appropriate public API surface; exposing it creates a leaky abstraction; The name `TopicRouter` is already in use internally and would become a public contract obligation, blocking future refactors; Does not replicate the MQTT-native decorator surface, so callers must use lower-level registration primitives; Diverges from the FastAPI naming convention, reducing the ergonomic benefit of the change

## Decision Matrix

| Criterion | Public Router with MQTT-native decorators | Generic topic-route operations | App-only with improved docs and contracts | Expose internal TopicRouter as public primitive |
| --- | --- | --- | --- | --- |
| FastAPI-like ergonomics | 5 | 4 | 1 | 2 |
| MQTT domain fit | 5 | 2 | 5 | 3 |
| Migration cost | 5 | 1 | 5 | 3 |
| Implementation risk | 3 | 2 | 5 | 4 |
| Docs and AI teachability | 5 | 2 | 3 | 2 |
| Future typed-contract compatibility | 4 | 3 | 2 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Multi-module cosalette apps gain a clean, testable composition boundary: `Router` instances can be defined and unit-tested in isolation without importing or mocking an `App`
- The FastAPI-for-MQTT ergonomic goal of the `cos-s2q` epic is achieved; `cosalette.Router` and `App.include_router` are immediately recognisable to FastAPI users
- Single-file small-app patterns using app-level decorators are unaffected; no migration is required for existing codebases
- MQTT topic composition is deterministic: prefix validation, tag accumulation, and snapshot semantics are fully specified in this ADR and in the design notes of `cos-s2q.1`
- Adapter conflict detection surfaces at `include_router()` call time rather than at startup, providing early feedback without requiring a full app bootstrap
- The `cos-zo3` AI guidance system (`cosalette ai`) and Zensical documentation can generate router-composition scaffolding with a canonical, documented API surface (tracked in `cos-zo3.3` and `cos-zo3.6`)
- The reserved `dependencies=` boundary with `cos-ebc` is explicit in the public API and in this ADR, preventing accidental coupling to the dependency-injection epic during Router implementation

### Negative

- Two registration surfaces (`App` and `Router`) must be kept in sync; a shared internal registration mixin is required in `cos-s2q.3` to prevent decorator signature divergence
- Stream adapter validation moves from decoration time to application startup (`cos-s2q.4`), which is a breaking change for tests that relied on `@app.stream` raising `TypeError` at import time
- Nested router composition (`Router.include_router`) is explicitly deferred; users expecting full FastAPI `APIRouter` feature parity may file follow-on issues
- The public validation entry-point name is deferred to `cos-s2q.4`: tests and templates that need pre-flight validation must use `app.run()` until that task ships
- Documentation must be updated in multiple places: public API reference, migration guide, getting-started guides, `cosalette ai` help topic (`cos-zo3.3`), `ai prime` what's-new entry (`cos-zo3.6`), and Zensical site (`cos-bnq`)
- AsyncAPI schema generation (`cos-bnq`) depends on the tag vocabulary established here; tag naming conventions (lowercase, hyphen-separated) must be enforced now to avoid a later rename

## Amendment (2026-08-07) — Corrective

**Rationale:** The reserved `dependencies=` parameter is retracted and removed from the public API. ADR-044 reserved `dependencies=` on `Router.__init__`, `App.include_router`, and every Router operation decorator, requiring it to raise `NotImplementedError` until the dependency-injection epic (`cos-ebc`) shipped. `cos-ebc` closed on 2026-05-09 with all five children complete, delivering dependency injection through parameter-level markers — `Depends()`, `Payload()`, `Topic()`, `Message()` — as recorded in ADR-046. The Router implementation landed on 2026-05-10, one day *after* the epic it deferred to had already completed, so the reservation has never had a live target. In its shipped form the parameter is a defect on three counts: it advertises a capability the framework does not deliver and points users at a closed epic; the value it accepts is stored in a `self._dependencies` field that is never read anywhere in the codebase; and it is asymmetric with `App`, whose operation decorators never accepted the keyword at all, so `@router.command(dependencies=[...])` raised `NotImplementedError` while `@app.command(dependencies=[...])` raised `TypeError`. Router-level dependency declaration is not being replaced by a new mechanism: DI is expressed per handler parameter via `Depends()`, which is the pattern ADR-046 already establishes and which needs no router-level collection point.

> **Justification for amendment (not supersession):** Supersession is not warranted because the decision recorded in ADR-044 is otherwise unchanged and remains fully implemented: `cosalette.Router` as the public composition primitive, `App.include_router` as the inclusion method, MQTT-native operation decorators replicated from `App`, single-segment prefix validation, additive tag accumulation across the three layers, snapshot inclusion semantics, and adapter merging with conflict detection all stand exactly as decided. Marking the whole ADR superseded would misrepresent the architecture by implying the composition API itself had been replaced, when a single reserved-parameter clause is being retracted. The impact analysis supports a corrective amendment: the parameter never had an implementation, so the only reachable behaviour for a non-empty argument was an immediate `NotImplementedError` at decoration or construction time. No working downstream code can depend on it — any caller that passed a real value already crashed at import. Removal changes the failure mode from `NotImplementedError` to `TypeError: unexpected keyword argument`, which is a signature-level breaking change shipped under a MINOR version bump, and the migration path (`Depends()`) already exists and is documented. The change touches one module tree (`cosalette._router`) plus the `include_router` signature on `App`.

### Revised Decision

Remove the reserved `dependencies=` parameter from the entire public composition surface. `Router.__init__` and `App.include_router` become `(*, prefix=None, tags=None, adapters=None)`; the Router operation decorators (`telemetry`, `command`, `device`, `stream`, `periodic`) drop the keyword entirely, matching `react`, which never accepted it. The dead `self._dependencies` field is deleted rather than read. Passing `dependencies=` now raises `TypeError: unexpected keyword argument` from Python's own argument binding, identically on `Router` and `App`, which removes the asymmetry ADR-044 left behind. Dependency injection is declared per handler parameter with `Depends()` (ADR-046) and needs no router-level or include-level collection point; the rest of the ADR-044 decision is unaffected. If a future need for router-scoped dependency declaration is established, it will be introduced as an additive change with a working implementation behind it and recorded in its own ADR — the framework does not carry reserved keywords for unbuilt features.

```python
# Before — reserved keyword that could only ever raise
router = cosalette.Router(prefix="sensors", dependencies=None)

@router.command("calibrate", dependencies=[])   # NotImplementedError if non-empty
async def calibrate() -> None: ...

app.include_router(router, dependencies=None)


# After — the keyword is gone; DI is per parameter
router = cosalette.Router(prefix="sensors", tags=["environment"])

@router.command("calibrate")
async def calibrate(
    sensor: Annotated[Sensor, cosalette.Depends(get_sensor)],
) -> None:
    await sensor.calibrate()

app.include_router(router, tags=["production"])
```

!!! note "Editorial note (2026-08-07)"
    The constraint in the Context section of this ADR — "The `dependencies=` parameter is reserved for the dependency-injection epic (`cos-ebc`) and must raise `NotImplementedError` if passed before that epic ships" — is retracted by this amendment, along with the corresponding clauses in the original Decision, Decision Drivers, Option 1 advantages, and Positive Consequences. Those passages are preserved above as the historical record of the 2026-05-06 decision and must be read together with this amendment. Implemented in `cos-v1dj.3`.

### Additional Positive Consequences

- The public composition surface no longer advertises a capability the framework does not deliver, and no public parameter references a closed epic (`cos-ebc`)
- `Router` and `App` now behave identically for the removed keyword — both raise `TypeError: unexpected keyword argument` — closing the asymmetry that ADR-044 introduced by putting `dependencies=` on `Router` but not on `App`'s operation decorators
- The dead `Router._dependencies` field is gone; there is no stored state that is written and never read
- Dependency injection has exactly one documented mechanism (`Depends()` per handler parameter, ADR-046), so AI tooling, generated scaffolding, and documentation no longer present two competing answers to the same question
- Removing the guard clauses deletes five near-identical validation blocks from the Router decorator modules and shrinks each decorator signature and docstring

### Additional Negative Consequences

- Breaking change: any code passing `dependencies=` to `Router(...)`, `App.include_router(...)`, or a Router operation decorator now raises `TypeError` at call time instead of `NotImplementedError`. In practice only `dependencies=None` and `dependencies=[]` were reachable without an exception, so affected call sites simply delete the argument
- Router-scoped dependency declaration is no longer signposted anywhere in the API, so FastAPI users who expect `APIRouter(dependencies=[...])` get no in-signature hint that `Depends()` is the cosalette answer; the router documentation and `cosalette ai help` topics must carry that pointer instead
- If router-scoped dependency declaration is later wanted, it must be reintroduced as a new parameter rather than filling in a placeholder that callers were already passing
