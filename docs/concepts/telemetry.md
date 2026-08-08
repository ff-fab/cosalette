---
icon: material/chart-line
---

# Telemetry Devices

`@app.telemetry` is the **recommended** decorator for devices that read a sensor on
a recurring schedule and publish the result to MQTT. It owns the polling loop, error
isolation, and publication strategy — your handler just reads and returns a `dict`.

## Handler anatomy

The simplest telemetry handler takes zero arguments:

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

Telemetry devices are normally poll-only, but adding `triggerable=True` makes them
also respond to inbound MQTT commands — see the
[Triggerable Telemetry](../guides/telemetry-device.md#triggerable-telemetry) guide.

## How the framework runs telemetry

Under the hood, `@app.telemetry` is syntactic sugar for a polling loop inside the
framework:

```python
# Simplified TelemetryRunner.run_telemetry (see _telemetry_runner.py)
async def run_telemetry(self, reg, ctx, error_publisher):
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

## Publish strategies

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

For threshold modes, composition operators, and the full strategy reference, see
[Publish Strategies](publish-strategies.md).

## Coalescing groups

When multiple telemetry handlers share a physical resource — such as a serial bus,
SPI interface, or rate-limited API — they can be grouped into a shared execution
window using the `group=` parameter:

```python
@app.telemetry("outdoor", interval=300, group="optolink")
async def outdoor(port: OptolinkPort) -> dict[str, object]:
    return await port.read_signals(["outdoor_temp"])

@app.telemetry("hotwater", interval=300, group="optolink")
async def hotwater(port: OptolinkPort) -> dict[str, object]:
    return await port.read_signals(["hot_water_temp"])
```

Handlers in the same group are managed by a shared **tick-aligned scheduler**. At
t=0 all grouped handlers fire together; at subsequent ticks only those whose interval
divides evenly into the elapsed time fire. This reduces resource sessions from N
(one per handler) down to 1 per coinciding tick, eliminates timing drift, and enables
adapter session sharing.

Each handler retains its own publish strategy, error isolation, persistence policy,
and init function — `group=` is purely an execution scheduling hint.

See [ADR-018](../adr/ADR-018-coalescing-groups.md) for the full design rationale.

## Deferred registration

Sometimes a device should only be registered when the app's settings dictate it.
All three decorator forms (`@app.telemetry`, `@app.device`, `@app.command`) accept
`enabled=` as a **callable** that receives the resolved `Settings` instance and
returns a `bool`:

```python
@app.telemetry(
    "magnetometer",
    interval=lambda s: s.poll_interval,
    enabled=lambda s: s.enable_debug_device,  # resolved at bootstrap
)
async def magnetometer(mag: MagnetometerPort) -> dict[str, object]:
    reading = mag.read()
    return {"bx": reading.bx, "by": reading.by, "bz": reading.bz}
```

When the callable returns `False`, the device is silently dropped from the registry
before MQTT wiring begins. Both `interval=` and `enabled=` support deferred
resolution — all callables receive the same `Settings` instance at bootstrap time.

This preserves the **fully-declarative `main.py`** style: every device is visible
at module level, and no `@app.on_configure` boilerplate is needed just to
conditionally register one device.

!!! note "Imperative add_*() methods"
    `app.add_telemetry()`, `app.add_device()`, and `app.add_command()` only accept
    `enabled: bool`. Inside `@app.on_configure`, settings are already available,
    so a callable is unnecessary.

See [ADR-038](../adr/ADR-038-deferred-enabled-for-decorator-registrations.md) and
[ADR-020](../adr/ADR-020-deferred-interval-resolution.md) for the design records.

## See also

- [Device Archetypes](device-archetypes.md) — comparison hub and decision tree
- [Publish Strategies](publish-strategies.md) — probing/publishing frequency control
- [Telemetry Device guide](../guides/telemetry-device.md) — triggerable, init, and more
- [ADR-010](../adr/ADR-010-device-archetypes.md) — device archetype decision record
- [ADR-013](../adr/ADR-013-telemetry-publish-strategies.md) — publish strategies design
- [ADR-018](../adr/ADR-018-coalescing-groups.md) — coalescing groups design
- [ADR-020](../adr/ADR-020-deferred-interval-resolution.md) — deferred interval resolution
- [ADR-038](../adr/ADR-038-deferred-enabled-for-decorator-registrations.md) — deferred enabled=
