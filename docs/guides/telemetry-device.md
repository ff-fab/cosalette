---
icon: material/thermometer
---

# Build a Telemetry Device

Telemetry devices are the most common archetype in cosalette. They poll a sensor at a
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
   invocations under the hood.
4. Is **error-isolated** — if one poll raises an exception, the framework logs the error,
   publishes it to the error topic, and _continues the loop_. A single bad reading
   never stops the daemon.

This is the **return-dict contract**: your function produces data, the framework
handles delivery. Compare this to `@app.device` where _you_ own the main loop and
call `ctx.publish_state()` manually (see
[Command & Control Device](command-device.md)).

!!! info "Under the hood"

    The framework wraps your telemetry function in a loop roughly equivalent to:

    ```python
    while not ctx.shutdown_requested:
        try:
            result = await your_function(ctx)
            await ctx.publish_state(result)
        except Exception as exc:
            log_and_publish_error(exc)
        await ctx.sleep(interval)
    ```

    You never write this loop yourself — that's the whole point.

## A Minimal Telemetry Device

The simplest telemetry handler takes zero arguments — just return a dict:

```python title="app.py"
import cosalette

app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.telemetry("counter", interval=60)  # (1)!
async def counter() -> dict[str, object]:  # (2)!
    """Read the gas meter impulse count."""
    return {"impulses": 42, "unit": "m³"}  # (3)!


app.run()
```

1. `"counter"` is the device name — it determines the MQTT topic:
   `gas2mqtt/counter/state`. `interval=60` means polling every 60 seconds.
2. Zero-arg handlers are valid. The framework injects nothing — your function
   just returns data. You can also request `ctx: DeviceContext` if needed.
3. The returned dict is published as `{"impulses": 42, "unit": "m³"}` to
   `gas2mqtt/counter/state` with `retain=True` and `qos=1`.

When you run this, the framework:

- Connects to the MQTT broker.
- Calls `counter()` every 60 seconds.
- Publishes the returned dict as JSON to `gas2mqtt/counter/state`.
- Keeps running until `SIGTERM` or `SIGINT`.

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
        "unit": "m³",
    }


app.run()
```

## Error Behaviour

When a telemetry function raises an exception:

1. The framework catches it (except `CancelledError`).
2. Logs the error at `ERROR` level.
3. Publishes a structured error payload to `gas2mqtt/error` and
   `gas2mqtt/counter/error`.
4. **Continues the polling loop** — the next interval runs normally.

This means transient failures (sensor timeouts, I/O glitches) are self-healing. The
daemon stays up and retries on the next cycle.

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

!!! warning "Interval must be positive"

    `interval` must be `> 0`. Passing `interval=0` or a negative value raises
    `ValueError` at registration time — the framework catches this early rather than
    failing at runtime.

---

## See Also

- [Device Archetypes](../concepts/device-archetypes.md) — telemetry vs command
  archetypes
- [MQTT Topics](../concepts/mqtt-topics.md) — the `{prefix}/{device}/state` topic
  layout
- [Architecture](../concepts/architecture.md) — how devices fit into the framework
- [ADR-010](../adr/ADR-010-device-archetypes.md) — the decision behind device
  archetypes
