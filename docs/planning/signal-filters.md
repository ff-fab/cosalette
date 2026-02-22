# Signal Filters in cosalette

## Context

gas2mqtt — the first cosalette-based project — uses an EWMA filter to smooth
noisy temperature readings and a Schmitt trigger for hysteresis-based edge
detection. Both live in the app's `domain/` layer as pure Python classes with an
`update(raw) → filtered` interface:

```python
# gas2mqtt/domain/ewma.py
class EwmaFilter:
    def __init__(self, alpha: float) -> None: ...
    def update(self, raw: float) -> float: ...
    def reset(self) -> None: ...
```

The filter is instantiated in a closure factory (`make_temperature_handler`) and
called inside the `@app.telemetry` handler. The pattern works but requires each
app to implement its own filter classes — and signal smoothing is a universal IoT
concern.

**Question:** Should cosalette provide filter primitives, and if so, at what
integration depth?

### Current Telemetry Pipeline

```
probe (interval=N)  →  handler returns dict  →  publish strategy  →  MQTT
                        ↑                        ↑
                    app-owned logic           framework-owned gate
```

Filters currently operate inside the handler (app-owned). Publishing strategies
operate after the handler returns (framework-owned). Both are stateful.

---

## Options

### Option A: Filter Utility Library (`cosalette.filters`)

Provide filter classes as importable utilities. No framework integration — apps
use them in handlers exactly as gas2mqtt does today.

```python
from cosalette.filters import EwmaFilter

ewma = EwmaFilter(alpha=0.2)

@app.telemetry("temperature", interval=10, publish=OnChange(threshold=0.5))
async def temperature() -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(ewma.update(raw), 1)}
```

**What it does:** Ships a `cosalette.filters` module with tested, documented
filter classes: `EwmaFilter`, `MedianFilter`, `SlidingMeanFilter`, maybe an
`OutlierGuard`. Each follows a simple `update(raw) → filtered` protocol. Apps
import and wire them manually.

**Why this approach:**

- **Hexagonal purity** — filters remain domain logic, used inside the handler.
  The framework provides tools, not magic. Consistent with ADR-006 ("domain
  layer has zero framework dependencies — pure Python, fully testable").
- **Explicit data flow** — the handler author sees the filter call, knows the
  raw-to-filtered transform, and controls rounding/field names. "Explicit is
  better than implicit" (PEP 20).
- **Universally useful** — works in `@app.telemetry`, `@app.device`, scripts,
  notebooks, tests. No coupling to the telemetry loop.
- **Low risk** — no framework API changes. No new parameters, no new lifecycle
  hooks. Just a library module.
- **IoT precedent** — many frameworks ship filter utilities alongside their
  core (ESPHome ships filters as YAML-declarable but they're just classes
  underneath; Home Assistant has `homeassistant.util.dt` and similar utils).

**Trade-offs:**

- **No boilerplate reduction** — each handler still needs `ewma = ...` setup
  and an `ewma.update()` call. The closure factory pattern (as in gas2mqtt's
  `make_temperature_handler`) remains the app's responsibility.
- **State management is the app's problem** — the app must figure out where to
  hold the filter instance (closure, shared state, class). The framework can't
  help with lifecycle.
- **Discoverability** — new users may not know the filters module exists unless
  docs prominently feature it.

**Filters provided:**

| Filter            | Algorithm                                     | Use case                          |
| ----------------- | --------------------------------------------- | --------------------------------- |
| `EwmaFilter`      | $y_n = \alpha x_n + (1-\alpha) y_{n-1}$       | Noise smoothing, fast response    |
| `MedianFilter`    | Median of last *k* samples                    | Spike/outlier rejection           |
| `SlidingMeanFilter` | Mean of last *k* samples                    | General smoothing                 |
| `OutlierGuard`    | Reject values outside $\mu \pm k\sigma$ range | Sensor glitch protection          |

---

### Option B: Declarative `filter=` Parameter on `@app.telemetry`

Extend the telemetry decorator with a `filter=` parameter. The framework applies
filters to the returned dict in the loop, before the publish strategy runs.

```python
from cosalette import Every, OnChange
from cosalette.filters import Ewma

@app.telemetry(
    "temperature",
    interval=1,
    filter=Ewma(alpha=0.2, fields=["celsius"]),
    publish=OnChange(threshold=0.5) | Every(seconds=300),
)
async def temperature() -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": raw}  # framework smooths this
```

**Pipeline becomes:**

```
probe → handler returns dict → filter(dict) → strategy(filtered) → MQTT
```

**What it does:** Adds a `filter=` parameter to `@app.telemetry` that accepts
a `TelemetryFilter` protocol object (or chain via `>>` composition). The
framework calls `filter.apply(result)` between the handler return and the
publish strategy check. Filters are stateful and bound to the loop lifecycle.

**Why this approach:**

- **Maximum boilerplate reduction** — the handler returns raw data, the
  framework handles smoothing. Mirrors the philosophy of `publish=`: declarative
  configuration visible in the decorator.
- **Pipeline composability** — filters compose with strategies naturally. You
  can probe at 10 Hz, EWMA-smooth, then only publish on significant change.
  The framework owns the full data pipeline.
- **Consistent lifecycle** — filter state is managed by the framework. No
  closure factories, no manual state wiring, no "where do I put the filter
  instance?" question.
- **IoT framework precedent** — ESPHome and Home Assistant both provide
  declarative filter configuration with framework-managed lifecycle.

**Trade-offs:**

- **Implicit transformation** — the handler returns `{"celsius": 25.3}` but
  MQTT receives `{"celsius": 24.8}`. This violates "explicit is better than
  implicit" and can confuse debugging. The returned dict is no longer what gets
  published.
- **Field-level complexity** — filters must know which fields to apply to.
  What about nested dicts? What about non-numeric fields? The `fields=`
  parameter adds a mini-DSL (dot-notation, wildcards?).
- **Scope creep** — the `@app.telemetry` decorator already has `name`,
  `interval`, `publish`. Adding `filter` starts turning it into a
  configuration object. What about calibration next? Rounding?
- **Only works for `@app.telemetry`** — the `@app.device` archetype (which
  gas2mqtt's gas counter uses) doesn't benefit. The Schmitt trigger can't
  be expressed as a `filter=` parameter.
- **Semantic ambiguity** — should the strategy see pre-filter or post-filter
  data? (Post-filter is the obvious answer, but it's a question that
  shouldn't need asking.)
- **Testing complexity** — testing a handler's output now requires accounting
  for the filter. Unit-testing the handler in isolation gives you raw data;
  integration-testing via the framework gives you filtered data.

---

### Option C: Both — Library + Optional Decorator Integration

Provide the utility library (Option A) as the primary interface, and optionally
add a thin `filter=` parameter (Option B) as syntactic sugar.

```python
# Explicit — use the filter directly (always works, all archetypes)
from cosalette.filters import EwmaFilter
ewma = EwmaFilter(alpha=0.2)

@app.telemetry("temperature", interval=10)
async def temperature() -> dict[str, object]:
    return {"celsius": round(ewma.update(await read_sensor()), 1)}

# Declarative — let the framework apply it (convenience, telemetry only)
from cosalette.filters import Ewma

@app.telemetry("temperature", interval=1, filter=Ewma(0.2, fields=["celsius"]))
async def temperature() -> dict[str, object]:
    return {"celsius": await read_sensor()}
```

**Why this approach:**

- Best of both worlds — library for power users and `@app.device`, decorator
  for simple `@app.telemetry` cases.

**Trade-offs:**

- **Two ways to do it** — violates "There should be one — and preferably only
  one — obvious way to do it" (PEP 20). Users must choose, docs must explain
  both, examples will diverge.
- **Double the API surface** — `EwmaFilter` (explicit class) vs `Ewma`
  (declarative filter) creates naming confusion.
- **Maintenance burden** — two integration points to test, document, and evolve.

---

### Option D: Filter Protocol Only — No Concrete Implementations

Define a `TelemetryFilter` protocol and the `filter=` parameter, but ship *no*
concrete filter classes. Apps define their own filters (as gas2mqtt already does)
and plug them in.

```python
from typing import Protocol

class TelemetryFilter(Protocol):
    def apply(self, data: dict[str, object]) -> dict[str, object]: ...
```

**Why this approach:**

- Avoids the "which filters should we ship?" problem.
- Establishes the extension point for future concrete implementations.

**Trade-offs:**

- **Zero immediate value** — apps still write their own filters. The protocol
  adds API surface without reducing boilerplate.
- **Premature abstraction** — we don't yet know what the right protocol shape
  is. Locking it in now may be wrong.

---

## Analysis

### Where Filters Sit in the Hexagonal Model

```
┌─────────────────────────────────────────────┐
│ Framework                                    │
│  MQTT • Logging • Lifecycle • Strategies     │
│  ┌─────────────────────────────────────────┐ │
│  │ Devices (app.telemetry / app.device)    │ │
│  │  ┌───────────────────────────────────┐  │ │
│  │  │ Domain Logic                      │  │ │
│  │  │  ← Filters live HERE (domain)     │  │ │
│  │  │  ← Calibration lives here too     │  │ │
│  │  └───────────────────────────────────┘  │ │
│  └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

Filters are **data transformations** — they take a numeric input and produce a
numeric output. In the hexagonal model, that's domain logic. Publish strategies
are **infrastructure decisions** — they control when the framework's MQTT
machinery fires. This is a meaningful distinction:

- **Strategy:** "Should the framework publish?" → infrastructure concern →
  natural framework feature.
- **Filter:** "What value should be published?" → domain concern → natural
  domain-layer responsibility.

gas2mqtt's architecture confirms this: `EwmaFilter` lives in `domain/ewma.py`,
not in an adapter or framework hook.

### IoT Framework Survey

| Framework       | Filters | How                                        |
| --------------- | ------- | ------------------------------------------ |
| **Home Assistant** | Yes  | YAML config: `filter: lowpass`, `filter: outlier` — framework applies them |
| **ESPHome**     | Yes     | YAML config: `filters: - exponential_moving_average` — framework applies |
| **Node-RED**    | Yes     | Function nodes — user-written JS filters   |
| **Tasmota**     | Some    | Built-in: sensor averaging, median          |
| **cosalette**   | No      | App-level (gas2mqtt's `EwmaFilter`)         |

Home Assistant and ESPHome both use **declarative, framework-managed filters**
because their users are primarily YAML-configuring, not coding. cosalette's
users are Python developers writing handler functions — a fundamentally
different audience.

### The gas2mqtt EWMA as a Test Case

How would migration look under each option?

**Current gas2mqtt (13 lines of temperature handler):**

```python
ewma = EwmaFilter(settings.ewma_alpha)

async def handler() -> dict[str, object]:
    reading = magnetometer.read()
    raw_celsius = settings.temp_scale * reading.temperature_raw + settings.temp_offset
    filtered = ewma.update(raw_celsius)
    return {"temperature": round(filtered, 1)}
```

**Option A — same structure, import from framework:**

```python
from cosalette.filters import EwmaFilter
ewma = EwmaFilter(settings.ewma_alpha)

async def handler() -> dict[str, object]:
    reading = magnetometer.read()
    raw_celsius = settings.temp_scale * reading.temperature_raw + settings.temp_offset
    return {"temperature": round(ewma.update(raw_celsius), 1)}
```

Δ: 1 changed import. Identical structure. The app deletes `domain/ewma.py`.

**Option B — declarative:**

```python
@app.telemetry(
    "temperature",
    interval=1,
    filter=Ewma(alpha=0.2, fields=["temperature"]),
    publish=OnChange(threshold=0.5),
)
async def temperature() -> dict[str, object]:
    reading = magnetometer.read()
    raw_celsius = settings.temp_scale * reading.temperature_raw + settings.temp_offset
    return {"temperature": round(raw_celsius, 1)}
```

Problem: the calibration (`temp_scale * raw + temp_offset`) is still in the
handler. The filter applies *after* calibration. This is correct, but the
handler now returns a "calibrated but unfiltered" value — a conceptual half-state
that doesn't map to any real-world quantity. Worse: the rounding happens before
filtering, which degrades filter precision.

Without rounding in the handler:

```python
return {"temperature": raw_celsius}  # 23.456789...
# Framework filters it to 23.2
# Framework applies rounding... wait, where? No rounding parameter.
```

Now we need a `round=` parameter too. Scope creep is real.

---

## Recommendation

**Option A: Filter Utility Library.** Start with the library approach.

### Rationale

1. **Architectural consistency** — filters are domain logic. The framework
   provides infrastructure (MQTT, lifecycle, strategies), the domain provides
   data transformations. This is exactly what ADR-006 prescribes.

2. **The right level of abstraction** — cosalette's value proposition is
   eliminating *infrastructure* boilerplate (1,000+ lines per ADR-001), not
   *domain* boilerplate. An EWMA filter is 10 lines of code. The framework
   shouldn't absorb every 10-line utility.

3. **No semantic confusion** — the handler returns exactly what gets published
   (after strategy gating). No invisible transformations. Easy to debug, test,
   and reason about.

4. **Future-proof** — if real-world usage shows that the explicit approach is
   too verbose, we can add declarative `filter=` support later (it's additive,
   not breaking). But we can't easily *remove* framework integration once
   established. Start conservative, expand based on evidence.

5. **Broad applicability** — library filters work everywhere: `@app.telemetry`,
   `@app.device`, test utilities, standalone scripts. Declarative `filter=`
   only works in the telemetry loop.

6. **The Schmitt trigger test** — gas2mqtt's `SchmittTrigger` is also a signal
   transform, but it can't be a `filter=` parameter because it controls
   *whether* to publish (via rising-edge detection), not *what* to publish. If
   one common signal-processing pattern doesn't fit the framework abstraction,
   that's a sign the abstraction is too narrow.

### Scope for the Library

**Phase 1 — Core filters:**

| Class               | Parameters                 |
| ------------------- | -------------------------- |
| `EwmaFilter`        | `alpha: float`             |
| `MedianFilter`      | `window: int`              |
| `SlidingMeanFilter`  | `window: int`              |

All follow a `Filter` protocol:

```python
from typing import Protocol

class Filter(Protocol):
    def update(self, raw: float) -> float: ...
    def reset(self) -> None: ...
```

**Phase 2 (if demand emerges):**

| Class            | Parameters                       |
| ---------------- | -------------------------------- |
| `OutlierGuard`   | `window: int`, `sigma: float`    |
| `Clamp`          | `min: float`, `max: float`       |
| `RateOfChange`   | (derivative filter)              |

### What NOT to Do

- **Don't add `filter=` to `@app.telemetry` yet.** Wait for at least 2-3 apps
  to use the library filters before considering framework integration.
- **Don't ship a `TelemetryFilter` protocol without concrete classes.** A
  protocol without implementations is premature abstraction.
- **Don't try to framework-ify the Schmitt trigger.** It's a domain object
  with side effects (counter increment). It belongs in `@app.device` handlers.

---

## Next Steps

1. Review and approve this analysis
2. If Option A is accepted, create ADR-014 documenting the decision
3. Implement `cosalette.filters` module with Phase 1 filters
4. Add to the telemetry device guide as a recommended pattern
5. File a T-item in TODO for "revisit declarative filter= when 3+ apps exist"
