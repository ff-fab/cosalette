---
icon: material/timer-sync
---

# Background Periodic Tasks

Periodic tasks run a coroutine on a fixed interval with no MQTT output. They are the
right primitive for side-effect work that runs alongside your devices: flushing write
buffers, sending watchdog pings, synchronising LED state, or warming caches.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## When to Use `@app.periodic`

| Need | Primitive |
|------|-----------|
| Publish sensor data to MQTT on an interval | `@app.telemetry` |
| Long-running loop with full MQTT control | `@app.device` |
| Side-effect work on an interval, no MQTT output | **`@app.periodic`** |

`@app.periodic` is deliberately MQTT-free. It has no `/state` topic, no publish
strategy, no error topic, and no `DeviceContext`. If your handler needs to publish
anything, use `@app.telemetry` or `@app.device` instead.

## Quick Start

Register a coroutine with `@app.periodic` and give it a name and interval:

```python title="app.py"
import cosalette

app = cosalette.App(name="bridge", version="1.0.0")


@app.periodic("flush-buffer", interval=60.0)  # (1)!
async def flush_buffer() -> None:  # (2)!
    """Flush the local write buffer to upstream storage."""
    await cache.flush()  # (3)!


app.run()
```

1. `"flush-buffer"` is the task name. `interval=60.0` is seconds between invocations.
2. No return value is expected or used — the framework ignores any return.
3. Your side-effect logic. No MQTT calls needed.

The framework spawns `flush_buffer` as an `asyncio.Task` at startup. It sleeps for 60
seconds, invokes the handler, sleeps again, and repeats until shutdown.

!!! info "Sleep-first semantics"

    The interval elapses **before** the first invocation. If you need an immediate
    first run, perform it in an `init=` factory (see [One-shot init](#one-shot-init-with-init)).

## Dependency Injection

Periodic handlers use the same DI system as `@app.device`. Declare parameters by type
and the framework injects them:

```python title="app.py"
import cosalette
from myapp.ports import BufferPort, LedPort


class AppSettings(cosalette.Settings):
    flush_url: str = "http://localhost:8080/flush"


app = cosalette.App(name="bridge", version="1.0.0")


@app.periodic("flush-buffer", interval=30.0)
async def flush_buffer(
    buf: BufferPort,          # (1)!
    settings: AppSettings,    # (2)!
    clock: cosalette.ClockPort,  # (3)!
) -> None:
    if buf.pending_count() > 0:
        await buf.flush(settings.flush_url)
```

1. Adapter ports are injected by type — register the adapter with `app.adapter()`.
2. Your `Settings` subclass is injected automatically.
3. `ClockPort` lets you read the current time without coupling to `time.time()`.

Available injection targets: `Settings` subclasses, adapter ports, `ClockPort`,
`@app.state` instances. `DeviceContext` is **not** available — periodic tasks have no
MQTT lifecycle.

## `timedelta` Interval Syntax

`interval=` accepts a `datetime.timedelta` as an alternative to a raw float:

```python
import datetime

@app.periodic("daily-report", interval=datetime.timedelta(hours=24))
async def daily_report() -> None:
    ...

@app.periodic("heartbeat", interval=datetime.timedelta(seconds=30))
async def heartbeat() -> None:
    ...
```

The framework converts the `timedelta` to seconds at registration time.

## Deferred Interval with `SettingRef`

Use `SettingRef` to read the interval from your settings at bootstrap:

```python
from cosalette import SettingRef


class AppSettings(cosalette.Settings):
    led_sync_interval: float = 5.0


@app.periodic("led-sync", interval=SettingRef("led_sync_interval", default=5.0))
async def led_sync(led: LedPort) -> None:
    await led.sync_state()
```

Or pass a callable that receives the resolved `Settings` instance:

```python
@app.periodic("flush-buffer", interval=lambda s: s.flush_interval_seconds)
async def flush_buffer(buf: BufferPort) -> None:
    await buf.flush()
```

Both forms are resolved during the bootstrap phase, before the task is spawned.

## Conditional Registration with `enabled=`

`enabled=` accepts a literal `bool` or a callable that receives the resolved settings:

```python
# Literal — decided at decoration time
@app.periodic("debug-logger", interval=10.0, enabled=False)
async def debug_logger() -> None:
    ...  # never registered


# Deferred — decided at bootstrap from settings
@app.periodic(
    "watchdog",
    interval=60.0,
    enabled=lambda s: s.watchdog_enabled,
)
async def watchdog_ping(settings: AppSettings) -> None:
    await ping_watchdog(settings.watchdog_url)
```

A literal `enabled=False` silently skips registration. A callable is evaluated after
settings are resolved — if it returns `False`, the task is not spawned.

This mirrors the `enabled=` behaviour on `@app.telemetry` (see
[ADR-038](../adr/ADR-038-deferred-enabled-for-decorator-registrations.md)).

## One-Shot Init with `init=`

Use `init=` for setup that must run once before the loop starts. The factory uses the
same DI rules as the handler:

```python
@app.periodic(
    "cache-warmer",
    interval=300.0,
    init=lambda cache: cache.warm(),  # (1)!
)
async def cache_warmer(cache: CachePort) -> None:
    await cache.refresh()
```

1. `init=` is called once at startup before the first sleep. The factory receives the
   same injected parameters as the handler.

This mirrors `init=` on `@app.device`.

## Error Handling

When a periodic handler raises an exception, the framework:

1. Logs the error at `ERROR` level with the task name and exception message.
2. **Continues the loop** — the next invocation runs after the next interval elapses.

No error is published to MQTT. No circuit breaker fires. The task never stops unless
shutdown is requested.

```python
@app.periodic("fragile-task", interval=10.0)
async def fragile_task() -> None:
    result = await external_api.call()  # may raise
    process(result)
    # If external_api.call() raises, the error is logged and the next
    # call fires after another 10 seconds.
```

!!! warning "asyncio.CancelledError is not caught"

    The framework re-raises `CancelledError` immediately. This is what enables clean
    shutdown — do not catch `CancelledError` inside periodic handlers.

## Testing

### Single-cycle invocation with `tick_periodic`

`AppHarness.tick_periodic(name)` invokes one cycle of a named periodic handler
**without** sleeping. This is the recommended way to test periodic handlers:

```python title="tests/test_periodic.py"
import pytest
from cosalette.testing import AppHarness

from myapp.app import app


@pytest.fixture
def harness() -> AppHarness:
    return AppHarness.create(app=app)


@pytest.mark.asyncio
async def test_flush_buffer_calls_flush(harness: AppHarness) -> None:
    mock_buf = MockBufferPort()
    harness.override_adapter(BufferPort, mock_buf)

    await harness.tick_periodic("flush-buffer")  # (1)!

    assert mock_buf.flush_called
```

1. One invocation, no sleep, no task spawning. The handler runs synchronously
   inside `tick_periodic`.

### Suppressing periodic tasks in integration tests

By default, `AppHarness.create()` sets `run_periodic=False` — periodic tasks are
**not spawned** when running tests. This keeps existing integration tests unaffected:

```python
# Existing tests — periodic tasks never start
harness = AppHarness.create(app=app)

# Opt in to spawning periodic tasks if you need integration-level coverage
harness = AppHarness.create(app=app, run_periodic=True)
```

Use `run_periodic=True` only when you need to verify that a periodic task actually
fires during the full app lifecycle. For unit-level testing of handler logic, prefer
`tick_periodic`.

## Companion Pattern: Periodic Flush Alongside Telemetry

A common pattern is a `@app.telemetry` handler that accumulates readings into a
buffer, combined with a `@app.periodic` task that flushes the buffer upstream on a
longer interval:

```python title="app.py"
import cosalette

app = cosalette.App(name="sensor-bridge", version="1.0.0")


@app.state
def reading_buffer() -> ReadingBuffer:
    return ReadingBuffer(capacity=100)


@app.telemetry("temperature", interval=10.0)
async def read_temperature(
    sensor: SensorPort,
    buf: ReadingBuffer,
) -> dict[str, object]:
    reading = await sensor.read()
    buf.append(reading)
    return {"celsius": reading.temp}


@app.periodic("upstream-flush", interval=300.0)  # (1)!
async def flush_upstream(buf: ReadingBuffer) -> None:
    if buf.pending_count() > 0:
        await buf.flush_to_api()
```

1. The telemetry handler publishes to MQTT every 10 s. The periodic task flushes
   buffered readings to the upstream API every 5 minutes — independently of the
   MQTT cadence.

The two handlers share `ReadingBuffer` via `@app.state` DI. Neither owns the other's
timing.
