---
icon: material/transit-connection-variant
---

# Streaming

Streaming is the **push-to-pull bridge** for hardware devices that deliver data
via callbacks rather than waiting to be polled. Examples: BLE characteristic
notifications, serial port events, HID input reports, USB bulk transfers.

Unlike [`@app.telemetry`](../reference/api.md#telemetry) — which owns a poll
loop and publishes on a schedule — streaming adapters receive items whenever the
hardware fires them. The framework provides three primitives to bridge this
callback-based world into idiomatic `async for` iteration:

- **`StreamablePort[T_co]`** — sync port Protocol for adapters with synchronous lifecycle
- **`AsyncStreamablePort[T_co]`** — async port Protocol for adapters that require awaitable open/close/scan
- **`Stream[T]`** — the async iterator that converts push callbacks into pull iteration

As established in [ADR-042](../adr/ADR-042-streaming-protocol-streamableport-and-stream-t.md) and
extended in [ADR-045](../adr/ADR-045-stateful-stream-receiver-semantics.md), these
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

## AsyncStreamablePort — async lifecycle variant

`AsyncStreamablePort[T_co]` mirrors `StreamablePort[T_co]` but declares its
lifecycle methods as `async`:

```python
class AsyncStreamablePort[T_co](Protocol):
    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def start_scan(self) -> None: ...
    async def stop_scan(self) -> None: ...
    def register_callback(self, cb: Callable[[T_co], None]) -> None: ...
```

`register_callback` remains synchronous — hardware callbacks are always sync;
the `Stream` bridge handles the async boundary.

Use `AsyncStreamablePort[T_co]` when your adapter requires awaitable I/O for
its lifecycle — for example, a BLE stack whose `open()` method must be awaited,
or a serial port that performs `async with` resource acquisition on connect.

| Protocol | Use when |
|----------|----------|
| `StreamablePort[T]` | `open()`, `close()`, `scan()` are synchronous |
| `AsyncStreamablePort[T]` | `open()`, `close()`, or `scan()` must be awaited |

Register with `app.adapter()` using the matching protocol as the key:

```python
# Sync adapter
app.adapter(StreamablePort[SensorReading], lambda: SerialAdapter("/dev/ttyUSB0"))

# Async adapter
app.adapter(AsyncStreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))
```

The stream runner detects which protocol the adapter satisfies at startup and
calls lifecycle methods accordingly — sync adapters are called directly; async
adapters are awaited. Registering both `StreamablePort[T]` and
`AsyncStreamablePort[T]` for the same item type is an error.

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
):
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
            yield  # reaction boundary
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
pattern above. The framework owns the port lifecycle; the handler iterates a
`Stream[T]` and may inject `DeviceContext`, `DeviceStore`, and the concrete
adapter alongside it:

```python
import cosalette
from cosalette import Stream
from cosalette._persistence._stores import DeviceStore

app = cosalette.App(name="sensor-bridge", version="1.0.0", store=store_backend)


@app.stream("ble-sensor")
async def ble_handler(
    stream: Stream[SensorReading],
    ctx: cosalette.DeviceContext,   # optional — inject to publish to MQTT
    store: DeviceStore,             # optional — inject to read/write persistent state
):
    registry.restore_from(store)
    async for reading in stream:
        result = registry.record(reading)
        if result.is_new:
            await ctx.publish_state({"sensor": result.name, "value": reading.value})
        store["last_reading"] = reading.value
        yield  # reaction boundary
```

The handler must declare exactly one `Stream[T]` parameter. Declaring
`StreamablePort[T]` or `AsyncStreamablePort[T]` directly is not permitted —
the framework manages the port and injects only the stream.

`DeviceContext` is always available for injection. `DeviceStore` requires the
app to be configured with a store backend (`App(store=...)`); without one,
declaring `DeviceStore` causes a `TypeError` when the handler starts — the
production stream runner logs the error and exits the task; `AppHarness.inject_stream`
raises it directly to the test.

Before invoking the handler, the framework:

1. Locates the registered `StreamablePort[T]` or `AsyncStreamablePort[T]` adapter.
2. Creates a `Stream[T]` instance.
3. Opens the port: calls `port.open()`, `port.register_callback(stream.put)`,
   and `port.start_scan()`. Async ports are awaited at each step.

On shutdown, after the handler exits, the framework calls `stream.shutdown()`,
then `port.stop_scan()` and `port.close()`. The store is saved before exit.

### Concrete adapter injection

The framework also exposes the concrete adapter instance under its own type so
handlers can call **non-lifecycle** methods on it directly:

```python
class SerialPort(AsyncStreamablePort[Frame]):
    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def start_scan(self) -> None: ...
    async def stop_scan(self) -> None: ...
    def register_callback(self, cb: Callable[[Frame], None]) -> None: ...
    def set_led(self, on: bool) -> None: ...  # device-specific, not a lifecycle method


app.adapter(AsyncStreamablePort[Frame], lambda: SerialPort("/dev/ttyUSB0"))


@app.stream("serial-receiver")
async def handle_frames(stream: Stream[Frame], port: SerialPort):
    async for frame in stream:
        await process(frame)
        port.set_led(True)  # OK — non-lifecycle method
        yield
```

!!! warning "Never call lifecycle methods on the injected adapter"
    The framework owns the port lifecycle exclusively. Calling `port.open()`,
    `port.close()`, `port.start_scan()`, or `port.stop_scan()` on the injected
    concrete adapter causes double-open errors or leaves the port in an
    inconsistent state. Use the concrete adapter only for device-specific
    methods that are not part of the port lifecycle.

### Manual wiring vs `@app.stream`

| | `@app.device` (manual) | `@app.stream` (managed) |
|---|---|---|
| Port lifecycle | Handler calls `open()` / `close()` | Framework manages |
| Async lifecycle | Manual `async with` or `await` calls | `AsyncStreamablePort[T]` detected automatically |
| Shutdown signal | `stream.shutdown()` in your loop | Framework signals before cleanup |
| What handler receives | `DeviceContext` + port | `Stream[T]` + optional `DeviceContext`, `DeviceStore`, concrete adapter |
| Persistent state | Manual store wiring | `DeviceStore` injection when `App(store=...)` is configured |
| Best for | Custom error handling, multiple ports,<br>inbound MQTT commands | Single-stream, callback-only devices |

Use `@app.device` with manual wiring when you need fine-grained port error
handling, multiple concurrent streams, or combined stream + MQTT command
support. Use `@app.stream` when a single callback stream is the primary data
path and you want framework-managed lifecycle, DI, and persistence.

See the [Using @app.stream](../guides/stream-adapters.md) guide for step-by-step
setup and testing patterns.
