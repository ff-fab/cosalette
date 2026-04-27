---
icon: material/broadcast
---

# Using @app.stream

`@app.stream` eliminates the boilerplate of opening a port, wiring a callback,
and tearing everything down on shutdown. Register a `StreamablePort[T]` adapter
once, write a handler that iterates a `Stream[T]`, and the framework handles
the rest.

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
handler is your responsibility — call your MQTT client or a context helper
as needed. If you want automatic periodic state publication, use
`@app.telemetry` instead.

## Step 1 — Define a `StreamablePort` adapter

Implement the five-method `StreamablePort[T]` protocol in your adapter:

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

## Step 2 — Register the adapter

Call `app.adapter()` before the handler is registered:

```python title="app.py"
import cosalette

from myapp.adapters import UsbScannerAdapter
from myapp.ports import ScannerPort

app = cosalette.App(name="scanner-bridge", version="1.0.0")

app.adapter(ScannerPort, lambda: UsbScannerAdapter(device="/dev/hidraw0"))
```

The framework matches the `Stream[Barcode]` parameter in the handler to the
registered `StreamablePort[Barcode]` by type at startup. A missing or
mismatched adapter raises a `RegistrationError` before the app runs.

## Step 3 — Write the handler

Declare a single `Stream[T]` parameter and iterate:

```python title="app.py"
from cosalette import Stream

from myapp.models import Barcode


@app.stream("barcode-scanner")  # (1)!
async def handle_scans(stream: Stream[Barcode]) -> None:
    async for barcode in stream:  # (2)!
        await process_barcode(barcode)  # (3)!
```

1. The name string is optional. When omitted, the function name is used.
2. `async for` blocks until the next item arrives or shutdown is signalled.
   The framework signals shutdown by calling `stream.shutdown()`, which causes
   the iterator to stop after draining any queued items.
3. Your domain logic. Publish to MQTT, write to a database, forward downstream.

### What the framework manages

Before calling the handler the framework:

1. Locates the registered `StreamablePort[T]` adapter.
2. Creates a `Stream[T]` instance.
3. Calls `port.open()`, `port.register_callback(stream.put)`, and
   `port.start_scan()`.

On shutdown, after the handler exits:

1. Calls `stream.shutdown()` to drain and stop the iterator.
2. Calls `port.stop_scan()` and `port.close()`.

Do not declare `StreamablePort[T]` as a handler parameter — the framework
manages it. Other DI targets (`Settings` subclasses, `@app.state` instances,
`ClockPort`) may be declared alongside `Stream[T]` as normal.

## Step 4 — Test with `inject_stream`

`AppHarness.inject_stream` feeds items directly into the handler's stream,
bypassing the hardware adapter entirely:

```python title="tests/test_scanner.py"
import pytest
from cosalette.testing import AppHarness

from myapp.app import app
from myapp.models import Barcode


@pytest.fixture
def harness() -> AppHarness:
    return AppHarness.create(app=app)


@pytest.mark.asyncio
async def test_barcode_processed(harness: AppHarness) -> None:
    barcode = Barcode(code="12345678", symbology="EAN-8")

    await harness.inject_stream(  # (1)!
        "barcode-scanner",
        barcode,
        shutdown=True,  # (2)!
    )

    assert harness.state("barcode-scanner")["last_code"] == "12345678"
```

1. `inject_stream(name, *items)` delivers each item to the handler's stream in
   order, then signals shutdown so the `async for` loop exits cleanly.
2. `shutdown=True` is the default. Pass `shutdown=False` to keep the stream
   open for multi-batch injection tests.

## Conditional registration with `enabled=`

`enabled=` follows the same rules as all other cosalette decorators:

```python
# Skip at decoration time
@app.stream("scanner", enabled=False)
async def handle_scans(stream: Stream[Barcode]) -> None:
    ...


# Defer the decision to bootstrap — settings are resolved first
@app.stream(
    "scanner",
    enabled=lambda s: s.scanner_enabled,
)
async def handle_scans(stream: Stream[Barcode]) -> None:
    ...
```

A callable receives the resolved `Settings` instance. When it returns `False`
the handler is silently skipped — no adapter is opened, no task is spawned.

See
[ADR-038](../adr/ADR-038-deferred-enabled-for-decorator-registrations.md)
for the deferred `enabled=` design rationale.

## Complete example

```python title="app.py"
from __future__ import annotations

import cosalette
from cosalette import Stream

from myapp.adapters import UsbScannerAdapter
from myapp.models import Barcode
from myapp.ports import ScannerPort

app = cosalette.App(name="scanner-bridge", version="1.0.0")

app.adapter(ScannerPort, lambda: UsbScannerAdapter(device="/dev/hidraw0"))


@app.stream("barcode-scanner")
async def handle_scans(stream: Stream[Barcode]) -> None:
    async for barcode in stream:
        await process_barcode(barcode)


app.run()
```

## See also

- [Streaming concepts](../concepts/streaming.md) — `StreamablePort`, `Stream[T]`,
  and the push-to-pull bridge
- [Device archetypes](../concepts/device-archetypes.md) — choosing the right
  decorator
- [Testing](../guides/testing.md) — full harness reference
- [ADR-042 — Streaming protocol](../adr/ADR-042-streaming-protocol-streamableport-and-stream-t.md)
