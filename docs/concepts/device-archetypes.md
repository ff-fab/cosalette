---
icon: material/devices
---

# Device Archetypes

Cosalette recognises three device archetypes. Every device in an IoT-to-MQTT bridge
falls into one of these categories — or can be expressed as a composition of them.

## Device Archetypes

| Aspect              | Command (`@app.command`)             | Telemetry (`@app.telemetry`)       | Device (`@app.device`)             |
|---------------------|--------------------------------------|------------------------------------|------------------------------------|
| **Direction**       | Bidirectional                        | Unidirectional (device → broker)   | Bidirectional or unidirectional    |
| **Execution model** | Per-message dispatch                 | Framework-managed polling loop     | Long-running coroutine             |
| **Inbound commands**| Automatic — handler receives them    | Not applicable                     | `@ctx.on_command` handler          |
| **State publishing**| Automatic — return a `dict`          | Automatic — return a `dict`        | Manual via `ctx.publish_state()`   |
| **Publish control** | Not applicable                       | `publish=` strategies              | Manual (your loop logic)           |
| **Typical devices** | GPIO relays, WiFi bulbs, simple actuators | BLE sensors, I²C temperature probes | State machines, combined patterns |

=== "Command (`@app.command`)"

    ```mermaid
    graph LR
        A[MQTT /set topic] -->|message| B[Handler function]
        B -->|return dict| C[Framework publishes to /state]
    ```

=== "Telemetry (`@app.telemetry`)"

    ```mermaid
    graph LR
        D[Hardware sensor] -->|read| E[Polling function]
        E -->|return dict| F[Framework publishes to /state]
    ```

=== "Device (`@app.device`)"

    ```mermaid
    graph LR
        A[MQTT /set topic] -->|command| B[Device coroutine]
        B -->|publish_state| C[MQTT /state topic]
    ```

## Command & Control Devices

Command devices receive MQTT commands and publish state back. The `@app.command`
decorator is the **recommended** approach — it registers a simple handler function
that the framework calls on each inbound message.

```python
@app.command("blind")  # (1)!
async def handle_blind(
    payload: str, ctx: cosalette.DeviceContext  # (2)!
) -> dict[str, object]:  # (3)!
    driver = ctx.adapter(VeluxPort)
    position = int(payload)
    await driver.set_position(position)
    return {"position": position}  # (4)!
```

1. `@app.command` registers a handler for `{prefix}/blind/set` messages.
2. `payload` is optional and injected by name from the MQTT message; `ctx` is injected by type annotation. Declare only what you need.
3. Returning a `dict` auto-publishes to `{prefix}/blind/state`.
4. No closure, no main loop, no `nonlocal` — just a function.

### When to Use `@app.device` Instead

For devices that need a **long-running coroutine** — periodic hardware polling,
custom event loops, state machines, or combined command + telemetry behaviour —
use `@app.device` with `@ctx.on_command`:

```python
@app.device("blind")  # (1)!
async def blind(ctx: cosalette.DeviceContext) -> None:
    driver = ctx.adapter(VeluxPort)

    @ctx.on_command  # (2)!
    async def handle(topic: str, payload: str) -> None:
        position = int(payload)
        await driver.set_position(position)
        await ctx.publish_state({"position": position})

    while not ctx.shutdown_requested:  # (3)!
        status = await driver.poll_status()
        await ctx.publish_state(status)
        await ctx.sleep(30)
```

1. `@app.device` registers the function as a long-running coroutine.
2. `@ctx.on_command` registers a handler for `{prefix}/blind/set` messages.
3. The `while` loop and periodic polling is the reason to use `@app.device`
   here — `@app.command` cannot do this.

!!! info "Coroutine ownership"
    The framework creates one `asyncio.Task` per `@app.device`. Your coroutine runs
    concurrently alongside other devices. When shutdown is signalled, the
    framework cancels the task after the current iteration completes.

### Command Routing

When a message arrives on `{prefix}/blind/set`, the framework's
`TopicRouter` extracts the device name and dispatches the payload to the
registered handler — whether it was registered via `@app.command` or
`@ctx.on_command`. See [MQTT Topics](mqtt-topics.md) for the full topic layout.

## Telemetry Devices

A telemetry device is a **simple function** that reads a sensor and returns
a dict. The framework handles the polling schedule and MQTT publication.

The simplest form takes zero arguments:

```python
@app.telemetry("temperature", interval=60)  # (1)!
async def temperature() -> dict[str, object]:
    reading = await read_i2c_sensor()  # (2)!
    return {"celsius": reading.temp, "humidity": reading.rh}  # (3)!
```

1. Framework calls this function every 60 seconds.
2. Your code reads the hardware (or adapter).
3. The returned dict is JSON-serialised and published to `{prefix}/temperature/state`
   as a retained QoS 1 message.

When you need infrastructure access (adapters, settings, MQTT publishing), declare
a `ctx: DeviceContext` parameter and the framework injects it:

```python
@app.telemetry("temperature", interval=60)
async def temperature(ctx: cosalette.DeviceContext) -> dict[str, object]:
    sensor = ctx.adapter(SensorPort)
    return {"celsius": sensor.read_temp()}
```

### Telemetry Internals

Under the hood, `@app.telemetry` is syntactic sugar for a polling loop inside
the framework:

```python
# Simplified framework internals (not user code)
async def _run_telemetry(reg, ctx, error_publisher):
    last_published = None
    last_error_type = None
    while not ctx.shutdown_requested:
        try:
            result = await reg.func(ctx)
            if result is None:
                await ctx.sleep(reg.interval)
                continue
            strategy = reg.publish_strategy
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
                last_error_type = None  # Recovery
        except asyncio.CancelledError:
            raise  # Let shutdown cancellation propagate
        except Exception as exc:
            if type(exc) is not last_error_type:
                await error_publisher.publish(exc, device=reg.name)
            last_error_type = type(exc)
        await ctx.sleep(reg.interval)
```

The framework wraps each telemetry call in error isolation with **state-transition
deduplication** — the first error of each type is published, but repeated same-type
errors are suppressed to prevent flooding. When the sensor recovers, the framework
logs recovery and restores the device health status.

### Publish Strategies

By default, the framework publishes every probe result. **Publish strategies**
decouple the probing frequency from the publishing frequency — probe often,
publish selectively:

```python
from cosalette import Every, OnChange

@app.telemetry("temperature", interval=10, publish=Every(seconds=300))
async def temperature() -> dict[str, object]:
    return {"celsius": await read_sensor()}
```

Here, `interval=10` means the sensor is **probed** every 10 seconds, but
`Every(seconds=300)` ensures state is **published** at most once every 5
minutes. This is useful when you want responsive readings locally (e.g. for
EWMA smoothing) but don't need to flood MQTT.

#### Available Strategies

| Strategy             | Publishes when…                                 |
| -------------------- | ------------------------------------------------ |
| `Every(seconds=N)`   | At least *N* seconds have elapsed since last pub |
| `Every(n=N)`         | Every *N*-th probe result                        |
| `OnChange()`         | The payload differs from the last published one  |

#### Composing Strategies

Strategies combine with `|` (OR — publish if **any** says yes) and `&`
(AND — publish only if **all** agree):

```python
# Publish on change OR every 5 minutes (whichever comes first)
@app.telemetry("temp", interval=10, publish=OnChange() | Every(seconds=300))

# Publish only when changed AND at least 30 seconds have passed
@app.telemetry("temp", interval=10, publish=OnChange() & Every(seconds=30))
```

#### Returning None

Handlers can return `None` to suppress a single cycle independently of the
strategy. This is useful for conditional reads:

```python
@app.telemetry("counter", interval=5, publish=OnChange())
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object] | None:
    meter = ctx.adapter(GasMeterPort)
    if not meter.is_ready():
        return None  # Skip this cycle entirely
    return {"impulses": meter.read_impulses()}
```

!!! note "Filters vs Strategies"
    **Strategies** (framework-level) control *when* to publish.
    **Filters** (handler-level, e.g. EWMA smoothing) control *what* to publish.
    They compose naturally — your handler applies filters, the framework
    applies strategies.

## Manual Telemetry Escape Hatch

Some sensors require complex polling logic — backoff, adaptive intervals,
or multi-step reads. For these cases, use `@app.device` with a manual loop:

```python
@app.device("complex_sensor")
async def complex_sensor(ctx: cosalette.DeviceContext) -> None:
    adapter = ctx.adapter(SensorPort)
    interval = 10.0

    while not ctx.shutdown_requested:
        try:
            data = await adapter.read()
            await ctx.publish_state(data)
            interval = 10.0  # reset on success
        except SensorTimeoutError:
            interval = min(interval * 2, 300)  # exponential backoff
        await ctx.sleep(interval)
```

!!! tip "When to use which"
    Use `@app.telemetry` for straightforward read-and-return sensors.
    Use `@app.device` when you need custom error handling, adaptive intervals,
    or inbound command support alongside telemetry.

## When to Use Which

Use this decision matrix to choose the right decorator:

| Need                                        | Decorator                    |
| ------------------------------------------- | ---------------------------- |
| React to MQTT commands, publish state        | `@app.command` ✓             |
| Poll a sensor on a fixed interval            | `@app.telemetry` ✓           |
| Poll often, publish selectively              | `@app.telemetry` + `publish=` ✓ |
| Suppress duplicate readings                  | `@app.telemetry` + `OnChange()` ✓ |
| Command + periodic hardware polling          | `@app.device` (needs loop)   |
| Custom event loop or state machine           | `@app.device` (escape hatch) |
| Adaptive intervals or backoff                | `@app.device` (manual loop)  |

`@app.command` and `@app.telemetry` are the **recommended** decorators for the
vast majority of devices. With publish strategies, `@app.telemetry` now covers
use cases that previously required `@app.device` — like polling frequently but
publishing only on change. Use `@app.device` only when you need capabilities
that the simpler decorators cannot provide (adaptive intervals, state machines,
or combined command + telemetry behaviour).

## Mixed Applications

Most real bridges combine multiple archetypes:

```python
app = cosalette.App(name="home2mqtt", version="1.0.0")

@app.command("relay")
async def handle_relay(
    payload: str, ctx: cosalette.DeviceContext
) -> dict[str, object]:
    """Bidirectional: accepts on/off commands, returns state."""
    ...

@app.telemetry("outdoor_temp", interval=120)
async def outdoor_temp() -> dict[str, object]:
    """Unidirectional: reads a BLE thermometer every 2 minutes."""
    ...

@app.telemetry("indoor_temp", interval=60)
async def indoor_temp(ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Unidirectional: reads an I²C sensor every minute (uses ctx for adapter)."""
    ...

app.run()
```

## Error Isolation

Each device runs in its own `asyncio.Task` with independent error boundaries.
A crash in one device does **not** take down others:

- **Command (`@app.command`)**: if the handler raises, the error is logged and
  published to the error topic. Subsequent commands are dispatched normally.
- **Device (`@app.device`)**: if the coroutine raises, the error is logged and
  published to the device's error topic. Other devices continue running.
- **Telemetry**: if one polling cycle raises, the error is published and the
  next cycle runs on schedule.

This isolation is fundamental to daemon reliability — a flaky BLE sensor
should never prevent an actuator motor from responding to commands.

!!! warning "CancelledError is special"
    `asyncio.CancelledError` is *not* caught by the error isolation layer.
    It propagates normally to allow graceful shutdown via task cancellation.

## Naming Constraints

Device names must be unique across **all three** registries (`@app.command`,
`@app.telemetry`, `@app.device`). Registering any device with a name
already used by another raises `ValueError` at import time:

```python
@app.command("sensor")
async def handle_sensor(payload, ctx): ...

@app.telemetry("sensor", interval=10)  # ValueError: Device name 'sensor' is already registered
async def sensor_data(ctx): ...
```

This constraint exists because device names are used as MQTT topic segments
(`{prefix}/{name}/state`) and must be unambiguous.

### Root Devices (Unnamed)

When `name` is omitted, the device publishes to root-level topics —
`{prefix}/state` instead of `{prefix}/{device}/state`. This is ideal
for single-device apps where a device segment would be redundant:

```python
# Named device — publishes to weather2mqtt/sensor/state
@app.telemetry("sensor", interval=30)
async def sensor() -> dict[str, object]: ...

# Root device — publishes to weather2mqtt/state
@app.telemetry(interval=30)
async def sensor() -> dict[str, object]: ...
```

At most **one** root device is allowed per app. Mixing root and named
devices is supported but discouraged — the framework logs a warning.

---

## See Also

- [Architecture](architecture.md) — composition root and registration API
- [MQTT Topics](mqtt-topics.md) — topic layout for state, commands, and errors
- [Error Handling](error-handling.md) — structured error payloads per device
- [Lifecycle](lifecycle.md) — when devices start, run, and stop
- [Testing](testing.md) — testing device functions with `DeviceContext` fixtures
- [ADR-010 — Device Archetypes](../adr/ADR-010-device-archetypes.md)
- [ADR-013 — Telemetry Publish Strategies](../adr/ADR-013-telemetry-publish-strategies.md)
