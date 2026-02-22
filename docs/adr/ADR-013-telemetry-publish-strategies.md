# ADR-013: Telemetry Publish Strategies

## Status

Accepted **Date:** 2026-02-22

## Context

The current `@app.telemetry` decorator conflates **probing frequency** with **publishing
frequency** — every handler invocation results in an MQTT publish. This makes
`@app.telemetry` unsuitable for the large class of sensors that need fast probing but
infrequent or conditional publishing.

### Evidence from gas2mqtt (First Framework Consumer)

gas2mqtt has three devices, and two of them fell back to `@app.device` with manual loops
because `@app.telemetry` could not express their publishing needs:

| Device         | Registered as  | Why not `@app.telemetry`?                                            |
| -------------- | -------------- | -------------------------------------------------------------------- |
| `gas_counter`  | `@app.device`  | Polls at 1 Hz, publishes **only on Schmitt trigger edge events**     |
| `temperature`  | `@app.device`  | Polls at 1 Hz, applies EWMA filter, publishes every 300 s           |
| `magnetometer` | `@app.device`  | Polls at 1 Hz, publishes at 1 Hz (could use `@app.telemetry`)       |

Both `gas_counter` and `temperature` duplicated exactly the boilerplate that
`@app.telemetry` was designed to eliminate: shutdown-aware loop, error handling, sleep
timing.

### The Pattern is Pervasive

This is not specific to gas meters. Many IoT sensors share the same structure:

- **Probe fast, publish slow:** temperature sensors that sample at 1 Hz but only need
  updates every 5 min (HVAC, weather stations).
- **Probe fast, publish on change:** door contacts, motion detectors, impulse
  counters where events are sparse and irregular.
- **Probe fast, publish on significant change:** analog sensors where minor
  fluctuations should be suppressed (power meters, light sensors, air quality).

In all cases, the sensing loop runs at a higher frequency than the reporting frequency.
The existing `@app.telemetry` forces `interval` to serve double duty as both probe rate
and publish rate.

## Decision

Add composable **publish strategies** to `@app.telemetry` via an optional `publish=`
parameter. When omitted, behavior is unchanged — every probe is published (backward
compatible). The strategy protocol defines two methods: `should_publish(current,
previous) -> bool` and `on_published() -> None`.

### Strategy Protocol

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class PublishStrategy(Protocol):
    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool: ...

    def on_published(self) -> None: ...
```

The **first publish always goes through** regardless of strategy — this guarantees every
telemetry device publishes an initial state on startup, which is critical for MQTT
retained messages and Home Assistant discovery.

### Base Strategy Catalogue

#### `Every(seconds=N)` — Time-Based Throttle

Publishes when at least N seconds have elapsed since the last publish. Requires a
`ClockPort` dependency for testability (injected by the framework, not the user).

```python
@app.telemetry("temperature", interval=1.0, publish=Every(seconds=300))
async def temp() -> dict[str, object]:
    return {"celsius": read_temp()}
```

#### `Every(n=N)` — Count-Based Throttle

Publishes every N-th handler invocation.

```python
@app.telemetry("power", interval=0.1, publish=Every(n=10))
async def power() -> dict[str, object]:
    return {"watts": meter.read()}
```

`Every(seconds=N)` and `Every(n=N)` are mutually exclusive — specifying both raises
`ValueError`. Ambiguous semantics are resolved via explicit composition instead.

#### `OnChange()` — Publish on Value Change

Supports three modes through progressive disclosure:

- **Exact equality** (`OnChange()`): publishes when the returned dict differs from the
  last published dict. Use case: binary sensors (door open/closed).
- **Global numeric threshold** (`OnChange(threshold=0.5)`): numeric fields publish when
  `abs(current - previous) > threshold`; non-numeric fields use exact equality.
- **Per-field thresholds** (`OnChange(threshold={"celsius": 0.5, "humidity": 2.0})`):
  each field gets its own threshold; unlisted fields use exact equality.

Threshold comparison uses strict `>` (not `>=`) to avoid publishing on floating-point
noise that rounds to exactly the threshold. Structural changes (new or removed fields)
always trigger a publish.

### Composition via `|` and `&` Operators

Strategies compose using two operators:

| Operator | Semantics                              | Example                                          |
| -------- | -------------------------------------- | ------------------------------------------------ |
| `A \| B` | Publish if **either** A or B says yes  | `OnChange() \| Every(seconds=600)` — change + heartbeat |
| `A & B`  | Publish if **both** A and B say yes    | `OnChange() & Every(seconds=30)` — debounced change     |

When a composite publishes, **all** child strategies receive `on_published()` — even
the child that did not trigger the decision. This ensures all strategies reset their
internal state consistently.

### `None` Return as Complementary Escape Valve

Returning `None` from a telemetry handler suppresses publishing for that cycle.
This complements strategies for one-off suppression logic that does not warrant a
custom strategy object. The two mechanisms are orthogonal: `None` is checked first,
strategies are consulted only when the handler returns a dict.

### Separation of Concerns — Filtering vs. Publishing

**Filters (EWMA, calibration) are handler-level concerns. Strategies are
framework-level transmission policies.** These compose by layering, not coupling.

```
Handler (user code)         → probe → calibrate → filter → return dict
Strategy (framework)        → should_publish(current, previous) → bool
Framework loop              → ctx.publish_state(result) + error isolation
```

The handler runs on every probe cycle, feeding every sample to its filter. The strategy
only sees the handler's output dict and decides whether to transmit it. The framework
does not provide built-in filters — filter parameters (alpha, calibration coefficients,
field selection) are domain-specific and belong in user code.

### Example: gas2mqtt Temperature Under the New Design

```python
@app.telemetry("temperature", interval=1.0, publish=Every(seconds=300))
async def temperature() -> dict[str, object]:
    reading = magnetometer.read()
    filtered = ewma.update(reading.temperature_raw * scale + offset)
    return {"temperature": round(filtered, 1)}
```

The EWMA filter receives every 1 Hz sample (correct for convergence), but only the
300-second output is published (correct for MQTT bandwidth). The handler code is
identical to the existing gas2mqtt implementation — only the registration changes from
`@app.device` with a manual loop to `@app.telemetry` with a publish strategy.

## Decision Drivers

- 2 of 3 devices in the first framework consumer fell back to `@app.device`, defeating
  the "you don't write the loop" value proposition of `@app.telemetry`
- The probe-fast-report-slow pattern is pervasive across IoT sensor categories
- Backward compatibility is non-negotiable — existing `@app.telemetry` code must work
  without changes
- The framework should make the common cases declarative and testable without pushing
  timing logic into every handler
- Truly stateful devices (Schmitt trigger with counter side effects) correctly remain
  as `@app.device` — the archetype boundary from ADR-010 is preserved, not extended

## Considered Options

### Option 1: Publish Strategies via `publish=` Parameter (Chosen)

Extend `@app.telemetry` with an optional `publish=` parameter accepting composable
strategy objects.

- *Advantages:* Backward compatible. Declarative — publish policy visible in the
  decorator, not buried in loop logic. Composable via `|` and `&`. Testable — strategies
  are small objects with a single `should_publish()` method. Eliminates manual-loop
  boilerplate for the most common sensor types. Correct boundary with `@app.device`
  for truly stateful devices.
- *Disadvantages:* Adds API surface (`Every`, `OnChange`, `PublishStrategy` protocol).
  Strategies are stateful objects requiring clock injection. `interval` semantically
  shifts to "probe interval" (though observable behavior is unchanged for existing
  code).

### Option 2: Separate `probe_interval` and `publish_interval` Parameters

Add a `probe_interval` parameter; the existing `interval` becomes the publish interval.

- *Advantages:* Simple mental model — two intervals is easy to explain. No new classes
  or protocols. Backward compatible.
- *Disadvantages:* Cannot express delta thresholds, field-specific logic, or composed
  conditions. Flag proliferation — `publish_on_change`, `publish_on_delta`,
  `delta_threshold` grow the parameter list with each new strategy. Not composable.
  Naming confusion between probe and publish intervals.

### Option 3: Handler Returns `None` to Suppress Publishing

Keep `interval` as the probe interval, but returning `None` means "don't publish this
cycle." The handler owns the publish decision entirely.

- *Advantages:* Extremely simple — no new classes or parameters. Maximum flexibility.
  Easy to explain.
- *Disadvantages:* Pushes all timing/filtering logic into every handler — no reuse
  across devices. Handler must manage its own state (previous values, timers) via
  closures, approaching `@app.device` complexity. Publish policy is invisible from the
  decorator — requires reading the function body. No framework-level visibility for
  health reporting.

### Option 4: Do Nothing — `@app.device` is the Escape Hatch

Accept that `@app.telemetry` serves only the simple "poll-and-publish" case. Complex
publishing uses `@app.device`.

- *Advantages:* Zero framework changes. Clear separation between simple and complex.
- *Disadvantages:* gas2mqtt demonstrates that most devices are "complex" enough to need
  `@app.device`. The `@app.device` manual loop duplicates the exact boilerplate
  `@app.telemetry` was designed to eliminate. The framework's value proposition narrows
  to a small slice of real sensors.

## Decision Matrix

| Criterion              | Strategies (`publish=`) | Two Intervals | Return `None` | Do Nothing |
| ---------------------- | ----------------------- | ------------- | ------------- | ---------- |
| Common case simplicity | 5                       | 4             | 3             | 2          |
| Composability          | 5                       | 2             | 3             | 1          |
| Backward compatibility | 5                       | 4             | 4             | 5          |
| API clarity            | 4                       | 3             | 4             | 5          |
| Testability            | 5                       | 3             | 2             | 2          |
| Code elimination       | 5                       | 4             | 2             | 1          |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- **Backward compatible** — existing `@app.telemetry` code works without changes; the
  default behavior (no strategy) publishes every probe as before
- **Declarative** — publish policy is visible in the decorator, not hidden in loop logic
- **Composable** — `|` and `&` operators cover real-world combinations (change +
  heartbeat, debounced change) without bespoke framework parameters
- **Testable** — strategies are small, pure objects with a deterministic
  `should_publish()` contract; clock injection enables tests without asyncio or real time
- **Eliminates manual-loop boilerplate** for the majority of sensors — the gas2mqtt
  temperature device drops from ~20 lines of manual loop to a single decorated function
- **Correct archetype boundary** — truly stateful devices (gas counter with Schmitt
  trigger and side effects) remain as `@app.device` per ADR-010, validating rather than
  undermining the archetype design
- **First publish guarantee** ensures MQTT retained messages and Home Assistant discovery
  work correctly regardless of strategy

### Negative

- **New API surface** — `Every`, `OnChange`, and `PublishStrategy` are new public types
  that users must learn
- **Stateful strategy objects** — `Every(seconds=N)` requires clock injection, adding
  internal framework complexity for deterministic testing
- **Semantic shift of `interval`** — `interval` implicitly becomes "probe interval" when
  a strategy is present, though observable behavior is unchanged for existing code;
  documentation must be clear about this distinction
- **Does not cover all manual-loop cases** — devices with domain-level side effects
  (counter increments, consumption tracking) still require `@app.device`
