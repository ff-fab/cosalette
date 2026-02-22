# Telemetry Publish Strategies

## Problem Statement

The current `@app.telemetry` decorator equates **polling frequency** with **publishing
frequency**: every call to the handler results in an MQTT publish. This makes
`@app.telemetry` unsuitable for the large class of sensors that need **fast probing** but
**infrequent publishing**.

### Evidence from gas2mqtt (First Framework Consumer)

gas2mqtt has three devices, and **none** use `@app.telemetry`:

| Device          | Registered as      | Why not `@app.telemetry`?                                              |
| --------------- | ------------------- | ---------------------------------------------------------------------- |
| `gas_counter`   | `@app.device`       | Polls at 1 Hz, but publishes **only on Schmitt trigger edge events**   |
| `temperature`   | `@app.device`       | Polls at 1 Hz, applies EWMA filter, publishes every 300 s             |
| `magnetometer`  | `@app.device`       | Polls at 1 Hz, publishes at 1 Hz (could use `@app.telemetry`)         |

Both `gas_counter` and `temperature` had to fall back to `@app.device` with a manual
`while` loop, duplicating exactly the boilerplate that `@app.telemetry` was designed to
eliminate: shutdown-aware loop, error handling, sleep timing.

gas2mqtt's own [framework-opportunities.md](example-projects/gas2mqtt/docs/planning/framework-opportunities.md)
explicitly calls this out:

> *"If cosalette adds event-driven or threshold-based `@app.telemetry()` variants, the
> gas counter could become simpler — publishing only when the Schmitt trigger fires
> rather than polling on a fixed interval."*

### The Pattern is Pervasive

This isn't specific to gas meters. Many IoT sensors share the same structure:

- **Probe fast, publish slow:** temperature sensors that sample at 1 Hz but only need
  updates every 5 min (HVAC, weather stations).
- **Probe fast, publish on change:** motion detectors, door contacts, impulse counters
  where events are sparse and irregular.
- **Probe fast, publish on significant change:** analog sensors where minor fluctuations
  should be suppressed (power meters, light sensors, air quality).

In all cases, the sensing loop runs at a higher frequency than the reporting frequency.
The current `@app.telemetry` can't express this — it forces `interval` to serve double
duty as both probe rate and publish rate.

---

## Hypothesis

> If we reinterpret `@app.telemetry`'s `interval` as the **probing** frequency and add a
> declarative way to control **when** the returned value is actually published, we can
> cover the majority of the "manual loop" use cases while keeping the return-dict
> simplicity.

---

## Options

### Option A: Publish Strategies via `@app.telemetry` Parameters

Extend `@app.telemetry` with an optional `publish=` parameter that accepts a composable
**publish strategy** object. When omitted, behaviour is unchanged (publish every poll —
backward compatible).

```python
from cosalette import Every, OnChange

# Current behaviour (unchanged)
@app.telemetry("debug_mag", interval=1.0)
async def mag(ctx): ...

# Publish every N-th probe (fixed reporting interval, fast probing)
@app.telemetry("temperature", interval=1.0, publish=Every(seconds=300))
async def temp(ctx) -> dict[str, object]:
    # Called every 1s (probing), published every 300s
    return {"celsius": read_temp()}

# Publish only when any value in the dict changes (exact equality)
@app.telemetry("door", interval=0.5, publish=OnChange())
async def door(ctx) -> dict[str, object]:
    return {"open": gpio.read()}

# Publish when any numeric field changes by more than 0.5
@app.telemetry("temperature", interval=1.0, publish=OnChange(threshold=0.5))
async def temp(ctx) -> dict[str, object]:
    return {"celsius": read_temp()}

# Per-field thresholds for multi-signal payloads
@app.telemetry("weather", interval=1.0,
               publish=OnChange(threshold={"celsius": 0.5, "humidity": 2.0}))
async def weather(ctx) -> dict[str, object]:
    return {"celsius": read_temp(), "humidity": read_rh()}
```

#### Framework internals sketch

```python
# Updated _run_telemetry loop (simplified)
async def _run_telemetry(self, reg, ctx, error_pub, health):
    strategy = reg.publish_strategy   # PublishStrategy instance or None
    last_published = None
    while not ctx.shutdown_requested:
        try:
            result = await reg.func(**kwargs)
            if strategy is None or strategy.should_publish(result, last_published):
                await ctx.publish_state(result)
                last_published = result
                strategy.on_published() if strategy else None
        except ...:
            ...
        await ctx.sleep(reg.interval)
```

#### Strategy protocol

```python
from typing import Protocol

class PublishStrategy(Protocol):
    def should_publish(
        self, current: dict[str, object], previous: dict[str, object] | None
    ) -> bool: ...

    def on_published(self) -> None: ...
```

**Advantages:**

- Backward compatible — no publish strategy = current behaviour
- Declarative — the *what* (probe temp at 1 Hz, report every 300 s) is visible in the
  decorator, not buried in loop logic
- Composable — strategies can be combined (e.g., `OnChange(threshold=0.5) | Every(seconds=600)`
  for "publish on significant change, but at least every 10 min")
- Testable — strategies are pure, stateless-ish objects with a simple
  `should_publish()` contract
- Eliminates the manual-loop boilerplate for the most common cases
- The handler remains a simple return-dict function — no `ctx.publish_state()`, no
  `while` loop

**Disadvantages:**

- Adds API surface: new classes (`Every`, `OnChange`) and the strategy
  protocol
- Does not cover *all* manual-loop cases — truly stateful devices (like gas_counter's
  Schmitt trigger with counter increment side effects) still need `@app.device`
- `interval` now means "probe interval," which is a semantic shift — though backward
  compatible because the default strategy preserves the old meaning
- Composition operators (`|`, `&`) add complexity and need clear documentation

---

### Option B: Separate `probe_interval` and `publish_interval` Parameters

Add an explicit `probe_interval` parameter to `@app.telemetry`. The existing `interval`
becomes the publish interval. When `probe_interval` is set, the framework probes at that
rate but only publishes at `interval`.

```python
@app.telemetry("temperature", interval=300, probe_interval=1.0)
async def temp(ctx) -> dict[str, object]:
    return {"celsius": read_temp()}
```

For publish-on-change, add a boolean flag:

```python
@app.telemetry("door", interval=0.5, publish_on_change=True)
async def door(ctx) -> dict[str, object]:
    return {"open": gpio.read()}
```

**Advantages:**

- Very simple mental model — two intervals is easy to explain
- No new classes or protocols
- Backward compatible — when `probe_interval` is not set, `interval` works as before

**Disadvantages:**

- Limited expressiveness — cannot express delta thresholds, field-specific logic, or
  composed conditions without adding more flags
- Flag proliferation — `publish_on_change`, `publish_on_delta`, `delta_threshold`,
  `delta_field`… the parameter list grows with each new strategy
- Not composable — "publish on change OR every 10 min" requires bespoke parameter
  combinations
- Naming confusion — is `interval` the probe or the publish interval? Renaming it is
  a breaking change

---

### Option C: Handler Returns `None` to Suppress Publishing

Keep `interval` as the **probe interval**, but change the contract so that returning
`None` from a telemetry handler means "don't publish this cycle." The handler owns the
publish decision.

```python
@app.telemetry("temperature", interval=1.0)
async def temp(ctx) -> dict[str, object] | None:
    reading = read_temp()
    if not significant_change(reading):
        return None  # Suppress this cycle
    return {"celsius": reading}
```

**Advantages:**

- Extremely simple API change — the `None` return is intuitive
- Maximum flexibility — the handler can implement any publish logic
- No new classes, protocols, or parameters
- Easy to explain: "return data to publish, return None to skip"

**Disadvantages:**

- Pushes the publish logic into *every handler* — no reuse across devices
- Requires the handler to manage its own state (previous values, timers, etc.) via
  closures or injected state — approaches the complexity of `@app.device` anyway
- The return-dict contract becomes "return dict | None", which is a contract change
  (though additive)
- Harder to see "when does this device publish?" from the decorator alone — the answer
  is "read the function body"
- No framework-level visibility into publish strategy for health reporting / debugging

---

### Option D: Do Nothing — `@app.device` is the Escape Hatch

Accept that `@app.telemetry` serves the simple "poll-and-publish" use case, and devices
with more complex publishing logic should use `@app.device` with a manual loop — exactly
as the current design intends.

**Advantages:**

- Zero framework changes
- Clear separation: simple → `@app.telemetry`, complex → `@app.device`
- No risk of over-engineering

**Disadvantages:**

- gas2mqtt shows that in practice, *most* devices are "complex" enough to need
  `@app.device` — the telemetry decorator covers fewer real use cases than expected
- The `@app.device` manual loop duplicates shutdown-aware loop boilerplate, error
  isolation, and health status management — exactly what `@app.telemetry` was designed
  to eliminate
- The framework's "you don't write the loop" value proposition applies to a narrow
  slice of real sensors

---

## Analysis & Recommendation

### How gas2mqtt devices would look under each option

| Device          | Option A (Strategies)                              | Option B (Two Intervals) | Option C (Return None) | Option D (Status Quo) |
| --------------- | -------------------------------------------------- | ------------------------ | ---------------------- | --------------------- |
| `gas_counter`   | Still `@app.device` — has side effects (counter++) | Still `@app.device`      | Still `@app.device`    | `@app.device`         |
| `temperature`   | `@app.telemetry(interval=1, publish=Every(300))`   | `@app.telemetry(interval=300, probe_interval=1)` | Handler with timer logic | `@app.device`         |
| `magnetometer`  | `@app.telemetry(interval=1)` (unchanged)           | `@app.telemetry(interval=1)` | Unchanged              | `@app.device`         |

`gas_counter` stays as `@app.device` in **all** options — it has domain-level side
effects (counter increment, consumption tracking) that go beyond read-and-report. This
is the legitimate use case for `@app.device` and validates its existence.

`temperature` is the **smoking gun**: it's a pure read-filter-report device that should
be expressible as `@app.telemetry` but can't be today because the probing rate (1 Hz for
EWMA convergence) differs from the reporting rate (300 s). Only Options A and B
rescue it.

### Challenging the hypothesis

1. **Is the complexity worth it?** gas2mqtt is one project with one affected device.
   However, every other bridge project in the ADR-010 survey (airthings2mqtt,
   smartmeter2mqtt) has sensors with the same probe-fast-report-slow pattern.
   Temperature/humidity alone — present in nearly every IoT project — fits this model.

2. **Does Option C solve it without framework complexity?** Technically yes — but at the
   cost of pushing *all* timing/filtering logic into every handler, which defeats the
   "framework handles the loop" value proposition. The handler in Option C starts looking
   like a mini `@app.device` without the explicit loop.

3. **Will strategies be composable enough?** Real-world needs:
   - "Publish on change OR every 10 min" → `OnChange() | Every(seconds=600)` ✓
   - "Publish if delta > 0.5 AND at least 30 s since last" → `OnChange(threshold=0.5) & Every(seconds=30)` ✓
   - "Publish on Schmitt trigger edge" → Too stateful for a strategy, needs
     `@app.device` ✓ (correct boundary)

4. **Does reinterpreting `interval` cause confusion?** Only if the default behavior
   changes. With Option A, `interval` explicitly becomes "probe interval," but since the
   default strategy is "publish every probe," the observable behavior is identical for
   existing code. Documentation should be clear about the semantic shift.

### Recommendation: Option A (Publish Strategies)

Option A provides the best balance of expressiveness, composability, and simplicity:

- It covers the high-value middle ground between "publish every poll" and "full manual
  loop" without sacrificing the return-dict contract.
- It's **backward compatible** — existing `@app.telemetry` code is unaffected.
- It's **declarative** — the publish policy is visible in the decorator, not hidden in
  function logic.
- It's **testable** — strategies are small, pure objects with a single method.
- The gas counter's Schmitt trigger with side effects is correctly *excluded* — it
  remains an `@app.device`, validating the archetype boundary.

**Option C (return None)** is worth considering as a **complementary** addition: even
with strategies, allowing `None` returns gives handlers an escape valve for
one-off suppression logic that doesn't warrant a custom strategy. The two approaches
are not mutually exclusive.

---

## Detailed Design: Publish Strategies

### Separation of Concerns — Filtering vs. Publishing

A critical design question is how data **filtering** (e.g., EWMA smoothing) relates to
publish **strategies**. The answer: they are orthogonal concerns operating at different
layers.

```
┌──────────────────────────────────────────────────────────┐
│  Handler (user code)                                     │
│  probe → calibrate → filter → return dict                │
│  Runs on EVERY probe cycle (interval)                    │
│  Owns: domain logic, filtering, data transformation     │
└────────────────────────┬─────────────────────────────────┘
                         │ dict (or None)
┌────────────────────────▼─────────────────────────────────┐
│  Strategy (framework)                                    │
│  should_publish(current, previous) → bool                │
│  Owns: transmission policy (when to send to MQTT)        │
└────────────────────────┬─────────────────────────────────┘
                         │ publish / suppress
┌────────────────────────▼─────────────────────────────────┐
│  Framework loop                                          │
│  ctx.publish_state(result) + error isolation             │
└──────────────────────────────────────────────────────────┘
```

**Filters are handler-level concerns, not strategy-level concerns.** The strategy only
sees the handler's output dict and decides whether to transmit it. The filter runs
inside the handler on every probe, which is exactly what filters need — every sample
feeds the filter, but only some outputs are published.

#### gas2mqtt temperature under the new design

```python
def make_temperature_handler(
    magnetometer: MagnetometerPort,
    settings: Gas2MqttSettings,
) -> Callable[[], Awaitable[dict[str, object]]]:
    ewma = EwmaFilter(settings.ewma_alpha)  # State persists across calls

    async def handler() -> dict[str, object]:
        reading = magnetometer.read()
        raw_celsius = settings.temp_scale * reading.temperature_raw + settings.temp_offset
        filtered = ewma.update(raw_celsius)  # Fed on EVERY probe (1 Hz)
        return {"temperature": round(filtered, 1)}

    return handler

# Registration — handler is called at 1 Hz, published every 300s:
handler = make_temperature_handler(magnetometer, settings)

@app.telemetry("temperature", interval=1.0, publish=Every(seconds=300))
async def temperature() -> dict[str, object]:
    return await handler()
```

The EWMA filter receives every 1 Hz sample (correct for convergence), but only the
300-second output is published (correct for MQTT bandwidth). The handler code is
**identical** to what gas2mqtt already has — only the registration changes from
`@app.device` with a manual loop to `@app.telemetry` with a publish strategy.

**Key insight:** The framework does NOT need to provide built-in filters. Filters are
domain-specific (which field to smooth, what alpha, what calibration). Strategies are
about *when to transmit*, not *what to transmit*. These concerns compose by layering,
not by coupling.

#### When the framework should NOT provide filters

- EWMA alpha depends on sensor noise characteristics — domain knowledge
- Calibration coefficients are sensor-specific — `temp_scale * raw + temp_offset`
- Field selection (which readings to smooth) is application-specific

All of this belongs in the handler. The strategy layer stays thin and generic.

### Strategy Protocol

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class PublishStrategy(Protocol):
    """Decides whether a telemetry reading should be published.

    Strategies are stateful objects — they track elapsed time, sample
    counts, or previous values to make publish/suppress decisions.
    The framework calls ``should_publish()`` after every handler
    invocation and ``on_published()`` after each successful publish.
    """

    def should_publish(
        self,
        current: dict[str, object],
        previous: dict[str, object] | None,
    ) -> bool:
        """Return True if *current* should be published.

        Args:
            current: The dict returned by the telemetry handler.
            previous: The last dict that was actually published,
                or None if nothing has been published yet.
        """
        ...

    def on_published(self) -> None:
        """Called after a successful publish — reset internal counters."""
        ...
```

The first invocation (when `previous is None`) **always publishes** regardless of the
strategy. This guarantees that every telemetry device publishes an initial state on
startup, which is critical for MQTT retained messages and Home Assistant discovery.

### Base Strategy Catalogue

#### `Every(seconds=N)` — Time-Based Throttle

Publishes when at least `seconds` seconds have elapsed since the last publish.

```python
@app.telemetry("temperature", interval=1.0, publish=Every(seconds=300))
async def temp() -> dict[str, object]:
    return {"celsius": read_temp()}
```

- **Probe rate:** 1 Hz (handler called every second)
- **Publish rate:** every 300 seconds
- **Use case:** slow-changing physical quantities (temperature, humidity, battery)

Implementation needs a `ClockPort` dependency for testability (fake clock in tests).

#### `Every(n=N)` — Count-Based Throttle

Publishes every N-th handler invocation.

```python
@app.telemetry("power", interval=0.1, publish=Every(n=10))
async def power() -> dict[str, object]:
    return {"watts": meter.read()}
```

- **Probe rate:** 10 Hz
- **Publish rate:** every 10th sample (effectively 1 Hz)
- **Use case:** high-frequency sensors where you want fixed downsampling

Implementation tracks an internal counter, resets on `on_published()`.

#### `OnChange()` — Publish on Value Change

Publishes only when the returned dict differs from the last published dict.
Supports three modes through progressive disclosure:

**Mode 1 — Exact equality (no threshold):**

```python
@app.telemetry("door", interval=0.5, publish=OnChange())
async def door() -> dict[str, object]:
    return {"open": gpio.read()}
```

- Comparison uses `==` on the full dict.
- **Use case:** binary sensors, discrete state (door open/closed, motion detected).

**Mode 2 — Global numeric threshold:**

```python
@app.telemetry("temperature", interval=1.0, publish=OnChange(threshold=0.5))
async def temp() -> dict[str, object]:
    return {"celsius": read_temp()}
```

- Numeric fields (`int`, `float`): publish if `abs(current - previous) > threshold`.
- Non-numeric fields (`str`, `bool`, etc.): exact equality — any change triggers.
- Publish if **any** field exceeds its threshold.
- **Use case:** single-signal sensors, temperature, power meters.

**Mode 3 — Per-field thresholds:**

```python
@app.telemetry("weather", interval=1.0,
               publish=OnChange(threshold={"celsius": 0.5, "humidity": 2.0}))
async def weather() -> dict[str, object]:
    return {"celsius": read_temp(), "humidity": read_rh(), "status": "ok"}
```

- Fields **with** a threshold entry: numeric delta comparison against that threshold.
- Fields **without** a threshold entry: exact equality (any change triggers).
- Non-numeric fields always use exact equality regardless of threshold specification.
- Publish if **any** field triggers.
- **Use case:** multi-signal payloads where different signals have different noise
  characteristics (temperature ±0.5°C, humidity ±2%).

#### `OnChange` threshold type

```python
threshold: float | dict[str, float] | None = None
```

- `None` (default) → Mode 1: exact equality on all fields.
- `float` → Mode 2: global numeric threshold for all numeric fields.
- `dict[str, float]` → Mode 3: per-field thresholds; unlisted fields use exact equality.

#### `OnChange` comparison semantics

| Field type | No threshold    | Global threshold `T` | Per-field threshold `{field: T}` |
| ---------- | --------------- | -------------------- | -------------------------------- |
| `int`      | `!=`            | `abs(Δ) > T`         | `abs(Δ) > T` if listed, else `!=` |
| `float`    | `!=`            | `abs(Δ) > T`         | `abs(Δ) > T` if listed, else `!=` |
| `str`      | `!=`            | `!=`                 | `!=`                             |
| `bool`     | `!=`            | `!=`                 | `!=`                             |
| Other      | `!=`            | `!=`                 | `!=`                             |

**Strict `>`, not `>=`:** A change of exactly `0.5` with `threshold=0.5` does **not**
trigger a publish. This is the intuitive reading of "changes by more than 0.5" and
avoids publishing on floating-point noise that rounds to exactly the threshold.

**New fields:** If `current` contains a key not present in `previous` (or vice versa),
that field always triggers a publish — structural changes are always significant.

#### `OnChange` with EWMA-filtered temperature — the gas2mqtt sweet spot

The threshold variant is particularly powerful when combined with EWMA filtering.
The filter smooths high-frequency noise, and the threshold suppresses insignificant
drift. Together, they eliminate jitter publishing without a fixed time interval:

```python
# Probes at 1 Hz, EWMA smooths, publishes only when temperature moves > 0.5°C
@app.telemetry("temperature", interval=1.0, publish=OnChange(threshold=0.5))
async def temp() -> dict[str, object]:
    reading = magnetometer.read()
    filtered = ewma.update(reading.temperature_raw * scale + offset)
    return {"temperature": round(filtered, 1)}
```

This is arguably an even better fit for gas2mqtt's temperature device than
`Every(seconds=300)` — it publishes *when the temperature actually changes* rather
than on a fixed schedule, which is more useful for downstream consumers like Home
Assistant automation triggers.

### `Every` Design Decision — One Class or Two?

`Every(seconds=N)` and `Every(n=N)` are both throttles, just with different clocks
(wall time vs. sample count). Keeping them as one class with mutually exclusive
parameters is the cleanest API:

```python
# Time-based
Every(seconds=300)

# Count-based
Every(n=10)

# Both specified — ERROR (ambiguous, use composition instead)
Every(seconds=300, n=10)  # raises ValueError
```

**Why not allow both?** Because the semantics are ambiguous — does it mean "publish when
EITHER condition is met" or "publish when BOTH conditions are met"? Rather than pick one
and surprise users, we force explicit composition:

```python
# "Publish every 300s OR every 10th sample" (whichever comes first)
Every(seconds=300) | Every(n=10)

# "Publish every 300s AND only if 10+ samples collected" (both required)
Every(seconds=300) & Every(n=10)
```

### Composability

Strategies support two composition operators:

| Operator | Semantics | Mnemonic |
| -------- | --------- | -------- |
| `A \| B` | Publish if **either** A or B says yes | "or" / "any" |
| `A & B`  | Publish if **both** A and B say yes   | "and" / "all" |

#### Implementation: `AnyStrategy` and `AllStrategy`

```python
class AnyStrategy:
    """Composite: publish if ANY child strategy says yes."""

    def __init__(self, *children: PublishStrategy) -> None:
        self._children = children

    def should_publish(self, current, previous) -> bool:
        return any(c.should_publish(current, previous) for c in self._children)

    def on_published(self) -> None:
        for c in self._children:
            c.on_published()


class AllStrategy:
    """Composite: publish only if ALL child strategies say yes."""

    def __init__(self, *children: PublishStrategy) -> None:
        self._children = children

    def should_publish(self, current, previous) -> bool:
        return all(c.should_publish(current, previous) for c in self._children)

    def on_published(self) -> None:
        for c in self._children:
            c.on_published()
```

Base strategies implement `__or__` and `__and__` to return these composites.
Composites themselves support further composition (flattened — no deep nesting).

#### Composition behaviour matrix

| Composition                                    | Meaning                                                      | Example Use Case                          |
| ---------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------ |
| `OnChange() \| Every(seconds=600)`              | Publish on change, but heartbeat at least every 10 min       | Door sensor with liveness guarantee        |
| `OnChange(threshold=0.5) \| Every(seconds=600)` | Publish on significant change OR every 10 min (liveness)     | Temperature with heartbeat guarantee       |
| `OnChange() & Every(seconds=30)`                | Publish changes, but at most once per 30 s (debounce)        | Noisy analog sensor                        |
| `OnChange(threshold=0.5) & Every(seconds=30)`   | Publish significant changes, debounced to 30 s minimum      | High-frequency sensor with threshold + rate limit |
| `Every(seconds=300) \| Every(n=10)`             | Publish every 300 s or every 10 samples, whichever first     | Adaptive rate with both guarantees         |
| `Every(seconds=300) & Every(n=10)`              | Publish only when 300 s elapsed AND 10+ samples collected    | Ensuring minimum sample count before report|

#### `on_published()` semantics in composites

When the composite decides to publish, **all** child strategies get their
`on_published()` called — even the child that didn't trigger the publish. This is
correct because all strategies should reset their "since last publish" state. Otherwise,
a time-based strategy that didn't trigger would carry stale elapsed time into the next
cycle.

### Updated Framework Loop

```python
async def _run_telemetry(self, reg, ctx, error_publisher, health_reporter):
    """Run a telemetry polling loop with optional publish strategy."""
    providers = build_providers(ctx, reg.name)
    kwargs = resolve_kwargs(reg.injection_plan, providers)
    strategy = reg.publish_strategy  # PublishStrategy | None
    last_published: dict[str, object] | None = None
    last_error_type: type[Exception] | None = None

    while not ctx.shutdown_requested:
        try:
            result = await reg.func(**kwargs)

            # None return = suppress this cycle (complementary to strategies)
            if result is None:
                await ctx.sleep(reg.interval)
                continue

            # Publish decision: first publish always goes through
            should_publish = (
                last_published is None          # First publish — always
                or strategy is None             # No strategy — every probe
                or strategy.should_publish(result, last_published)
            )

            if should_publish:
                await ctx.publish_state(result)
                last_published = result
                if strategy is not None:
                    strategy.on_published()

            if last_error_type is not None:
                logger.info("Telemetry '%s' recovered", reg.name)
                last_error_type = None
                health_reporter.set_device_status(reg.name, "ok")

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if type(exc) is not last_error_type:
                logger.error("Telemetry '%s' error: %s", reg.name, exc)
                await error_publisher.publish(
                    exc, device=reg.name, is_root=reg.is_root
                )
            last_error_type = type(exc)
            health_reporter.set_device_status(reg.name, "error")

        await ctx.sleep(reg.interval)
```

Notable changes from the current implementation:

1. `result is None` check — enables Option C as a complementary escape valve
2. `last_published is None` guard — first publish always goes through
3. `strategy.should_publish()` / `strategy.on_published()` calls

### Future Strategies (Deferred)

Strategies that may be added in future phases based on real-world demand:

- **Strategy introspection** — exposing publish strategy metadata for health
  reporting and debug logging (e.g., "last publish was 47 s ago, threshold: 0.5")
- **`OnChange` with nested dict support** — threshold-based comparison for nested
  payload structures (`{"sensor": {"temp": 21.5}}`)

### Clock Dependency for `Every(seconds=N)`

`Every(seconds=N)` needs a monotonic clock to track elapsed time. In production this
is `time.monotonic()`; in tests, a `FakeClock` for deterministic assertions.

The strategy receives the framework's `ClockPort` at construction time (injected by
the telemetry registration machinery), keeping the strategy itself testable without
asyncio.

```python
class EverySeconds:
    def __init__(self, seconds: float, clock: ClockPort) -> None:
        self._seconds = seconds
        self._clock = clock
        self._last_publish_time: float | None = None

    def should_publish(self, current, previous) -> bool:
        if previous is None:
            return True
        now = self._clock.now()
        return (now - (self._last_publish_time or 0)) >= self._seconds

    def on_published(self) -> None:
        self._last_publish_time = self._clock.now()
```

**User-facing API does NOT require passing the clock** — `Every(seconds=300)` stores the
seconds value, and the framework injects the clock when wiring the telemetry loop. This
keeps the user API clean while maintaining testability. The `Every` constructor is a
lightweight factory; the framework calls an internal `_bind(clock)` method before the
loop starts.

---

## Scope & Phasing

### Phase 1: Core Strategy Infrastructure (MVP)

1. `PublishStrategy` protocol in `cosalette/_strategies.py`
2. `Every(seconds=N)` strategy with `ClockPort` injection
3. `Every(n=N)` strategy (count-based)
4. `OnChange()` strategy (exact equality, no threshold)
5. `AnyStrategy` / `AllStrategy` composites with `|` and `&` operators
6. `None` return support in telemetry handlers (complementary)
7. Updated `_run_telemetry` loop in `_app.py`
8. Updated `@app.telemetry` decorator to accept `publish=` parameter
9. Public API exports in `__init__.py`
10. Comprehensive tests
11. ADR-013 documenting the decision
12. Updated documentation (device-archetypes concept, telemetry guide)

### Phase 2: Threshold-Based Change Detection

1. `OnChange(threshold=T)` — global numeric threshold mode
2. `OnChange(threshold={field: T})` — per-field threshold mode
3. Comparison semantics for `int`, `float`, and non-numeric types
4. Structural change detection (new/removed fields)
5. Tests for threshold edge cases (exactly-at-threshold, mixed types, NaN)
6. Updated documentation with threshold examples

### Phase 3: Advanced Features (Follow-up)

1. Strategy introspection for health reporting / debug logging
2. Nested dict support for `OnChange`

---

## Next Steps

1. **Review this detailed design** — confirm the approach & scope.
2. Approve, then create ADR-013 recording the decision.
3. Create beads epic with phased tasks.
4. Implement Phase 1.
5. Update gas2mqtt example project to demonstrate the new feature.
