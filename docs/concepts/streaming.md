---
icon: material/transit-connection-variant
---

# Streaming

Streaming is the **push-to-pull bridge** for hardware devices that deliver data
via callbacks rather than waiting to be polled. Examples: BLE characteristic
notifications, serial port events, HID input reports, USB bulk transfers.

Unlike [`@app.telemetry`](../reference/api.md#telemetry) — which owns a poll
loop and publishes on a schedule — streaming adapters receive items whenever the
hardware fires them. The framework provides two primitives to bridge this
callback-based world into idiomatic `async for` iteration:

- **`StreamablePort[T_co]`** — the port Protocol that hardware adapters implement
- **`Stream[T]`** — the async iterator that converts push callbacks into pull iteration

As established in [ADR-042](../adr/ADR-042-streaming-protocol-streamableport-and-stream-t.md), these
primitives live at the hexagonal boundary (ADR-006): the adapter layer
implements the port contract; the domain handler iterates a `Stream`.

## The StreamablePort Protocol

All streamable hardware adapters implement `StreamablePort[T_co]`:

```python
class StreamablePort[T_co](Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def start_scan(self) -> None: ...
    def stop_scan(self) -> None: ...
    def register_callback(self, cb: Callable[[T_co], None]) -> None: ...
```

The five methods define a hardware lifecycle: connect, optionally begin a
scan phase, register one or more callbacks to receive items, stop scanning,
and disconnect. `T_co` is covariant — a `StreamablePort[Sensor]` satisfies
`StreamablePort[BaseSensor]`.

## Stream[T] — the async bridge

`Stream[T]` converts sync callbacks into an `AsyncIterator[T]`:

| Method | Role |
|--------|------|
| `put(item)` | Push end — called by the hardware callback (sync, never blocks) |
| `shutdown()` | Signal the iterator to stop (idempotent) |
| `async for item in stream` | Pull end — consumes items as they arrive |

`__anext__` races `queue.get()` against a shutdown `asyncio.Event` using
`asyncio.wait(FIRST_COMPLETED)`. There is no timeout polling: shutdown latency
is zero, and idle iteration blocks cleanly on the queue.

## Typical pattern

```python
import cosalette
from cosalette import Stream, StreamablePort

app = cosalette.App(name="sensor-bridge", version="1.0.0")


@app.device("ble-sensor")
async def ble_handler(
    ctx: cosalette.DeviceContext,
    port: BlePort,          # implements StreamablePort[SensorReading]
) -> None:
    stream: Stream[SensorReading] = Stream()
    port.register_callback(stream.put)
    port.open()
    port.start_scan()
    try:
        async for reading in stream:
            if ctx.shutdown_requested:
                stream.shutdown()
                continue
            await ctx.publish_state({"reading": reading})
    finally:
        port.stop_scan()
        port.close()
```

## Push vs pull

| | Pull (`@app.telemetry`) | Push (streaming) |
|---|---|---|
| Data source | Polled on a schedule | Fires on hardware events |
| Timing control | Framework owns the interval | Hardware owns the schedule |
| MQTT integration | `@app.telemetry` decorator | Manual via `ctx.publish_state()` |
| Shutdown | `ctx.shutdown_requested` | `stream.shutdown()` |

## When to use `@app.stream`

`@app.stream` is the managed-lifecycle alternative to the manual `@app.device`
pattern above. The framework owns the port lifecycle; the handler receives only
a `Stream[T]` iterator:

```python
import cosalette
from cosalette import Stream

app = cosalette.App(name="sensor-bridge", version="1.0.0")


@app.stream("ble-sensor")
async def ble_handler(stream: Stream[SensorReading]) -> None:
    async for reading in stream:
        await process(reading)
```

The handler must declare exactly one `Stream[T]` parameter. Declaring
`StreamablePort[T]` directly is not permitted — the framework manages the
port and injects only the stream.

Before invoking the handler, the framework:

1. Locates the registered `StreamablePort[T]` adapter.
2. Creates a `Stream[T]` instance.
3. Calls `port.open()`, `port.register_callback(stream.put)`, and
   `port.start_scan()`.

On shutdown, after the handler exits, the framework calls `stream.shutdown()`,
then `port.stop_scan()` and `port.close()`.

### Manual wiring vs `@app.stream`

| | `@app.device` (manual) | `@app.stream` (managed) |
|---|---|---|
| Port lifecycle | Handler calls `open()` / `close()` | Framework manages |
| Shutdown signal | `stream.shutdown()` in your loop | Framework signals before cleanup |
| What handler receives | `DeviceContext` + port | `Stream[T]` only |
| Best for | Custom error handling, multiple ports,<br>inbound MQTT commands | Single-stream, callback-only devices |

Use `@app.device` with manual wiring when you need fine-grained port error
handling, multiple concurrent streams, or combined stream + MQTT command
support. Use `@app.stream` when a single callback stream is all the device
needs.

See the [Using @app.stream](../guides/stream-adapters.md) guide for step-by-step
setup and testing patterns.
