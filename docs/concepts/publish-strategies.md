---
icon: material/filter-check
---

# Publish Strategies

Publish strategies decouple **probing frequency** from **publishing frequency**.
The `@app.telemetry` handler runs on a fixed `interval=`, but the strategy
decides which results actually reach MQTT. Without a strategy, every probe
result is published — strategies let you reduce traffic, suppress noise, or
guarantee periodic heartbeats without changing the probe logic itself.

## How Strategies Work

Each telemetry cycle follows a simple lifecycle:

1. The framework calls your handler (the **probe**).
2. If the handler returns `None`, the cycle is skipped entirely — the strategy
   is never consulted.
3. Otherwise the framework calls `strategy.should_publish(result, last_published)`.
4. If the strategy says yes, the result is published and
   `strategy.on_published()` is called to update internal bookkeeping.
5. If the strategy says no, the result is silently discarded.

!!! info "Under the hood"

    This lifecycle is embedded in the framework's polling loop. The first
    probe result is **always** published (there is no previous value to
    compare against). See the
    [telemetry guide](../guides/telemetry-device.md#how-telemetry-works) for
    the full pseudo-code loop.

## Available Strategies

| Strategy                    | Publishes when…                                       |
| --------------------------- | ----------------------------------------------------- |
| `Every(seconds=N)`          | At least *N* seconds elapsed since last publish        |
| `Every(n=N)`                | Every *N*-th probe result                              |
| `OnChange()`                | The payload differs from the last published payload    |
| `OnChange(threshold=T)`     | Any numeric leaf field changed by more than *T*        |
| `OnChange(threshold={…})`   | Per-field numeric thresholds (dot-notation for nested) |

Both `Every` and `OnChange` are importable directly from the `cosalette`
package:

```python
from cosalette import Every, OnChange
```

## Threshold Modes

`OnChange` supports three progressive modes through the optional `threshold`
parameter.

### 1. Exact equality (default)

All fields are compared with `!=`. Any difference triggers a publish.

```python
publish=OnChange()
```

### 2. Global numeric threshold

Numeric fields (`int`, `float`) publish when `abs(current - previous) > T`.
Non-numeric fields (`str`, `bool`, etc.) still use exact equality.

```python
publish=OnChange(threshold=0.5)
```

### 3. Per-field thresholds

A dict maps field names to individual numeric thresholds. Use **dot-notation**
for fields inside nested dicts (`"sensor.temp"`). Unlisted fields fall back to
exact equality (`!=`).

```python
publish=OnChange(threshold={"celsius": 0.5, "humidity": 2.0})

# Nested payloads — dots traverse into child dicts
publish=OnChange(threshold={"sensor.temp": 0.5, "sensor.humidity": 2.0})
```

### Comparison Semantics

| Field type              | No threshold     | Global `T`               | Per-field `{field: T}`                    |
| ----------------------- | ---------------- | ------------------------ | ----------------------------------------- |
| `int` / `float`         | `!=`             | `abs(Δ) > T`             | `abs(Δ) > T` if listed, else `!=`         |
| `str` / `bool` / other  | `!=`             | `!=`                     | `!=`                                      |
| Nested `dict`           | recursive `!=`   | recursive leaf `abs(Δ) > T` | recursive leaf check with dot-notation |

!!! tip "Why strict `>` instead of `>=`?"

    The comparison uses strict greater-than to avoid publishing on
    floating-point noise that rounds to exactly the threshold value.

## Edge Cases

- **Structural changes** (added or removed keys at any nesting level) always
  trigger a publish.
- **Nested dicts** are traversed recursively — thresholds apply to leaf values
  only, never to intermediate dict structures.
- **`bool` is non-numeric** — `True`/`False` are not treated as `1`/`0` for
  threshold purposes.
- **`NaN` → number** transitions always trigger a publish;
  `NaN` → `NaN` is treated as unchanged.
- **Negative thresholds** raise `ValueError` at construction time.

## Composing Strategies

Strategies support `|` (OR) and `&` (AND) operators to build compound
publish rules:

```python
# Publish on change OR every 5 minutes (heartbeat guarantee)
publish=OnChange() | Every(seconds=300)

# Publish only when changed AND at least 30s have passed (debounce)
publish=OnChange() & Every(seconds=30)
```

- **`|` (OR)** — publish if **any** strategy says yes. Useful for change
  detection with a periodic heartbeat fallback.
- **`&` (AND)** — publish only if **all** strategies agree. Useful for
  debouncing rapid changes so downstream consumers aren't overwhelmed.

## Returning None

Handlers can return `None` to suppress a single cycle, **independently of any
strategy**:

```python
@app.telemetry("counter", interval=5, publish=OnChange())
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object] | None:
    meter = ctx.adapter(GasMeterPort)
    if not meter.is_ready():
        return None  # (1)!
    return {"impulses": meter.read_impulses()}
```

1. `None` skips this cycle entirely — the strategy is not consulted, and the
   "last published" value is not updated.

This is useful when the underlying hardware is temporarily unavailable.
Because the strategy state is untouched, the next successful probe is
evaluated against the most recent *published* value, not the skipped one.

## When to Use Which Strategy

| Scenario                                | Strategy                            |
| --------------------------------------- | ----------------------------------- |
| Slow-changing value, reduce MQTT traffic | `Every(seconds=N)`                 |
| Only publish on real changes            | `OnChange()`                        |
| Suppress minor fluctuations             | `OnChange(threshold=0.5)`           |
| Per-field tolerance                     | `OnChange(threshold={"temp": 0.5})` |
| Change detection with heartbeat fallback | `OnChange() \| Every(seconds=N)`   |
| Debounce rapid changes                  | `OnChange() & Every(seconds=N)`     |
| Downsample high-frequency readings      | `Every(n=N)`                        |
| Need adaptive intervals or backoff      | Use `@app.device` instead           |

!!! note "Adaptive intervals"

    If your publishing cadence needs to change at runtime (e.g., exponential
    backoff, event-driven bursts), strategies won't cover it. Use
    `@app.device` with manual `ctx.publish_state()` calls for full control
    over timing.

## Filters vs Strategies

**Strategies** (framework-level) control **when** to publish — they see the raw
payload and decide whether to send it. **Filters** (handler-level) control
**what** to publish — they transform data before it reaches the strategy. The
two compose naturally: a filter smooths noisy readings, then `OnChange`
suppresses publishes until the smoothed value drifts far enough.

```python
publish=OnChange(threshold=0.5)   # strategy — when to publish
init=make_pt1_filter              # filter  — what to publish
```

See [Signal Filters](signal-filters.md) for the full concept.

## See Also

- [Build a Telemetry Device](../guides/telemetry-device.md) — practical
  step-by-step usage guide
- [Signal Filters](signal-filters.md) — handler-level data transformations
- [ADR-013](../adr/ADR-013-telemetry-publish-strategies.md) — decision
  rationale for the strategy system
