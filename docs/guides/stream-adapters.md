---
icon: material/broadcast
---

# Using @app.stream

`@app.stream` eliminates the boilerplate of opening a port, wiring a callback,
and tearing everything down on shutdown. Register a `StreamablePort[T]` (or
`AsyncStreamablePort[T]`) adapter once, write a handler that iterates a
`Stream[T]`, and the framework handles lifecycle, DI, and persistence.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md) and read the
    [Streaming concepts](../concepts/streaming.md) page.

## When to use `@app.stream`

| Need | Primitive |
|------|-----------|
| Callback-based hardware (BLE, serial, HID) | **`@app.stream`** |
| Poll a sensor on a fixed interval | `@app.telemetry` |
| Full port control, multiple streams, or inbound MQTT commands | `@app.device` |

`@app.stream` has no built-in MQTT publish schedule. Publishing inside the
handler is your responsibility via the injected `DeviceContext`. If you want
automatic periodic state publication on a schedule, use `@app.telemetry`
instead.

## Step 1 — Define a port adapter

### Synchronous lifecycle

Implement `StreamablePort[T]` when your adapter's open/close/scan methods are
synchronous:

```python title="myapp/ports.py"
from collections.abc import Callable

from cosalette import StreamablePort


class ScannerPort(StreamablePort["Barcode"]):
    """USB HID barcode scanner."""

    def open(self) -> None: ...
    def close(self) -> None: ...
    def start_scan(self) -> None: ...
    def stop_scan(self) -> None: ...
    def register_callback(self, cb: Callable[["Barcode"], None]) -> None: ...
```

The five methods map to the hardware lifecycle: connect, begin a scan phase,
receive items via registered callbacks, stop scanning, and disconnect. See
[Streaming concepts](../concepts/streaming.md#the-streamableport-protocol) for
the full protocol definition and covariance rules.

### Async lifecycle

When your adapter needs awaitable open/close/scan — for example, a BLE stack
whose connect procedure is asynchronous — implement `AsyncStreamablePort[T]`
instead:

```python title="myapp/ports.py"
from collections.abc import Callable

from cosalette import AsyncStreamablePort


class BlePort(AsyncStreamablePort["SensorReading"]):
    """BLE sensor with async lifecycle."""

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def start_scan(self) -> None: ...
    async def stop_scan(self) -> None: ...
    def register_callback(self, cb: Callable[["SensorReading"], None]) -> None: ...
```

`register_callback` stays synchronous in both protocols — hardware callbacks
fire synchronously; `Stream` handles the async boundary.

## Step 2 — Register the adapter

Call `app.adapter()` using the **port protocol** as the key:

```python title="app.py"
import cosalette
from cosalette import AsyncStreamablePort, StreamablePort

from myapp.adapters import UsbScannerAdapter, BleAdapter
from myapp.models import Barcode, SensorReading

app = cosalette.App(name="scanner-bridge", version="1.0.0")

# Sync adapter
app.adapter(StreamablePort[Barcode], lambda: UsbScannerAdapter(device="/dev/hidraw0"))

# Async adapter
app.adapter(AsyncStreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))
```

The framework matches the `Stream[T]` parameter in the handler to the registered
port by item type at startup. Registering both `StreamablePort[T]` and
`AsyncStreamablePort[T]` for the same `T` raises a `RuntimeError` before the app
runs.

## Step 3 — Write the handler

### Stateless handler

Declare a single `Stream[T]` parameter and iterate:

```python title="app.py"
from cosalette import Stream

from myapp.models import Barcode


@app.stream("barcode-scanner")  # (1)!
async def handle_scans(stream: Stream[Barcode]):
    async for barcode in stream:  # (2)!
        await process_barcode(barcode)  # (3)!
        yield  # (4)!
```

1. The name string is optional. When omitted, the function name is used.
2. `async for` blocks until the next item arrives or shutdown is signalled.
   The framework signals shutdown by calling `stream.shutdown()`, which causes
   the iterator to stop immediately; items still in the queue may be discarded.
3. Your domain logic. Publish to MQTT, write to a database, forward downstream.
4. `yield` marks the **reaction boundary**. Place it _after_ processing each stream
   item. If any `@app.react` reactors are registered for mutated state, the framework
   drains events and runs them before the next `async for` iteration. Omitting `yield`
   batches all items before reactor dispatch — use this only when accumulating state
   across items is the intended behaviour.

### Stateful handler — DeviceContext and DeviceStore

Declare `DeviceContext` to publish MQTT messages and `DeviceStore` to persist
state across restarts:

```python title="app.py"
from collections.abc import AsyncIterator

from cosalette import AsyncStreamablePort, DeviceContext, DeviceStore, Stream

from myapp.models import SensorReading


app = cosalette.App(name="sensor-bridge", version="1.0.0", store=store_backend)  # (1)!
app.adapter(AsyncStreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))


@app.stream("ble-sensor")
async def handle_readings(
    stream: Stream[SensorReading],
    ctx: DeviceContext,   # (2)!
    store: DeviceStore,   # (3)!
) -> AsyncIterator[None]:
    registry.restore_from(store)  # (4)!

    async for reading in stream:
        result = registry.record(reading)
        if result.is_new:
            await ctx.publish_state({  # (5)!
                "sensor": result.name,
                "value": reading.value,
            })
        store["last_seen"] = reading.sensor_id  # (6)!
        yield  # reaction boundary
```

1. `store=` is required to use `DeviceStore` in handlers. Without it, declaring
   `DeviceStore` causes a `TypeError` when the handler starts — the production
   stream runner logs the error and exits the task; `AppHarness.inject_stream`
   raises it directly.
2. `DeviceContext` is always available. Use it to call `ctx.publish_state()`,
   `ctx.publish()`, and `ctx.sleep()`. To publish availability directly, use
   `ctx.publish("availability", "online", retain=True)`.
3. `DeviceStore` is a `MutableMapping[str, Any]` scoped to this stream by name.
   Load it to restore persisted values; write to it to persist new state.
4. Restore domain state before the first reading arrives. This is the idiomatic
   pattern for stateful receivers that survive application restarts.
5. Publish arbitrary state to the stream's MQTT topic. The framework manages
   topic construction using the stream name.
6. Mark the store dirty. The framework saves the store to the backend on graceful
   shutdown.

### Concrete adapter injection

When your adapter has device-specific methods beyond the port lifecycle — for
example a `set_led()` call — declare the concrete type alongside `Stream[T]`:

```python title="app.py"
from myapp.ports import SerialPort  # implements AsyncStreamablePort[Frame]


@app.stream("serial-receiver")
async def handle_frames(stream: Stream[Frame], port: SerialPort):
    async for frame in stream:
        await process(frame)
        port.set_led(True)   # non-lifecycle method — safe to call
        yield
```

!!! warning "Never call lifecycle methods on the injected adapter"
    Do not call `port.open()`, `port.close()`, `port.start_scan()`, or
    `port.stop_scan()` on the injected concrete instance. The framework owns
    the port lifecycle and has already called these; calling them again causes
    double-open errors or leaves the port in an inconsistent state.

### What the framework manages

Before calling the handler the framework:

1. Locates the registered `StreamablePort[T]` or `AsyncStreamablePort[T]` adapter.
2. Creates a `Stream[T]` instance.
3. Opens the port: calls `port.open()`, `port.register_callback(stream.put)`,
   and `port.start_scan()`. Async ports are awaited at each step.
4. Injects `DeviceContext`, `DeviceStore` (if configured), and the concrete
   adapter instance into the provider map.

On shutdown, after the handler exits:

1. Calls `stream.shutdown()` to send an immediate stop signal; any items
   still queued may be discarded.
2. Calls `port.stop_scan()` and `port.close()` (awaited for async ports).
3. Saves the `DeviceStore` to the backend.

Do not declare `StreamablePort[T]` or `AsyncStreamablePort[T]` as handler
parameters — the framework manages them. `Settings` subclasses, `@app.state`
instances, and `ClockPort` may be declared alongside `Stream[T]` as usual.


## Step 4 — Test with `inject_stream`

`AppHarness.inject_stream` feeds items directly into the handler's stream,
bypassing the hardware adapter entirely. `DeviceContext`, `DeviceStore`,
`Settings`, concrete adapters, and `ClockPort` are resolved through the same
provider map as production execution. To supply `@app.state` dependencies, call
`harness.override_state()` before `inject_stream`, or pass them via `providers=`.

### Basic usage

```python title="tests/test_scanner.py"
import pytest
from cosalette import Stream, StreamablePort
from cosalette.testing import AppHarness

from myapp.adapters import UsbScannerAdapter
from myapp.models import Barcode


@pytest.mark.asyncio
async def test_barcode_processed() -> None:
    harness = AppHarness.create()  # (1)!
    harness.app.adapter(StreamablePort[Barcode], lambda: UsbScannerAdapter(device="/dev/hidraw0"))

    captured: list[Barcode] = []

    @harness.app.stream("barcode-scanner")
    async def handle_scans(stream: Stream[Barcode]):
        async for barcode in stream:
            captured.append(barcode)
            yield

    barcode = Barcode(code="12345678", symbology="EAN-8")
    await harness.inject_stream("barcode-scanner", barcode)  # (2)!

    assert captured == [barcode]
```

1. `AppHarness.create()` builds a fresh `App` with test doubles. Register
   adapters and handlers on `harness.app` — the harness does not accept an
   existing app.
2. `inject_stream(name, *items)` delivers each item to the handler's stream in
   order, then signals shutdown so the `async for` loop exits cleanly. Pass
   `shutdown=False` to keep the stream open for multi-batch tests.

### Testing stateful handlers with DeviceContext and DeviceStore

For handlers that inject `DeviceContext` or `DeviceStore`, register the handler
on `harness.app` and assert via `harness.mqtt` or the store backend:

```python title="tests/test_sensor_receiver.py"
import pytest
from collections.abc import AsyncIterator

from cosalette import AsyncStreamablePort, DeviceContext, DeviceStore, MemoryStore, Stream
from cosalette.testing import AppHarness

from myapp.adapters import BleAdapter
from myapp.models import SensorReading


@pytest.mark.asyncio
async def test_publishes_new_sensor() -> None:
    harness = AppHarness.create(name="sensor-bridge")
    harness.app.adapter(AsyncStreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))

    @harness.app.stream("ble-sensor")
    async def handle_readings(stream: Stream[SensorReading], ctx: DeviceContext) -> AsyncIterator[None]:
        async for reading in stream:
            await ctx.publish_state({"sensor_id": reading.sensor_id, "value": reading.value})
            yield

    reading = SensorReading(sensor_id=17, value=22.4)
    await harness.inject_stream("ble-sensor", reading)  # (1)!

    published = harness.mqtt.get_messages_for("sensor-bridge/ble-sensor/state")
    assert len(published) == 1
    import json
    assert json.loads(published[0][0])["sensor_id"] == 17


@pytest.mark.asyncio
async def test_restores_registry_from_store() -> None:
    mem_store = MemoryStore({"ble-sensor": {"last_seen": 42}})  # (2)!
    harness = AppHarness.create(name="sensor-bridge", store=mem_store)  # (3)!
    harness.app.adapter(AsyncStreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))

    @harness.app.stream("ble-sensor")
    async def handle_readings(stream: Stream[SensorReading], store: DeviceStore) -> AsyncIterator[None]:
        async for reading in stream:
            store["last_seen"] = reading.sensor_id
            yield

    reading = SensorReading(sensor_id=42, value=18.0)
    await harness.inject_stream("ble-sensor", reading)

    # Verify the store was updated after the handler ran
    saved = mem_store.load("ble-sensor")
    assert saved is not None
    assert saved["last_seen"] == 42
```

1. `inject_stream` auto-wires a `DeviceContext` from `harness.mqtt` and
   `harness.clock`. All publishes are captured in `harness.mqtt.published`.
2. Seed the store with pre-existing state to test restore behaviour.
3. `AppHarness.create(store=...)` sets the store backend. `inject_stream`
   auto-creates a `DeviceStore` keyed by handler name, loads it before the
   handler runs, and saves it on exit.

### Full signature

```python
await harness.inject_stream(
    name,           # stream handler name
    *items,         # items to deliver into the stream
    shutdown=True,  # signal shutdown after items are delivered
    ctx=None,       # DeviceContext override (replaces harness default)
    store=None,     # Store backend override (replaces app._store)
    providers=None, # extra DI providers merged last (highest precedence)
    adapters=None,  # concrete adapters injected under their own type
)
```

| Parameter | Default | Effect |
|-----------|---------|--------|
| `shutdown` | `True` | Auto-signal stream shutdown after delivery |
| `ctx` | harness default | Replace the entire `DeviceContext`; harness doubles not merged in |
| `store` | `app._store` | Override the store backend for this call |
| `providers` | `{}` | Merged last — highest-precedence DI overrides |
| `adapters` | `{}` | Concrete adapter instances injected by their type |

!!! note "Lifecycle is still bypassed"
    `inject_stream` never calls `port.open()`, `port.start_scan()`,
    `port.stop_scan()`, or `port.close()`. Hardware adapters registered with
    the app are not instantiated. Only the stream items and DI providers you
    pass are available to the handler.

## Conditional registration with `enabled=`

`enabled=` follows the same rules as all other cosalette decorators:

```python
# Skip at decoration time
@app.stream("scanner", enabled=False)
async def handle_scans(stream: Stream[Barcode]):
    async for barcode in stream:
        ...
        yield


# Defer the decision to bootstrap — settings are resolved first
@app.stream(
    "scanner",
    enabled=lambda s: s.scanner_enabled,
)
async def handle_scans(stream: Stream[Barcode]):
    async for barcode in stream:
        ...
        yield
```

A callable receives the resolved `Settings` instance. When it returns `False`
the handler is silently skipped — no adapter is opened, no task is spawned.

See
[ADR-038](../adr/ADR-038-deferred-enabled-for-decorator-registrations.md)
for the deferred `enabled=` design rationale.

## Complete example

=== "Stateless"

    ```python title="app.py"
    from __future__ import annotations

    import cosalette
    from cosalette import Stream, StreamablePort

    from myapp.adapters import UsbScannerAdapter
    from myapp.models import Barcode

    app = cosalette.App(name="scanner-bridge", version="1.0.0")

    app.adapter(StreamablePort[Barcode], lambda: UsbScannerAdapter(device="/dev/hidraw0"))


    @app.stream("barcode-scanner")
    async def handle_scans(stream: Stream[Barcode]):
        async for barcode in stream:
            await process_barcode(barcode)
            yield


    app.run()
    ```

=== "Stateful"

    ```python title="app.py"
    from __future__ import annotations

    from collections.abc import AsyncIterator

    import cosalette
    from cosalette import AsyncStreamablePort, DeviceContext, DeviceStore, Stream

    from myapp.adapters import BleAdapter
    from myapp.models import SensorReading

    app = cosalette.App(name="sensor-bridge", version="1.0.0", store=store_backend)

    app.adapter(AsyncStreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))


    @app.stream("ble-sensor")
    async def handle_readings(
        stream: Stream[SensorReading],
        ctx: DeviceContext,
        store: DeviceStore,
    ) -> AsyncIterator[None]:
        registry.restore_from(store)

        async for reading in stream:
            result = registry.record(reading)
            if result.is_new:
                await ctx.publish_state({"sensor": result.name, "value": reading.value})
            store["last_seen"] = reading.sensor_id
            yield  # reaction boundary


    app.run()
    ```

## See also

- [Streaming concepts](../concepts/streaming.md) — `StreamablePort`, `AsyncStreamablePort`, `Stream[T]`,
  and the push-to-pull bridge
- [Device archetypes](../concepts/device-archetypes.md) — choosing the right
  decorator
- [Testing](../guides/testing.md) — full harness reference
- [ADR-042 — Streaming protocol](../adr/ADR-042-streaming-protocol-streamableport-and-stream-t.md)
- [ADR-045 — Stateful stream receiver semantics](../adr/ADR-045-stateful-stream-receiver-semantics.md)
