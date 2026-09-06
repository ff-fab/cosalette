---
icon: material/broadcast
---

# Stream Continuous Sensor Data

`@app.stream` eliminates the boilerplate of opening a port, wiring a callback,
and tearing everything down on shutdown. Register a `StreamablePort[T]`
adapter once, write a handler that iterates a `Stream[T]`, and the framework
handles lifecycle, DI, and persistence.

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

Implement `StreamablePort[T]` for your hardware adapter. All lifecycle methods
are `async`; `register_callback` is synchronous:

```python title="myapp/ports.py"
from collections.abc import Callable

from cosalette import StreamablePort


class ScannerPort(StreamablePort["Barcode"]):
    """USB HID barcode scanner."""

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def start_scan(self) -> None: ...
    async def stop_scan(self) -> None: ...
    def register_callback(self, cb: Callable[["Barcode"], None]) -> None: ...
```

The five methods map to the hardware lifecycle: connect, begin a scan phase,
receive items via registered callbacks, stop scanning, and disconnect. See
[Streaming concepts](../concepts/streaming.md#the-streamableport-protocol) for
the full protocol definition and covariance rules.

## Step 2 — Register the adapter

Call `app.adapter()` using the **port protocol** as the key:

```python title="app.py"
import cosalette
from cosalette import StreamablePort

from myapp.adapters import UsbScannerAdapter, BleAdapter
from myapp.models import Barcode, SensorReading

app = cosalette.App(name="scanner-bridge", version="1.0.0")

app.adapter(StreamablePort[Barcode], lambda: UsbScannerAdapter(device="/dev/hidraw0"))
app.adapter(StreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))
```

The framework matches the `Stream[T]` parameter in the handler to the registered
port by item type at startup.

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

For a complete example of a stateful `@app.stream` handler that injects
`DeviceContext` (for MQTT publishing) and `DeviceStore` (for persistent state across
restarts), see [Streaming concepts](../concepts/streaming.md).

`DeviceStore` requires the app to be configured with a store backend
(`App(store=...)`). Without it, declaring `DeviceStore` causes a `TypeError` when
the handler starts — the production stream runner logs the error and exits the task;
`AppHarness.inject_stream` raises it directly.

### Concrete adapter injection

When your adapter has device-specific methods beyond the port lifecycle — for
example a `set_led()` call — declare the concrete type alongside `Stream[T]`:

```python title="app.py"
from myapp.ports import SerialPort  # implements StreamablePort[Frame]


@app.stream("serial-receiver")
async def handle_frames(stream: Stream[Frame], port: SerialPort):
    async for frame in stream:
        await process(frame)
        port.set_led(True)   # non-lifecycle method — safe to call
        yield
```

!!! warning "Lifecycle methods raise `AttributeError` on the injected adapter"
    Production `run_stream()` injects a **capability-limited proxy** under the
    concrete type — not the raw adapter. Non-lifecycle attributes and methods
    forward transparently, but `open()`, `close()`, `start_scan()`, and
    `stop_scan()` raise `AttributeError` because lifecycle belongs to the
    framework.

    `AppHarness.inject_stream()` is a test-only shortcut that bypasses
    production lifecycle management; it may inject raw test instances without
    this restriction.

### What the framework manages

Before calling the handler the framework:

1. Locates the registered `StreamablePort[T]` adapter.
2. Creates a `Stream[T]` instance.
3. Opens the port: `await port.open()`, `port.register_callback(stream.put)`,
   and `await port.start_scan()`.
4. Injects `DeviceContext`, `DeviceStore` (if configured), and a
   **capability-limited proxy** under the concrete adapter type into the provider map.

On shutdown, after the handler exits:

1. Calls `stream.shutdown()` to send an immediate stop signal; any items
   still queued may be discarded.
2. Calls `port.stop_scan()` and `port.close()` (awaited).
3. Saves the `DeviceStore` to the backend.

Do not declare `StreamablePort[T]` as handler
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

from cosalette import DeviceContext, DeviceStore, MemoryStore, Stream
from cosalette.testing import AppHarness

from myapp.adapters import BleAdapter
from myapp.models import SensorReading


@pytest.mark.asyncio
async def test_publishes_new_sensor() -> None:
    harness = AppHarness.create(name="sensor-bridge")
    harness.app.adapter(StreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))

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
    harness.app.adapter(StreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))

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

### Running the real lifecycle with `run_streams=True`

`inject_stream` is the seam for handler *logic*. When a test needs the real
lifecycle — a stream arming a concurrently running device, port open/scan
ordering, or both halves publishing into one recorder — opt in with
`AppHarness.create(run_streams=True)`. This mirrors the `run_periodic=` knob:
`harness.run()` opens each registered `StreamablePort`, scans, and runs the
handler for real.

```python title="tests/test_stream_arms_device.py"
import asyncio
import pytest
from cosalette import DeviceContext, Stream, StreamablePort
from cosalette.testing import AppHarness

from myapp.adapters import FakeReadingPort
from myapp.models import Reading


@pytest.mark.asyncio
async def test_stream_publishes_under_run() -> None:
    harness = AppHarness.create(run_streams=True)  # (1)!
    harness.app.adapter(StreamablePort[Reading], lambda: FakeReadingPort([Reading(21)]))

    @harness.app.stream("readings")
    async def readings(stream: Stream[Reading], ctx: DeviceContext):
        async for reading in stream:
            await ctx.publish_state({"value": reading.value})
            yield

    task = asyncio.create_task(harness.run())
    try:
        await harness.wait_for_publish_count("testapp/readings/state", 1)  # (2)!
    finally:
        harness.trigger_shutdown()
        await asyncio.wait_for(task, timeout=10.0)

    assert harness.messages_for("testapp/readings/state")[0][0] == '{"value":21}'
```

1. Default is `run_streams=False`, which suppresses streams so existing tests
   are unaffected. `True` runs the real lifecycle.
2. `wait_for_publish_count` yields until the publish lands, avoiding a
   hand-rolled spin.

`run_streams=True` **fails fast**: if a statically-enabled stream's
`StreamablePort[T]` adapter was never registered, `run()` raises `RuntimeError`
naming the port to register — rather than booting green and hanging in
`wait_for_publish_count`. (A stream disabled by a deferred `enabled=` callable is
resolved at bootstrap, so it is not preflight-checked.)

!!! warning "`run_streams=True` opens the app's real ports"
    The framework opens whatever `StreamablePort` the app registers — **real
    hardware included**. Registering a fake port on `harness.app` (as above) is
    the always-safe path. `AppHarness.create(dry_run=True)` binds the dry-run
    adapter variant **only if** the adapter was registered with a `dry_run=`
    variant (`app.adapter(Port, RealImpl, dry_run=FakeImpl)`); otherwise it
    still opens the real impl.

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
    from cosalette import DeviceContext, DeviceStore, Stream

    from myapp.adapters import BleAdapter
    from myapp.models import SensorReading

    app = cosalette.App(name="sensor-bridge", version="1.0.0", store=store_backend)

    app.adapter(StreamablePort[SensorReading], lambda: BleAdapter("AA:BB:CC:DD"))


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

- [Streaming concepts](../concepts/streaming.md) — `StreamablePort`, `Stream[T]`,
  and the push-to-pull bridge
- [Device archetypes](../concepts/device-archetypes.md) — choosing the right
  decorator
- [Testing](../guides/testing.md) — full harness reference
- [ADR-042 — Streaming protocol](../adr/ADR-042-streaming-protocol-streamableport-and-stream-t.md)
- [ADR-045 — Stateful stream receiver semantics](../adr/ADR-045-stateful-stream-receiver-semantics.md)
