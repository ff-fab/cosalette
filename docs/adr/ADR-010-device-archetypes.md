# ADR-010: Device Archetypes

## Status

Accepted **Date:** 2026-02-14

## Context

Analysis of all 8 IoT-to-MQTT bridge projects reveals two fundamental device
interaction patterns. The framework needs first-class support for both patterns and
must allow them to be mixed within a single application.

### Archetype A: Command & Control (Bidirectional)

Subscribe to MQTT command topics, translate into device actions, publish state.

| Project          | Hardware         | Library                 |
| ---------------- | ---------------- | ----------------------- |
| velux2mqtt       | GPIO relay       | RPi.GPIO                |
| wiz2mqtt         | WiFi UDP         | pywizlight              |
| concept2mqtt     | BLE              | bleak (CSAFE)           |
| wallpanel2mqtt   | LAN SSH          | asyncssh                |
| smartmeter2mqtt  | USB/IR           | pyserial/FTDI           |
| vito2mqtt        | USB/Optolink     | pyserial/vcontrold      |

**Pattern:** MQTT command → parse → device action → read state → publish

### Archetype B: Telemetry (Unidirectional)

Poll or stream sensor data, publish readings to MQTT.

| Project          | Hardware         | Library                 |
| ---------------- | ---------------- | ----------------------- |
| airthings2mqtt   | BLE              | wave-reader/bleak       |
| gas2mqtt         | I²C (HMC5883)   | smbus2                  |

**Pattern:** poll/stream → read → transform → publish

Some projects combine both (e.g., smartmeter2mqtt reads data continuously but also
accepts configuration commands).

## Decision

Use **two first-class device archetypes** — **Command & Control** via `@app.device` with
`@ctx.on_command`, and **Telemetry** via `@app.telemetry` with a configurable polling
interval — because analysis of 8 IoT projects shows these two patterns cover all
use cases, and the framework should make the common cases effortless.

### Command & Control (`@app.device`)

```python
@app.device("blind")
async def blind(ctx: DeviceContext) -> None:
    gpio = ctx.adapter(GpioPort)

    @ctx.on_command
    async def handle(payload: str) -> None:
        command = parse_command(payload)
        await execute(command, gpio, ctx)
        await ctx.publish_state({"position": get_position()})

    await ctx.publish_state({"position": None, "moving": False})
```

The framework automatically subscribes to `{app}/{device}/set` and dispatches incoming
messages to the registered command handler.

### Telemetry (`@app.telemetry`)

```python
@app.telemetry("wave-1", interval=30.0)
async def wave_sensor(ctx: DeviceContext) -> dict:
    reading = await ble_client.read_characteristic(...)
    return {"radon_bq_m3": reading.radon, "temperature": reading.temp}
```

The framework calls the function at the specified interval, publishes the returned
dict as JSON to `{app}/{device}/state`, and never subscribes to a `/set` topic.

### Manual telemetry loop

For devices that need custom polling logic (e.g., trigger detection with hysteresis),
the `@app.device` decorator also supports long-running loops:

```python
@app.device("counter")
async def counter(ctx: DeviceContext) -> None:
    while not ctx.shutdown_requested:
        bz = read_magnetometer(bus)
        if trigger_detected(bz):
            await ctx.publish_state({"count": count, "trigger": "CLOSED"})
        await ctx.sleep(1.0)
```

### Mixed archetypes

A single application can register both types — the framework manages them as
concurrent asyncio tasks.

## Decision Drivers

- Analysis of 8 real IoT projects shows exactly two interaction patterns
- The framework should make the common cases (periodic polling, command handling)
  effortless
- Complex devices (custom polling, stateful trigger detection) must remain expressible
- Telemetry-only devices should not incur command subscription overhead
- Both archetypes must be combinable within a single application

## Considered Options

### Option 1: Single generic device type

Provide only `@app.device` and let projects implement both patterns manually.

- *Advantages:* Maximum flexibility with minimal API surface. Simpler framework code.
- *Disadvantages:* Periodic telemetry requires boilerplate (loop, sleep, publish) in
  every telemetry project. Misses the opportunity to make the common case trivial.
  The `@app.telemetry` shorthand eliminates 5-10 lines of polling boilerplate per
  device.

### Option 2: Event-driven only

All devices react to events (MQTT messages, timer ticks) — no long-running device
functions.

- *Advantages:* Uniform model, easy to reason about concurrency.
- *Disadvantages:* Does not fit sensor polling patterns where the device owns the
  read loop. Event-driven timer ticks for polling add indirection without benefit.
  The gas2mqtt trigger detection (hysteresis, EWMA filtering) is naturally expressed
  as a loop, not as discrete events.

### Option 3: Two first-class archetypes (chosen)

`@app.device` for command & control (with `@ctx.on_command`) and `@app.telemetry`
for periodic polling, with `@app.device` also supporting manual loops.

- *Advantages:* Covers 100% of known use cases. `@app.telemetry` makes periodic
  polling trivial (return dict → published as state). `@app.device` with
  `@ctx.on_command` handles bidirectional devices cleanly. Manual loops remain
  possible for complex cases. Telemetry devices automatically skip `/set`
  subscription.
- *Disadvantages:* Two registration decorators to learn. The distinction between
  `@app.device` and `@app.telemetry` is syntactic sugar — they could be unified
  with parameters.

## Consequences

### Positive

- Periodic telemetry devices are trivial to implement — a single decorated function
  that returns a dict
- Command & control devices have a clear, structured pattern — `@ctx.on_command`
  registers the handler, framework manages topic subscription
- Telemetry-only devices incur no command subscription overhead
- Both archetypes can coexist in a single application as concurrent tasks
- The gas2mqtt example (telemetry) reduces to ~20 lines of application code

### Negative

- Two decorators (`@app.device`, `@app.telemetry`) to learn and choose between
- `@app.telemetry` is specific to the periodic-return-dict pattern — devices that
  conditionally publish (e.g., only on trigger) must use `@app.device` with a manual
  loop instead
- The distinction may cause confusion: when to use `@app.telemetry` vs. `@app.device`
  with a while loop

_2026-02-14_
