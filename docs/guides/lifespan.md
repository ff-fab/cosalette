---
icon: material/timer-sand
---

# Manage App Lifespan

The lifespan pattern lets you run code at application startup and shutdown — before
devices start and after they stop. Common uses: initialising hardware connections,
warming up caches, releasing resources on exit.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## How Lifespan Works

cosalette uses a single **async context manager** to handle both startup and shutdown
logic. You pass it to the `App` constructor via the `lifespan` parameter:

```python
App(name="myapp", version="1.0.0", lifespan=my_lifespan)
```

The function runs as a context manager around the device phase:

```text
MQTT Connect
    ↓
Enter lifespan (code before yield)
    ↓
Device tasks start
    ↓
... running ...
    ↓
Shutdown signal (SIGTERM/SIGINT)
    ↓
Device tasks cancelled
    ↓
Exit lifespan (code after yield)
    ↓
MQTT Disconnect
```

This is the same pattern used by
[Starlette/FastAPI lifespan](https://www.starlette.io/lifespan/) — if you've used
that, this will feel familiar.

## A Minimal Lifespan

```python title="app.py"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import cosalette
from gas2mqtt.ports import GasMeterPort


@asynccontextmanager  # (1)!
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:  # (2)!
    """Initialise hardware at startup, close at shutdown."""
    meter = ctx.adapter(GasMeterPort)  # (3)!
    meter.connect(ctx.settings.serial_port)
    yield  # (4)!
    meter.close()  # (5)!


app = cosalette.App(
    name="gas2mqtt",
    version="1.0.0",
    lifespan=lifespan,  # (6)!
)


app.run()
```

1. `@asynccontextmanager` from the standard library turns an async generator into a
   context manager. This is the recommended way to write lifespan functions.
2. The `LifespanFunc` type alias is exported from `cosalette` for type annotations.
   The function receives an `AppContext` and yields nothing.
3. `ctx.adapter()` resolves adapters the same way as in device functions.
4. `yield` marks the boundary between startup and shutdown. Everything before `yield`
   runs before devices start; everything after runs after devices stop.
5. Cleanup code after `yield` runs during shutdown — even if devices crashed.
6. Pass the lifespan function to the `App` constructor. It's called once during the
   application lifecycle.

!!! tip "Type annotation"

    cosalette exports a `LifespanFunc` type alias you can use for explicit typing:

    ```python
    lifespan: cosalette.LifespanFunc = my_lifespan_function
    ```

## AppContext API

!!! warning "AppContext is NOT DeviceContext"

    The lifespan function receives `AppContext`, which is deliberately limited. It does
    **not** have publish, sleep, or on_command methods — those belong to `DeviceContext`
    and only make sense inside device functions.

| Property / Method    | Description                                      |
| -------------------- | ------------------------------------------------ |
| `ctx.settings`       | Application `Settings` instance                  |
| `ctx.adapter(Port)`  | Resolve a registered adapter                     |

That's it. If you need MQTT access during startup, register a device instead — the
lifespan is for infrastructure setup, not for publishing messages.

**Comparison with DeviceContext:**

| Capability         | `AppContext` | `DeviceContext` |
| ------------------ | ------------ | --------------- |
| `.settings`        | ✅           | ✅              |
| `.adapter()`       | ✅           | ✅              |
| `.publish_state()` | ❌           | ✅              |
| `.publish()`       | ❌           | ✅              |
| `.sleep()`         | ❌           | ✅              |
| `.on_command`      | ❌           | ✅              |
| `.name`            | ❌           | ✅              |
| `.clock`           | ❌           | ✅              |

## Startup vs Shutdown

The `yield` statement divides the lifespan into two halves:

```python title="app.py"
@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    # --- Startup ---
    # This code runs BEFORE devices start.
    # Use it to initialise resources, warm caches, connect hardware.
    print("Starting up!")

    yield  # ← devices run here

    # --- Shutdown ---
    # This code runs AFTER devices stop.
    # Use it to release resources, flush buffers, close connections.
    print("Shutting down!")
```

This is the **paired resource** pattern — the natural structure of a context manager
ensures that every resource opened during startup gets cleaned up during shutdown.
Compare this to separate startup/shutdown functions where it's easy to forget cleanup.

## Error Handling

If an exception occurs in the **startup** half (before `yield`), the lifespan aborts
and the application does not start device tasks. The exception propagates to the
framework, which logs it and shuts down.

If an exception occurs in the **shutdown** half (after `yield`), it is caught and
logged at ERROR level — but does not prevent MQTT disconnection.

```python title="app.py"
@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    meter = ctx.adapter(GasMeterPort)
    try:
        meter.connect(ctx.settings.serial_port)  # (1)!
    except ConnectionError:
        logger.error("Hardware not available — aborting startup")
        raise  # (2)!

    yield

    try:
        meter.close()  # (3)!
    except Exception:
        logger.exception("Error during hardware cleanup")
        # Don't re-raise — let the rest of shutdown proceed
```

1. Startup failures can be caught and handled with custom logic.
2. Re-raising aborts the application. Use this for critical resources.
3. Use try/except in cleanup to ensure partial failures don't prevent other
   cleanup from running.

!!! tip "Critical vs non-critical resources"

    If your hardware init is critical (the app can't function without it), let the
    exception propagate — the framework will shut down cleanly. If it's non-critical
    (e.g. a cache warm-up), catch the exception and log a warning so devices can
    still start.

## Common Patterns

### Hardware Initialisation

The most common use case — open connections at startup, close at shutdown:

```python title="app.py"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from gas2mqtt.ports import GasMeterPort
from gas2mqtt.settings import Gas2MqttSettings

import cosalette


@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    """Open serial connection before devices start, close after."""
    meter = ctx.adapter(GasMeterPort)
    settings = ctx.settings
    assert isinstance(settings, Gas2MqttSettings)
    meter.connect(settings.serial_port, settings.baud_rate)
    yield
    meter.close()
```

### Database / Cache Warm-Up

Pre-load data so devices don't pay the cold-start penalty:

```python title="app.py"
@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    """Load cache at startup, flush at shutdown."""
    cache = ctx.adapter(CachePort)
    await cache.load()
    yield
    await cache.flush()
```

### Multiple Resources

Use `try/finally` or nested context managers for multiple resources:

```python title="app.py"
@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    """Initialise hardware and database connections."""
    meter = ctx.adapter(GasMeterPort)
    db = ctx.adapter(DatabasePort)

    meter.connect(ctx.settings.serial_port)
    await db.connect(ctx.settings.db_url)

    try:
        yield
    finally:
        meter.close()
        await db.disconnect()
```

!!! tip "try/finally for guaranteed cleanup"

    Wrapping `yield` in `try/finally` ensures cleanup runs even if device tasks
    raise unexpected exceptions during shutdown. This is best practice when managing
    multiple resources.

### Logging / Diagnostics

Log application state at lifecycle boundaries:

```python title="app.py"
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    """Log configuration at startup, farewell at shutdown."""
    logger.info(
        "Starting with settings: mqtt_host=%s, log_level=%s",
        ctx.settings.mqtt.host,
        ctx.settings.logging.level,
    )
    yield
    logger.info("Shutdown complete — goodbye!")
```

## Practical Example: Serial Port Lifecycle

A complete example managing a serial port connection across the application lifecycle:

```python title="app.py"
"""gas2mqtt — lifespan for serial port management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

import cosalette
from pydantic import Field
from pydantic_settings import SettingsConfigDict


# --- Port ---

@runtime_checkable
class GasMeterPort(Protocol):
    def connect(self, port: str, baud_rate: int) -> None: ...
    def read_impulses(self) -> int: ...
    def close(self) -> None: ...


# --- Settings ---

class Gas2MqttSettings(cosalette.Settings):
    model_config = SettingsConfigDict(
        env_prefix="GAS2MQTT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
    serial_port: str = Field(default="/dev/ttyUSB0")
    baud_rate: int = Field(default=9600)


# --- Lifespan ---

@asynccontextmanager
async def lifespan(ctx: cosalette.AppContext) -> AsyncIterator[None]:
    """Open serial connection before devices start, close after."""
    meter = ctx.adapter(GasMeterPort)
    settings = ctx.settings
    assert isinstance(settings, Gas2MqttSettings)
    meter.connect(settings.serial_port, settings.baud_rate)
    yield
    meter.close()


# --- App ---

app = cosalette.App(
    name="gas2mqtt",
    version="1.0.0",
    settings_class=Gas2MqttSettings,
    lifespan=lifespan,
)


# --- Telemetry (uses the pre-initialised adapter) ---

@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    meter = ctx.adapter(GasMeterPort)  # (1)!
    return {"impulses": meter.read_impulses()}


app.run()
```

1. By the time this runs, the lifespan has already called `meter.connect()`.
   The adapter instance is shared — same object in the lifespan and devices.

---

## See Also

- [Application Lifecycle](../concepts/lifecycle.md) — conceptual overview of the
  startup/shutdown sequence
- [Architecture](../concepts/architecture.md) — how lifespan fits into the framework
- [ADR-001](../adr/ADR-001-framework-architecture-style.md) — framework architecture
  decisions
