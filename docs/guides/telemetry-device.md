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

## Imperative Registration

The `@app.telemetry` decorator works great when the handler is defined in the same
module as the `App`. When the handler lives in a **separate module** — a sensor
library, a shared utility, or a generated function — the decorator forces you to
write a pass-through wrapper:

```python title="app.py — wrapper approach (verbose)"
from my_sensors import read_temperature

@app.telemetry("temperature", interval=30)
async def temperature(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return await read_temperature(ctx)  # just forwarding
```

The `app.add_telemetry()` method eliminates the wrapper — register the imported
function directly:

```python title="app.py — imperative approach"
from my_sensors import read_temperature

app.add_telemetry("temperature", read_temperature, interval=30)
```

### Full Signature

```python
app.add_telemetry(
    name,           # device name (always required — no root device)
    func,           # async callable returning dict | None
    *,
    interval,       # polling interval in seconds
    publish=None,   # optional PublishStrategy
    persist=None,   # optional PersistPolicy
    init=None,      # optional synchronous factory
)
```

All keyword parameters behave identically to the decorator form.

### Using `init=`

`init=` works the same way as the decorator — pass a synchronous factory whose
return value is injected by type:

```python title="app.py"
from my_sensors import read_temperature
from cosalette.filters import Pt1Filter


def make_filter() -> Pt1Filter:
    return Pt1Filter(tau=5.0, dt=10.0)


app.add_telemetry(
    "temperature",
    read_temperature,
    interval=10,
    publish=cosalette.OnChange(threshold=0.5),
    init=make_filter,
)
```

### Choosing Between Decorator and Imperative

| Scenario | Preferred style |
| --- | --- |
| Handler defined inline, same file | `@app.telemetry` decorator |
| Handler imported from another module | `app.add_telemetry()` |
| Handler generated dynamically (factory) | `app.add_telemetry()` |
| Registering in a loop | `app.add_telemetry()` |

/// admonition | Identical validation
    type: info

Both paths run the same registration logic — signature validation,
`init=` type-collision checks, and duplicate-name detection happen
identically whether you use the decorator or `add_telemetry()`.
///

/// admonition | Named devices only
    type: warning

`add_telemetry()` always requires a device name — root (unnamed)
devices can only be created via the decorator.
///

## Conditional Registration

Use `enabled=` to skip registration based on a settings flag — no `if` block needed:

```python title="Before — imperative if-block"
settings = app.settings

if settings.enable_temperature:
    @app.telemetry("temperature", interval=30)
    async def temperature() -> dict[str, object]:
        return {"celsius": read_temp()}
```

```python title="After — declarative enabled="
settings = app.settings

@app.telemetry("temperature", interval=30, enabled=settings.enable_temperature)
async def temperature() -> dict[str, object]:
    return {"celsius": read_temp()}
```

The imperative form works identically:

```python
app.add_telemetry("temperature", temperature, interval=30, enabled=settings.enable_temperature)
```

/// admonition | Disabled devices are invisible
    type: info

When `enabled=False`, the device is not registered at all — it won't
appear in MQTT topics, won't reserve a name slot, and won't consume
resources at runtime.
///

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

For threshold modes, comparison semantics, edge cases, and composition details,
see [Publish Strategies](../concepts/publish-strategies.md).

### Returning None

Handlers can return `None` to suppress a single cycle, independently of any
strategy. The strategy is not consulted for `None` returns, and the "last
published" value is not updated.

```python title="app.py"
@app.telemetry("counter", interval=5, publish=OnChange())
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object] | None:
    meter = ctx.adapter(GasMeterPort)
    if not meter.is_ready():
        return None  # skips this cycle entirely
    return {"impulses": meter.read_impulses()}
```

## Initialisation Callbacks (`init=`)

When a telemetry handler needs **per-device state** — such as a filter instance,
a calibration table, or a connection pool — the `init=` parameter provides a
clean way to create it once and inject it into every poll cycle.

Without `init=`, you'd resort to module-level globals or closures.
`init=` keeps state creation explicit, co-located with the decorator, and
testable in isolation.

### Basic Usage

```python title="app.py"
class SmoothingFilter:
    """Moving-average filter for noisy sensor readings."""

    def __init__(self, window: int = 5) -> None:
        self.readings: list[float] = []
        self.window = window

    def update(self, value: float) -> float:
        self.readings.append(value)
        if len(self.readings) > self.window:
            self.readings.pop(0)
        return sum(self.readings) / len(self.readings)


def make_filter() -> SmoothingFilter:  # (1)!
    return SmoothingFilter(window=10)


@app.telemetry("temperature", interval=30, init=make_filter)  # (2)!
async def temperature(smoother: SmoothingFilter) -> dict[str, object]:  # (3)!
    raw = read_sensor()
    return {"celsius": smoother.update(raw)}
```

1. The factory is a plain synchronous callable.  It runs **once** before
   the first poll cycle — not on every interval.
2. `init=make_filter` tells the framework to call `make_filter()` and inject
   the result into the handler.
3. The handler declares `smoother: SmoothingFilter` — the framework matches
   the return type of the init callback to this parameter automatically.

### How It Works

1. The framework calls `init()` **once** before the handler's polling loop
   starts.
2. The return value is added to the dependency-injection provider map, keyed
   by its type.
3. Any handler parameter whose type annotation matches the init result type
   receives the same instance on every invocation.
4. The init callback can itself receive injected parameters (e.g.
   `Settings`) — the same DI machinery used for handler parameters.

### Combining with Filters and Strategies

`init=` pairs naturally with the framework's built-in filters and publish
strategies.  Use `init=` to create the filter instance, and `publish=` to
control when results are sent:

```python title="app.py"
from cosalette import OnChange
from cosalette.filters import Pt1Filter


def make_pt1() -> Pt1Filter:
    return Pt1Filter(tau=5.0, dt=10.0)


@app.telemetry(
    "temperature",
    interval=10,
    publish=OnChange(threshold=0.5),
    init=make_pt1,
)
async def temperature(pt1: Pt1Filter) -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(pt1.update(raw), 1)}
```

Compare this to a module-level `pt1 = Pt1Filter(...)` pattern — `init=`
achieves the same result but scopes the filter to the device registration,
making it explicit which device owns the state.

### Rules and Constraints

- **Synchronous only** — `async def` init callbacks raise `TypeError` at
  decoration time.  The callback runs during bootstrap, before the async
  event loop processes device tasks.
- **Type collision guard** — if the init callback returns a type the framework
  already provides (`Settings`, `DeviceContext`, `Logger`, `ClockPort`,
  `Event`), a `TypeError` is raised immediately.  Use a wrapper class if
  you need to inject something with a colliding type.
- **Fail-fast validation** — bad signatures (e.g. un-annotated parameters)
  are caught at decoration time, not at runtime.

## Signal Filters

Filters are handler-level data transformations that smooth or clean sensor
readings before they reach publish strategies. Unlike strategies that control
*when* to publish, filters control *what* is published. They implement the
`Filter` protocol (`update(value) -> float`) and compose naturally.

### Available Filters

cosalette ships three filter implementations in `cosalette.filters`:

| Filter | Algorithm | Use case |
| ------ | --------- | -------- |
| `Pt1Filter(tau, dt)` | First-order low-pass (time constant) | Noise smoothing, sample-rate-independent |
| `MedianFilter(window)` | Sliding-window median | Spike / outlier rejection |
| `OneEuroFilter(min_cutoff, beta, d_cutoff, dt)` | Adaptive 1€ Filter (Casiez 2012) | Mostly-static signals with occasional movement |

### Example: PT1 Filter with `init=`

```python title="app.py"
from cosalette import OnChange
from cosalette.filters import Pt1Filter


def make_pt1() -> Pt1Filter:
    return Pt1Filter(tau=5.0, dt=10.0)


@app.telemetry(
    "temperature",
    interval=10,
    publish=OnChange(threshold=0.5),
    init=make_pt1,
)
async def temperature(pt1: Pt1Filter) -> dict[str, object]:
    raw = await read_sensor()
    return {"celsius": round(pt1.update(raw), 1)}
```

For algorithm details, parameter tuning, and the decision table, see
[Signal Filters](../concepts/signal-filters.md).

## Persistence

Telemetry devices can **persist state across restarts** using the `store=`
and `persist=` parameters.

### Basic Usage

Pass a `Store` backend to the app, then declare `store: DeviceStore` in
your handler:

```python
import cosalette
from cosalette import JsonFileStore, DeviceStore, SaveOnPublish

app = cosalette.App(
    "myapp", "1.0.0",
    store=JsonFileStore("./data/state.json"),
)

@app.telemetry("counter", interval=30, persist=SaveOnPublish())
async def counter(store: DeviceStore) -> dict[str, object]:
    store["total"] = store.get("total", 0) + 1
    return {"total": store["total"]}
```

### Available Save Policies

| Policy | Saves when |
| --- | --- |
| `SaveOnPublish()` | After each MQTT publish |
| `SaveOnChange()` | Whenever the store is dirty |
| `SaveOnShutdown()` | Only on graceful shutdown |

Policies compose with `|` (OR) and `&` (AND):

```python
persist = SaveOnPublish() | SaveOnChange()  # save on either condition
```

### Combining with Other Features

Persistence works seamlessly with publish strategies, filters, and
init callbacks:

```python
from cosalette import DeviceStore, OnChange, Pt1Filter, SaveOnPublish

@app.telemetry(
    "sensor",
    interval=10,
    publish=OnChange(threshold=0.5),
    persist=SaveOnPublish(),
    init=lambda: Pt1Filter(tau=2.0, dt=10.0),
)
async def sensor(
    store: DeviceStore,
    lpf: Pt1Filter,
) -> dict[str, object]:
    raw = 21.5  # e.g. from an adapter
    filtered = lpf.update(raw)
    store["last_value"] = filtered
    return {"value": filtered}
```

!!! tip "Testing persistence"
    Use `MemoryStore()` in tests — it keeps data in memory with no
    filesystem access. See the [Testing Guide](testing.md) for details.

For full details, see the [Persistence concept](../concepts/persistence.md).

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

@app.telemetry("counter", interval=app.settings.poll_interval)  # (1)!
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

1. `app.settings` is available at decoration time because `App.__init__` eagerly
   instantiates the settings class. The `poll_interval` value here reflects
   environment variables and `.env` files — no hardcoded constants needed.

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

## Coalescing Groups

When multiple telemetry handlers share a physical resource (e.g. a serial bus),
use the `group=` parameter to coalesce them into a shared execution window:

```python
@app.telemetry(name="outdoor", interval=300, group="optolink")
async def outdoor(port: OptolinkPort) -> dict[str, object]:
    return await port.read_signals(["outdoor_temp"])

@app.telemetry(name="hotwater", interval=300, group="optolink")
async def hotwater(port: OptolinkPort) -> dict[str, object]:
    return await port.read_signals(["hot_water_temp"])
```

Handlers in the same group execute sequentially within a batch when their
intervals coincide. At t=0 all grouped handlers fire together; at subsequent
ticks only those whose interval divides evenly into the elapsed time fire.

Each handler retains its own publish strategy, error isolation, persistence
policy, and init function. The `group=` parameter is purely an execution
scheduling hint.

See [ADR-018](../adr/ADR-018-coalescing-groups.md) for the full design rationale.

---

## See Also

- [Device Archetypes](../concepts/device-archetypes.md) — telemetry vs command
  archetypes
- [MQTT Topics](../concepts/mqtt-topics.md) — the `{prefix}/{device}/state` topic
  layout
- [Architecture](../concepts/architecture.md) — how devices fit into the framework
- [Publish Strategies](../concepts/publish-strategies.md) — publishing control concepts
- [Signal Filters](../concepts/signal-filters.md) — handler-level data transformations
- [ADR-010](../adr/ADR-010-device-archetypes.md) — the decision behind device
  archetypes
- [ADR-013](../adr/ADR-013-telemetry-publish-strategies.md) — the decision behind
  publish strategies
- [ADR-014](../adr/ADR-014-signal-filters.md) — the decision behind signal filters
- [ADR-018](../adr/ADR-018-coalescing-groups.md) — the decision behind coalescing
  groups
