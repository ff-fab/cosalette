---
icon: material/chart-bell-curve-cumulative
---

# Signal Filters

Signal filters are **handler-level data transformations** that smooth, denoise,
or reject outliers from sensor readings before publishing. Unlike
[publish strategies](publish-strategies.md) — which control *when* to publish —
filters control *what* gets published: they clean up the raw data so downstream
consumers see stable, meaningful values instead of sensor noise.

As established in [ADR-014](../adr/ADR-014-signal-filters.md), filters are
**domain-level data transformations**, not infrastructure. They live in handler
code rather than framework decorator parameters — the handler returns exactly
what gets published, with no hidden pipeline steps between `return` and MQTT.
This preserves the hexagonal boundary (ADR-006): the domain layer stays pure
and explicit.

## The Filter Protocol

All filters implement a common protocol — a single `update(value)` method that
accepts a raw reading and returns a filtered value:

```python
class Filter(Protocol):
    def update(self, value: float) -> float: ...
```

Because every filter satisfies the same interface, filters are interchangeable.
You can swap a `Pt1Filter` for a `MedianFilter` without changing the handler
structure — only the import and constructor change.

## Available Filters

| Filter | Algorithm | Use case |
| --- | --- | --- |
| `Pt1Filter(tau, dt)` | First-order low-pass (time constant) | Noise smoothing, sample-rate-independent |
| `MedianFilter(window)` | Sliding-window median | Spike / outlier rejection |
| `OneEuroFilter(min_cutoff, beta, d_cutoff, dt)` | Adaptive 1€ Filter (Casiez 2012) | Mostly-static signals with occasional movement |

## PT1 Low-Pass Filter

A first-order IIR low-pass filter, parameterised by time constant τ and sample
interval dt. Internally it computes `α = dt / (τ + dt)` and applies the
recursive update `filtered = α·sample + (1-α)·filtered`. This is mathematically
equivalent to a classic EWMA but **sample-rate-independent** — changing the
probe interval doesn't silently alter the smoothing behaviour, because τ
describes the desired time constant in real-world seconds.

**Key parameters:**

- **`tau`** — time constant in seconds. Larger values = more smoothing (slower
  response). A τ of 5 s means the filter reaches ~63 % of a step change in 5 s.
- **`dt`** — sample interval in seconds. Should match your probe interval.

```python title="PT1 filter with init="
from cosalette.filters import Pt1Filter

def make_pt1() -> Pt1Filter:
    return Pt1Filter(tau=5.0, dt=10.0)

@app.telemetry("temperature", interval=10, init=make_pt1)
async def temperature(pt1: Pt1Filter) -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(pt1.update(raw), 1)}
```

!!! tip "`dt` should match your probe interval"

    For `@app.telemetry` with `interval=10`, use `dt=10.0`. Since `dt` is
    fixed at construction, `Pt1Filter` works best with stable intervals. For
    variable timing in `@app.device`, measure the actual interval via the
    framework's clock. For truly variable sample rates, consider
    `OneEuroFilter` instead.

## Median Filter

A sliding-window median filter. It collects the last *N* samples and returns
the median value, making it highly effective at rejecting isolated spikes and
outliers without distorting the underlying signal shape. Unlike low-pass
filters, a median filter preserves sharp step changes while still discarding
rogue readings.

**Key parameters:**

- **`window`** — number of samples in the sliding window. Odd values avoid
  ambiguity; even values average the two middle samples.

```python title="Median filter for spike rejection"
from cosalette.filters import MedianFilter

median = MedianFilter(window=5)

@app.telemetry("pressure", interval=1)
async def pressure() -> dict[str, object]:
    raw = await read_barometer()
    return {"hpa": round(median.update(raw), 1)}
```

## OneEuro Adaptive Filter

The 1€ Filter (Casiez et al., 2012) uses an adaptive cutoff frequency: it
applies **heavy smoothing when the signal is stable** (low derivative) and
**light smoothing when the signal is moving** (high derivative). This makes it
ideal for signals that are mostly static but occasionally change — e.g., a room
temperature sensor that sits at 21.3 °C for hours, then rises when heating
kicks in.

**Key parameters:**

- **`min_cutoff`** — minimum cutoff frequency (Hz). Lower = more smoothing when
  the signal is stable.
- **`beta`** — speed coefficient. Higher = faster reaction to real changes.
- **`d_cutoff`** — derivative cutoff frequency (Hz). Controls smoothing of the
  speed estimate itself. The default (1.0) is usually fine.
- **`dt`** — sample interval in seconds.

```python title="OneEuro filter for adaptive smoothing"
from cosalette.filters import OneEuroFilter

one_euro = OneEuroFilter(min_cutoff=0.5, beta=0.007, dt=30.0)

@app.telemetry("temperature", interval=30)
async def temperature() -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(one_euro.update(raw), 1)}
```

## When to Use Which Filter

| Need | Filter | Why |
| --- | --- | --- |
| Smooth noisy readings (fixed interval) | `Pt1Filter(tau, dt)` | Time-constant parameterisation; sample-rate-independent |
| Reject occasional sensor spikes | `MedianFilter(window)` | Spike-resistant; preserves step responses |
| Mostly-static signal with rare real changes | `OneEuroFilter(min_cutoff, beta, d_cutoff, dt)` | Adapts: heavy smoothing when stable, light when moving |
| Simple EWMA-style smoothing (fixed interval) | `Pt1Filter(tau, dt)` with `dt=1` | Equivalent to EWMA with α = 1/(τ+1) |

## Using `init=` for Filter State

The `init=` parameter on `@app.telemetry` is the **recommended** way to create
filter instances. It scopes the filter to the device registration, makes
ownership explicit, and keeps the filter testable in isolation — you can call
the factory in a test without standing up the full application.

```python title="Recommended: init= factory"
def make_pt1() -> Pt1Filter:
    return Pt1Filter(tau=5.0, dt=10.0)

@app.telemetry("temperature", interval=10, init=make_pt1)
async def temperature(pt1: Pt1Filter) -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(pt1.update(raw), 1)}
```

The alternative — a module-level global — works but couples filter lifetime to
module import and makes it harder to reset state between tests:

```python title="Alternative: module-level global"
pt1 = Pt1Filter(tau=5.0, dt=10.0)  # created at import time

@app.telemetry("temperature", interval=10)
async def temperature() -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(pt1.update(raw), 1)}
```

See the [telemetry device guide](../guides/telemetry-device.md) for full
`init=` documentation.

## Filters vs Strategies

**Strategies** control *when* to publish — they see the handler's return value
and decide whether to send it to MQTT. **Filters** control *what* to publish —
they transform raw data inside the handler before it reaches the strategy. The
two compose naturally by layering: the handler applies the filter, then the
framework applies the strategy.

```python title="Filter + strategy composition"
from cosalette import OnChange, Every
from cosalette.filters import Pt1Filter

def make_pt1() -> Pt1Filter:
    return Pt1Filter(tau=5.0, dt=10.0)

@app.telemetry("temp", interval=10, publish=OnChange() | Every(seconds=300), init=make_pt1)
async def temp(pt1: Pt1Filter) -> dict[str, object]:
    raw = await read_sensor()
    smoothed = pt1.update(raw)      # Filter: what to publish
    return {"celsius": smoothed}    # Strategy: when to publish
```

In this example, `Pt1Filter` smooths noise out of the raw reading, then
`OnChange() | Every(seconds=300)` ensures the smoothed value is only published
when it actually changes — or every 5 minutes as a heartbeat fallback.

## See Also

- [Publish Strategies](publish-strategies.md) — framework-level publishing
  control
- [Build a Telemetry Device](../guides/telemetry-device.md) — practical usage
  guide
- [ADR-014](../adr/ADR-014-signal-filters.md) — decision rationale for signal
  filters
