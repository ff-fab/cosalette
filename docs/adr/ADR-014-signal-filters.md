# ADR-014: Signal Filters

## Status

Accepted **Date:** 2026-02-22

## Context

IoT sensors produce noisy data requiring smoothing, spike rejection, or adaptive
filtering before the readings are useful. gas2mqtt — the first cosalette application —
implements an EWMA filter in its domain layer to smooth temperature readings before
publishing.

Signal filtering is a universal IoT concern: virtually every sensor-based application
will need some form of noise reduction. The question is whether the framework should
provide filter primitives, and at what depth of integration.

### Evidence from gas2mqtt

gas2mqtt's temperature device applies an EWMA filter to raw magnetometer readings
before publishing. The filter is a ~10-line domain utility — a data transformation, not
infrastructure. It sits inside the handler, between the raw probe reading and the
returned dict:

```
raw reading → calibration → EWMA filter → round → return dict
```

This is a handler-level concern. The framework's infrastructure value — MQTT lifecycle,
error isolation, shutdown awareness, timing loop — is orthogonal to filtering.

### The EWMA Foot-Gun

Classic EWMA uses a dimensionless α parameter (`filtered = α·sample + (1-α)·filtered`),
which silently couples the filter's behavior to the sample rate. Changing the probe
interval from 1 Hz to 0.1 Hz produces radically different smoothing — a subtle bug that
is easy to miss. A PT1 (first-order low-pass) formulation parameterised by time constant
τ and sample interval dt eliminates this coupling: `α = dt / (τ + dt)`.

## Decision

Provide filters as a **utility library** (`cosalette.filters`), not as
framework-integrated decorator parameters.

Filters are domain-level data transformations, not infrastructure concerns. The
framework's job is to eliminate infrastructure boilerplate — MQTT lifecycle, error
handling, logging, timing loops. Filters are 10-line domain utilities, not 1,000-line
infrastructure. Apps import and use filters inside handlers: explicit is better than
implicit (PEP 20).

This preserves the hexagonal boundary established in ADR-006: the domain layer remains
pure. The handler returns exactly what gets published — no hidden transformations between
`return` and MQTT.

## Decision Drivers

- **Domain purity (ADR-006)** — filters are data transformations, not infrastructure
- **Hexagonal architecture** — domain logic stays in the handler; the framework provides
  MQTT, lifecycle, and error handling
- **"Explicit is better than implicit" (PEP 20)** — the handler returns exactly what
  gets published; no invisible pipeline steps
- **Broad applicability** — library filters work in `@app.telemetry`, `@app.device`,
  standalone scripts, and tests
- **Future-proof** — a declarative `filter=` parameter can be added later if real demand
  proves the need (additive change, not breaking)

## Considered Options

### Option 1: Filter Utility Library (Chosen)

Import and use filters in handlers like any other domain utility:

```python
from cosalette.filters import Pt1Filter

pt1 = Pt1Filter(tau=5.0, dt=10.0)

@app.telemetry("temperature", interval=10)
async def temperature() -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(pt1.update(raw), 1)}
```

- *Advantages:* Hexagonal purity — filters live in handler code, not framework
  internals. Explicit data flow — `return` is what gets published. Works everywhere —
  `@app.telemetry`, `@app.device`, scripts, tests. Low integration risk.
- *Disadvantages:* No boilerplate reduction for the filtering code itself. The app
  manages filter state (instantiation, lifecycle).

### Option 2: Declarative `filter=` on `@app.telemetry`

The framework applies filters in the telemetry loop, between handler return and publish
strategy:

```python
@app.telemetry("temperature", interval=10, filter=Pt1Filter(tau=5.0))
async def temperature() -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": raw}  # framework applies filter before publishing
```

- *Advantages:* Maximum boilerplate reduction — filter is declarative. Framework manages
  filter lifecycle and state.
- *Disadvantages:* Implicit transformation — the handler returns X, MQTT receives Y.
  Scope creep — what about calibration? rounding? unit conversion? Only works for
  `@app.telemetry`, not `@app.device`.

### Option 3: Both Library + Declarative

Provide both the utility library and a `filter=` decorator parameter.

- *Advantages:* Best of both — maximum flexibility.
- *Disadvantages:* Two ways to do it (PEP 20: "There should be one — and preferably
  only one — obvious way to do it"). Double API surface, documentation burden, and
  maintenance cost.

### Option 4: Protocol Only, No Implementations

Define a `Filter` protocol as an extension point but ship no concrete implementations.

- *Advantages:* Establishes extensibility without commitment.
- *Disadvantages:* Zero immediate value for users. Premature abstraction — the protocol
  design cannot be validated without implementations to test it against.

## Decision Matrix

| Criterion            | Utility Library | Declarative `filter=` | Both | Protocol only |
| -------------------- | :-------------: | :-------------------: | :--: | :-----------: |
| Domain purity        | 5               | 3                     | 4    | 5             |
| Explicit data flow   | 5               | 2                     | 3    | 5             |
| Boilerplate reduction| 2               | 5                     | 5    | 1             |
| Broad applicability  | 5               | 2                     | 4    | 3             |
| API surface          | 4               | 3                     | 2    | 5             |
| Future flexibility   | 5               | 3                     | 3    | 4             |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- **Domain purity preserved** — filters live in handler code, not framework internals
- **Sample-rate-independent PT1** eliminates the hidden EWMA foot-gun where changing the
  probe interval silently alters filter behavior
- **No framework API changes needed** — filters are importable classes, not decorator
  parameters or framework pipeline stages
- **Works in all archetypes** — `@app.telemetry`, `@app.device`, and outside the
  framework entirely
- **Testing is trivial** — filters are pure objects with no framework context required;
  `assert Pt1Filter(tau=5, dt=1).update(10.0) == pytest.approx(...)` just works
- **Future additive path** — a `filter=` parameter can be introduced later based on
  real demand from multiple framework consumers, without breaking existing code

### Negative

- **Each handler must instantiate and call filters explicitly** — the framework does not
  automate filtering
- **Filter lifecycle is the app's responsibility** — state persistence across handler
  calls requires module-level or closure-scoped filter instances
- **No framework-level pipeline visualisation** — the handler → filter → strategy → MQTT
  flow is implicit in user code, not inspectable by the framework

## Filters Provided

| Filter | Algorithm | Use case |
| ------ | --------- | -------- |
| `Pt1Filter(tau, dt)` | First-order low-pass with time constant | Noise smoothing, sample-rate-independent |
| `MedianFilter(window)` | Sliding-window median | Spike / outlier rejection |
| `OneEuroFilter(min_cutoff, beta, d_cutoff, dt)` | Adaptive 1€ Filter (Casiez 2012) | Mostly-static signals with occasional movement |

## Related Decisions

- [ADR-006](ADR-006-hexagonal-architecture.md) — Hexagonal Architecture (domain purity
  boundary)
- [ADR-010](ADR-010-device-archetypes.md) — Device Archetypes (telemetry vs device)
- [ADR-013](ADR-013-telemetry-publish-strategies.md) — Telemetry Publish Strategies
  (framework-level transmission policies, complementary to handler-level filters)
