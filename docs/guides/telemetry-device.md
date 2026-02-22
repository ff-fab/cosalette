---
icon: material/thermometer
---

# Build a Telemetry Device

Telemetry devices are the most simple archetype in cosalette. They poll a sensor at a
fixed interval and publish a JSON state message — the framework handles the timing loop,
serialisation, and error isolation for you.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## How Telemetry Works

The `@app.telemetry` decorator registers a function that:

1. Optionally receives a `DeviceContext` or other injectable parameters.
2. **Returns a dict** — the framework JSON-serialises it and publishes to
   `{prefix}/{name}/state`.
3. Runs on a fixed interval — the framework calls `await ctx.sleep(interval)` between
   invocations under the hood. This is the **probing** frequency.
4. Optionally uses a **publish strategy** (`publish=`) to control which probe results
   are actually published — decoupling probing from publishing.
5. Can **return `None`** to suppress a single cycle.
6. Is **error-isolated** — if one poll raises an exception, the framework logs the error,
   publishes it to the error topic, and _continues the loop_. A single bad reading
   never stops the daemon.

This is the **return-dict contract**: your function produces data, the framework
handles delivery. Compare this to `@app.device` where _you_ own the main loop and
call `ctx.publish_state()` manually (see
[Command & Control Device](command-device.md)).

!!! info "Under the hood"

    The framework wraps your telemetry function in a loop roughly equivalent to:

    ```python
    strategy = ...  # from the publish= parameter, or None
    last_published = None
    last_error_type = None
    while not ctx.shutdown_requested:
        try:
            result = await your_function(ctx)
            if result is None:
                await ctx.sleep(interval)
                continue
            should_publish = (
                last_published is None          # First → always
                or strategy is None             # No strategy → always
                or strategy.should_publish(result, last_published)
            )
            if should_publish:
                await ctx.publish_state(result)
                last_published = result
                if strategy is not None:
                    strategy.on_published()
            if last_error_type is not None:
                log_recovery()
                last_error_type = None
        except Exception as exc:
            if type(exc) is not last_error_type:
                log_and_publish_error(exc)
            last_error_type = type(exc)
        await ctx.sleep(interval)
    ```

    You never write this loop yourself — that's the task of the framework.

## A Minimal Telemetry Device

The simplest telemetry handler takes zero arguments — just return a dict:

```python title="app.py"
import cosalette

app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.telemetry("counter", interval=60)  # (1)!
async def counter() -> dict[str, object]:  # (2)!
    """Read the gas meter impulse count."""
    return {"impulses": 42}  # (3)!


app.run()
```

1. `"counter"` is the device name — it determines the MQTT topic:
   `gas2mqtt/counter/state`. `interval=60` means polling every 60 seconds.
2. Zero-arg handlers are valid. The framework injects nothing — your function
   just returns data. You can also request `ctx: DeviceContext` if needed.
3. The returned dict is published as `{"impulses": 42}` to
   `gas2mqtt/counter/state` with `retain=True` and `qos=1`.

When you run this, the framework:

- Connects to the MQTT broker.
- Calls `counter()` every 60 seconds.
- Publishes the returned dict as JSON to `gas2mqtt/counter/state`.
- Keeps running until `SIGTERM` or `SIGINT`.

## Single-Device Apps (Root Device)

When your app has only one device, you can omit the device name entirely.
The framework publishes directly to root-level topics — no `/{device}/`
segment:

```python title="app.py"
import cosalette

app = cosalette.App(name="weather2mqtt", version="1.0.0")


@app.telemetry(interval=30)  # (1)!
async def read_sensor() -> dict[str, object]:
    """Read weather station sensors."""
    return {"temperature": 21.5, "humidity": 58.0}


app.run()
```

1. No device name — the function name `read_sensor` is used internally for
   logging. The MQTT topic is `weather2mqtt/state` (no device segment).

**Topic layout:**

| Pattern                  | Named device                    | Root device             |
| ------------------------ | ------------------------------- | ----------------------- |
| State                    | `weather2mqtt/sensor/state`     | `weather2mqtt/state`    |
| Availability             | `weather2mqtt/sensor/availability` | `weather2mqtt/availability` |
| Error                    | `weather2mqtt/sensor/error`     | _(global only)_         |

!!! info "One root device per app"

    An app can have at most **one** root (unnamed) device. Registering a
    second raises `ValueError`. You can mix one root device with named
    devices, but the framework logs a warning — this combination is unusual
    and may indicate a design issue.

## Using DeviceContext

When your handler needs infrastructure access, declare a `ctx: DeviceContext`
parameter — the framework injects it automatically:

```python title="app.py"
@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    settings = ctx.settings          # (1)!
    device_name = ctx.name           # (2)!
    clock_value = ctx.clock.now()    # (3)!

    return {"impulses": 42, "read_at": clock_value}
```

1. Access the application `Settings` instance (or your custom subclass).
2. The device name as registered — `"counter"` in this case.
3. The monotonic clock port — useful for timing calculations. In tests, this is a
   `FakeClock` you control directly.

!!! warning "DeviceContext vs AppContext"

    Telemetry and device functions can request `DeviceContext`, which has publish,
    sleep, and on_command capabilities. The lifespan function receives `AppContext`,
    which only has `.settings` and `.adapter()`. Don't mix them up — see
    [Lifespan](lifespan.md) for details.

## Resolving Adapters

When your telemetry device needs hardware access, use the adapter pattern:

```python title="app.py"
from gas2mqtt.ports import GasMeterPort

@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    meter = ctx.adapter(GasMeterPort)  # (1)!
    reading = meter.read_impulses()
    return {"impulses": reading}
```

1. Resolves the adapter registered for `GasMeterPort`. Raises `LookupError` if no
   adapter is registered. See [Hardware Adapters](adapters.md) for registration.

## Multiple Sensors in One App

A single app can register multiple telemetry devices, each with its own interval:

```python title="app.py"
import cosalette
from gas2mqtt.ports import GasMeterPort

app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Read impulse count every 60 seconds."""
    meter = ctx.adapter(GasMeterPort)
    return {"impulses": meter.read_impulses()}


@app.telemetry("temperature", interval=30)
async def temperature(ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Read the meter's temperature sensor every 30 seconds."""
    meter = ctx.adapter(GasMeterPort)
    return {"celsius": meter.read_temperature()}


app.run()
```

Each telemetry device runs as an independent asyncio task. They share the same MQTT
connection and adapter instances, but their polling loops are completely independent.
If `temperature` fails, `counter` keeps running.

**Topic layout:**

| Device        | Topic                          | Interval |
| ------------- | ------------------------------ | -------- |
| `counter`     | `gas2mqtt/counter/state`       | 60 s     |
| `temperature` | `gas2mqtt/temperature/state`   | 30 s     |

## Publish Strategies

By default, every probe result is published to MQTT. **Publish strategies** let you
decouple the probing frequency from the publishing frequency — the handler runs on
`interval`, but only selected results are actually sent.

### Basic Usage

```python title="app.py"
from cosalette import Every, OnChange

@app.telemetry("temperature", interval=10, publish=Every(seconds=300))
async def temperature() -> dict[str, object]:
    """Probe every 10s, publish at most once every 5 minutes."""
    return {"celsius": await read_sensor()}
```

Without `publish=`, the behaviour is exactly as before — every result is published.

### Available Strategies

| Strategy           | Publishes when…                                    |
| ------------------ | -------------------------------------------------- |
| `Every(seconds=N)` | At least *N* seconds elapsed since last publish     |
| `Every(n=N)`       | Every *N*-th probe result                           |
| `OnChange()`       | The payload differs from the last published payload |
| `OnChange(threshold=T)` | Any numeric leaf field changed by more than *T*     |
| `OnChange(threshold={…})` | Per-field numeric thresholds (dot-notation for nested) |

#### Count-Based Publishing

`Every(n=N)` publishes every *N*-th probe result, useful for downsampling
high-frequency readings:

```python title="app.py"
@app.telemetry("power", interval=0.1, publish=Every(n=10))
async def power() -> dict[str, object]:
    """Sample power 10× per second, publish once per second."""
    return {"watts": await read_power_meter()}
```

### Composing Strategies

Combine strategies with `|` (OR) and `&` (AND):

```python title="app.py"
# Publish on change OR every 5 minutes (heartbeat guarantee)
@app.telemetry("temp", interval=10, publish=OnChange() | Every(seconds=300))
async def temp() -> dict[str, object]:
    return {"celsius": await read_sensor()}

# Publish only when changed AND at least 30s have passed (debounce)
@app.telemetry("temp", interval=10, publish=OnChange() & Every(seconds=30))
async def temp() -> dict[str, object]:
    return {"celsius": await read_sensor()}
```

- **`|` (OR)**: publish if **any** strategy says yes — useful for change detection
  with a periodic heartbeat fallback.
- **`&` (AND)**: publish only if **all** strategies agree — useful for debouncing
  rapid changes.

### Threshold Modes

`OnChange` supports three progressive modes through the optional `threshold`
parameter:

#### Exact equality (default)

```python title="app.py"
@app.telemetry("door", interval=5, publish=OnChange())
async def door() -> dict[str, object]:
    """Publish only when the door state actually changes."""
    return {"open": read_reed_switch()}
```

Every field is compared with `!=`. Any difference triggers a publish.

#### Global numeric threshold

```python title="app.py"
@app.telemetry("temperature", interval=10, publish=OnChange(threshold=0.5))
async def temperature() -> dict[str, object]:
    """Publish only when temperature moves by more than 0.5 °C."""
    return {"celsius": await read_sensor()}
```

Numeric fields (`int`, `float`) publish when `abs(current - previous) > 0.5`.
Non-numeric fields (`str`, `bool`, etc.) still use exact equality.

#### Per-field thresholds

```python title="app.py"
@app.telemetry(
    "weather",
    interval=10,
    publish=OnChange(threshold={"celsius": 0.5, "humidity": 2.0}),
)
async def weather() -> dict[str, object]:
    """Each field gets its own threshold."""
    return {"celsius": await read_temp(), "humidity": await read_rh()}
```

For **nested payloads**, use dot-notation to target leaf fields:

```python title="app.py"
@app.telemetry(
    "environment",
    interval=10,
    publish=OnChange(threshold={"sensor.temp": 0.5, "sensor.humidity": 2.0}),
)
async def environment() -> dict[str, object]:
    """Thresholds apply to leaf values inside nested dicts."""
    return {
        "sensor": {"temp": await read_temp(), "humidity": await read_rh()},
        "name": "outdoor",
    }
```

Intermediate dicts (like `"sensor"`) are traversed automatically — thresholds
are always applied to the **leaf** values (`temp`, `humidity`), never to the
dict as a whole.

Fields listed in the dict use their specific threshold. Unlisted fields fall
back to exact equality (`!=`). Nested dicts are traversed recursively —
thresholds always apply to leaf values, not intermediate dict structures.

#### Comparison semantics

| Field type         | No threshold | Global `T`     | Per-field `{field: T}`          |
| ------------------ | ------------ | -------------- | ------------------------------- |
| `int` / `float`    | `!=`         | `abs(Δ) > T`   | `abs(Δ) > T` if listed, else `!=` |
| `str` / `bool` / other | `!=`     | `!=`           | `!=`                            |
| Nested `dict`      | recursive `!=` | recursive leaf `abs(Δ) > T` | recursive leaf check with dot-notation |

!!! tip "Why strict `>` instead of `>=`?"

    The comparison uses strict greater-than to avoid publishing on
    floating-point noise that rounds to exactly the threshold value.

!!! note "Edge cases"

    - **Structural changes** (added or removed keys at any nesting level) always trigger a publish.
    - **Nested dicts** are traversed recursively — thresholds apply to leaf values only.
    - **`bool` is non-numeric** — `True`/`False` are not treated as `1`/`0`
      for threshold purposes.
    - **`NaN` → number** transitions always trigger; `NaN` → `NaN` is treated
      as unchanged.
    - **Negative thresholds** raise `ValueError` at construction time.

### Returning None

Handlers can return `None` to suppress a single cycle, independently of any
strategy:

```python title="app.py"
@app.telemetry("counter", interval=5, publish=OnChange())
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object] | None:
    meter = ctx.adapter(GasMeterPort)
    if not meter.is_ready():
        return None  # (1)!
    return {"impulses": meter.read_impulses()}
```

1. `None` skips this cycle entirely — the strategy is not consulted, and the
   "last published" value is not updated.

### Filters vs Strategies

**Strategies** (framework-level) control *when* to publish — they see the raw
payload and decide whether to send it. **Filters** (handler-level) control
*what* to publish — they transform the data before it reaches the strategy.

They compose naturally by layering:

```python title="app.py"
from cosalette import Every, OnChange
from cosalette.filters import Pt1Filter

pt1 = Pt1Filter(tau=5.0, dt=10.0)  # handler-level filter

@app.telemetry("temp", interval=10, publish=OnChange() | Every(seconds=300))
async def temp() -> dict[str, object]:
    raw = await read_sensor()
    smoothed = pt1.update(raw)      # Filter: what to publish
    return {"celsius": smoothed}    # Strategy: when to publish
```

See [ADR-014](../adr/ADR-014-signal-filters.md) for the decision rationale:
filters are *domain-level data transformations*, not infrastructure, so they
live in handler code rather than framework decorator parameters.

### Available Filters

cosalette ships three filter implementations in `cosalette.filters`:

| Filter | Algorithm | Use case |
| ------ | --------- | -------- |
| `Pt1Filter(tau, dt)` | First-order low-pass (time constant) | Noise smoothing, sample-rate-independent |
| `MedianFilter(window)` | Sliding-window median | Spike / outlier rejection |
| `OneEuroFilter(min_cutoff, beta, d_cutoff, dt)` | Adaptive 1€ Filter (Casiez 2012) | Mostly-static signals with occasional movement |

All filters implement the `Filter` protocol — a single `update(value) -> float`
method — and can be used interchangeably.

### Filter Examples

#### PT1 low-pass filter

```python title="app.py"
from cosalette.filters import Pt1Filter
from cosalette import Every, OnChange

pt1 = Pt1Filter(tau=5.0, dt=10.0)  # 5 s time constant, 10 s probe interval

@app.telemetry("temperature", interval=10, publish=OnChange(threshold=0.5))
async def temperature() -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(pt1.update(raw), 1)}
```

!!! tip "Setting `dt` correctly"
    The `dt` parameter should match your probe interval. For `@app.telemetry`
    with `interval=10`, use `dt=10.0`.

    Since `dt` is fixed at construction, it works best with stable intervals.
    If your `@app.device` loop has variable timing, you can measure the
    first interval via the framework's clock and use that to initialise
    the filter once:

    ```python
    clock: ClockPort = ctx.clock
    last_t = clock.now()
    await ctx.sleep(interval)
    dt = clock.now() - last_t  # actual elapsed seconds
    pt1 = Pt1Filter(tau=5.0, dt=dt)  # created once with measured dt
    ```

    For truly variable sample rates, consider `OneEuroFilter` — it handles
    fixed `dt` as an approximation more gracefully due to its adaptive
    cutoff.

#### Median filter (spike rejection)

```python title="app.py"
from cosalette.filters import MedianFilter
from cosalette import Every

median = MedianFilter(window=5)  # reject spikes over 5-sample window

@app.telemetry("pressure", interval=1, publish=Every(seconds=60))
async def pressure() -> dict[str, object]:
    raw = await read_barometer()
    return {"hpa": round(median.update(raw), 1)}
```

#### OneEuro adaptive filter

```python title="app.py"
from cosalette.filters import OneEuroFilter
from cosalette import OnChange

# Adaptive: smooth when stable, responsive when moving
one_euro = OneEuroFilter(min_cutoff=0.5, beta=0.007, dt=30.0)

@app.telemetry("temperature", interval=30, publish=OnChange(threshold=0.1))
async def temperature() -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(one_euro.update(raw), 1)}
```

### When to Use Which Filter

| Need | Filter | Why |
| ---- | ------ | --- |
| Smooth noisy readings (fixed interval) | `Pt1Filter(tau, dt)` | Time-constant parameterisation; sample-rate-independent |
| Reject occasional sensor spikes | `MedianFilter(window)` | Spike-resistant; preserves step responses |
| Mostly-static signal with rare real changes | `OneEuroFilter(min_cutoff, beta, d_cutoff, dt)` | Adapts: heavy smoothing when stable, light when moving |
| Simple EWMA-style smoothing (fixed interval) | `Pt1Filter(tau, dt)` with `dt=1` | Equivalent to EWMA with α = 1/(τ+1) — set `dt` to your actual interval |

### When to Use Strategies

| Scenario                                  | Strategy                              |
| ----------------------------------------- | ------------------------------------- |
| Slow-changing value, reduce MQTT traffic  | `Every(seconds=N)`                    |
| Only publish on real changes              | `OnChange()`                          |
| Suppress minor fluctuations               | `OnChange(threshold=0.5)`             |
| Per-field tolerance                       | `OnChange(threshold={"temp": 0.5})`   |
| Change detection with heartbeat fallback  | `OnChange() \| Every(seconds=N)`      |
| Debounce rapid changes                    | `OnChange() & Every(seconds=N)`       |
| Downsample high-frequency readings        | `Every(n=N)`                          |
| Need adaptive intervals or backoff        | Use `@app.device` instead             |

## Practical Example: Gas Meter Impulse Counter

Here's a complete, realistic telemetry device for a gas meter with a reed switch
impulse sensor:

```python title="app.py"
"""gas2mqtt — Gas meter impulse counter bridge."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import cosalette
from pydantic import Field
from pydantic_settings import SettingsConfigDict


# --- Port (Protocol) for hardware abstraction ---

@runtime_checkable
class GasMeterPort(Protocol):
    """Hardware abstraction for gas meter impulse sensors."""

    def read_impulses(self) -> int: ...
    def read_temperature(self) -> float: ...


# --- Settings ---

class Gas2MqttSettings(cosalette.Settings):
    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
    serial_port: str = Field(default="/dev/ttyUSB0")
    poll_interval: int = Field(default=60, ge=1)


# --- App ---

app = cosalette.App(
    name="gas2mqtt",
    version="1.0.0",
    settings_class=Gas2MqttSettings,
)


# --- Telemetry device ---

@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Read gas meter impulses and publish state."""
    meter = ctx.adapter(GasMeterPort)
    impulses = meter.read_impulses()
    temp = meter.read_temperature()

    return {
        "impulses": impulses,
        "temperature_celsius": temp,
    }


app.run()
```

## Error Behaviour

When a telemetry function raises an exception, the framework applies
**state-transition deduplication**:

1. **First error** — caught, logged at `ERROR` level, and published to
   `gas2mqtt/error` and `gas2mqtt/counter/error`. The device health status
   in the heartbeat is set to `"error"`.
2. **Repeated same-type errors** — suppressed. No additional MQTT publishes
   until the error type changes. This prevents flooding the broker when a
   sensor is persistently broken.
3. **Different error type** — treated as a new error: published and logged.
4. **Recovery** — when the next poll succeeds after a failure, recovery is
   logged at `INFO` level and the device health status is restored to
   `"ok"` in the heartbeat.
5. **Continues the polling loop** — the next interval always runs.

This means transient failures (sensor timeouts, I/O glitches) are self-healing. The
daemon stays up and retries on the next cycle. Persistent failures produce a single
error event instead of flooding MQTT with identical messages every interval.

```python title="Example error flow"
@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    meter = ctx.adapter(GasMeterPort)
    reading = meter.read_impulses()  # (1)!
    if reading < 0:
        raise ValueError(f"Invalid impulse count: {reading}")  # (2)!
    return {"impulses": reading}
```

1. If `read_impulses()` raises `OSError`, the framework catches it and publishes an
   error payload. The loop continues.
2. You can also raise explicitly — the framework treats it the same way.

!!! tip "Custom error types"

    For machine-readable error classification, define an `error_type_map`. See
    [Custom Error Types](error-types.md) for details.

## Interval Guidelines

| Sensor Type             | Typical Interval | Notes                              |
| ----------------------- | ---------------- | ---------------------------------- |
| Temperature / humidity  | 30–60 s          | Slow-changing physical quantities  |
| Energy / impulse        | 10–60 s          | Depends on consumption rate        |
| Motion / presence       | 1–5 s            | Fast-changing binary sensor        |
| Battery level           | 300–600 s        | Very slow-changing                 |

---

## See Also

- [Device Archetypes](../concepts/device-archetypes.md) — telemetry vs command
  archetypes
- [MQTT Topics](../concepts/mqtt-topics.md) — the `{prefix}/{device}/state` topic
  layout
- [Architecture](../concepts/architecture.md) — how devices fit into the framework
- [ADR-010](../adr/ADR-010-device-archetypes.md) — the decision behind device
  archetypes
- [ADR-013](../adr/ADR-013-telemetry-publish-strategies.md) — the decision behind
  publish strategies
- [ADR-014](../adr/ADR-014-signal-filters.md) — the decision behind signal filters
