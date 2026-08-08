---
icon: material/timer-outline
---

# Periodic Companion Tasks

`@app.periodic` registers a background coroutine that runs on a fixed interval with
**no MQTT output**. It is not a device archetype in the MQTT sense — it has no topic
ownership, no `DeviceContext`, and no publish strategy — but it frequently accompanies
device handlers as a side-effect partner.

## When to use `@app.periodic`

| Need | Primitive |
|------|-----------|
| Publish sensor data to MQTT | `@app.telemetry` |
| MQTT command + state | `@app.command` or `@app.device` |
| Side-effect task with no MQTT output | **`@app.periodic`** |

Typical uses: write-buffer flushing, watchdog pings, cache warming, LED
state synchronisation, background database sync.

## Companion pattern: flush buffer alongside telemetry

The most common `@app.periodic` use case is a telemetry handler that accumulates
readings into an in-process buffer, with a periodic task that flushes the buffer to
an upstream API on a longer cadence:

```python
@app.state
def reading_buffer() -> ReadingBuffer:
    return ReadingBuffer(capacity=100)


@app.telemetry("temperature", interval=10.0)  # (1)!
async def read_temperature(
    sensor: SensorPort,
    buf: ReadingBuffer,
) -> dict[str, object]:
    reading = await sensor.read()
    buf.append(reading)
    return {"celsius": reading.temp}


@app.periodic("upstream-flush", interval=300.0)  # (2)!
async def flush_upstream(buf: ReadingBuffer) -> None:
    if buf.pending_count() > 0:
        await buf.flush_to_api()
```

1. Telemetry publishes to MQTT every 10 s for Home Assistant / MQTT consumers.
2. The periodic task flushes accumulated readings to the upstream API every 5 minutes
   — independently of the MQTT cadence.

Both handlers share `ReadingBuffer` via `@app.state` DI. Neither owns the other's
timing. Exception isolation is independent: a flush failure logs an error and retries
next cycle; it does not affect MQTT telemetry.

## Exception isolation

Periodic tasks catch all exceptions except `asyncio.CancelledError`, log at `ERROR`
level, and continue the loop. There is no MQTT error topic for periodic tasks — unlike
`@app.telemetry` and `@app.device`, which publish structured errors to
`{prefix}/{name}/error`.

## Shutdown behaviour

Periodic tasks are cancelled during Phase 4 (Teardown) with a **5-second grace
period**. The framework waits for running handlers to complete before logging a
timeout warning if any exceed the grace period.

## See also

- [Device Archetypes](device-archetypes.md) — comparison hub and decision tree
- [Periodic Tasks guide](../guides/periodic-tasks.md) — full API and testing patterns
- [ADR-041](../adr/ADR-041-periodic-background-tasks.md) — design rationale
