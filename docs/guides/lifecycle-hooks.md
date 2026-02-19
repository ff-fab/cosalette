---
icon: material/hook
---

# Add Lifecycle Hooks

Lifecycle hooks let you run code at application startup and shutdown — before devices
start and after they stop. Common uses: initialising hardware connections, warming up
caches, releasing resources on exit.

!!! note "Prerequisites"

    This guide assumes you've completed the
    [Quickstart](../getting-started/quickstart.md).

## How Lifecycle Hooks Work

cosalette provides two hook decorators:

| Decorator           | When it runs                                  |
| ------------------- | --------------------------------------------- |
| `@app.on_startup`   | After MQTT connects, **before** devices start |
| `@app.on_shutdown`  | After devices stop, **before** MQTT disconnects |

Both receive an `AppContext` — a limited context with access to settings and adapters,
but **no** device-specific features.

```text
MQTT Connect
    ↓
@app.on_startup hooks (in registration order)
    ↓
Device tasks start
    ↓
... running ...
    ↓
Shutdown signal (SIGTERM/SIGINT)
    ↓
Device tasks cancelled
    ↓
@app.on_shutdown hooks (in registration order)
    ↓
MQTT Disconnect
```

## A Minimal Hook

```python title="app.py"
import cosalette

app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.on_startup  # (1)!
async def init_hardware(ctx: cosalette.AppContext) -> None:
    """Initialise hardware connections at startup."""
    meter = ctx.adapter(GasMeterPort)  # (2)!
    meter.connect(ctx.settings.serial_port)


@app.on_shutdown  # (3)!
async def cleanup(ctx: cosalette.AppContext) -> None:
    """Release hardware resources on shutdown."""
    meter = ctx.adapter(GasMeterPort)
    meter.close()


app.run()
```

1. `@app.on_startup` registers the function. No parentheses needed — it's used as a
   bare decorator.
2. `ctx.adapter()` resolves adapters the same way as in device functions.
3. `@app.on_shutdown` runs after all device tasks have been cancelled.

## AppContext API

!!! warning "AppContext is NOT DeviceContext"

    Lifecycle hooks receive `AppContext`, which is deliberately limited. It does **not**
    have publish, sleep, or on_command methods — those belong to `DeviceContext` and
    only make sense inside device functions.

| Property / Method    | Description                                      |
| -------------------- | ------------------------------------------------ |
| `ctx.settings`       | Application `Settings` instance                  |
| `ctx.adapter(Port)`  | Resolve a registered adapter                     |

That's it. If you need MQTT access during startup, register a device instead — hooks
are for infrastructure setup, not for publishing messages.

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

## Hook Ordering

Hooks run in **registration order** — the order you define them in your source code:

```python title="app.py"
@app.on_startup
async def first(ctx: cosalette.AppContext) -> None:
    print("First startup hook")  # Runs first


@app.on_startup
async def second(ctx: cosalette.AppContext) -> None:
    print("Second startup hook")  # Runs second
```

Shutdown hooks also run in registration order (not reversed). If you need LIFO
(last-in-first-out) teardown, register your shutdown hooks in the reverse order of
your startup hooks.

## Error Isolation

A failing hook does **not** prevent other hooks from running:

```python title="app.py"
@app.on_startup
async def flaky_hook(ctx: cosalette.AppContext) -> None:
    raise ConnectionError("Database unreachable")  # (1)!


@app.on_startup
async def reliable_hook(ctx: cosalette.AppContext) -> None:
    print("This still runs!")  # (2)!
```

1. The exception is caught and logged at ERROR level (with full traceback). The
   framework continues to the next hook.
2. Runs normally despite the previous hook failing.

This follows the same error-isolation philosophy as device functions — a single
failure shouldn't take down the entire daemon.

!!! warning "Startup failure doesn't prevent device launch"

    If a startup hook fails, devices still start. The framework logs the error but
    proceeds. If your hardware init is critical, consider checking state in the device
    function itself and handling gracefully (e.g. retry logic, or raising to trigger
    error publication).

## Common Patterns

### Hardware Initialisation

The most common use case — open connections at startup, close at shutdown:

```python title="app.py"
from gas2mqtt.ports import GasMeterPort


@app.on_startup
async def init_serial(ctx: cosalette.AppContext) -> None:
    """Open serial connection to the gas meter."""
    meter = ctx.adapter(GasMeterPort)
    settings = ctx.settings
    assert isinstance(settings, Gas2MqttSettings)
    meter.connect(settings.serial_port, settings.baud_rate)


@app.on_shutdown
async def close_serial(ctx: cosalette.AppContext) -> None:
    """Close the serial connection cleanly."""
    meter = ctx.adapter(GasMeterPort)
    meter.close()
```

### Database / Cache Warm-Up

Pre-load data so devices don't pay the cold-start penalty:

```python title="app.py"
@app.on_startup
async def warm_cache(ctx: cosalette.AppContext) -> None:
    """Load last-known state from local cache."""
    cache = ctx.adapter(CachePort)
    await cache.load()
```

### Graceful Resource Release

Release external resources (file handles, network sockets, hardware locks):

```python title="app.py"
@app.on_shutdown
async def release_gpio(ctx: cosalette.AppContext) -> None:
    """Release GPIO pins on shutdown."""
    gpio = ctx.adapter(GpioPort)
    gpio.cleanup()
```

### Logging / Diagnostics

Log application state at boundaries:

```python title="app.py"
import logging

logger = logging.getLogger(__name__)


@app.on_startup
async def log_config(ctx: cosalette.AppContext) -> None:
    """Log configuration at startup for diagnostics."""
    logger.info(
        "Starting with settings: mqtt_host=%s, log_level=%s",
        ctx.settings.mqtt.host,
        ctx.settings.logging.level,
    )


@app.on_shutdown
async def log_goodbye(ctx: cosalette.AppContext) -> None:
    logger.info("Shutdown complete — goodbye!")
```

## Practical Example: Serial Port Lifecycle

A complete example managing a serial port connection across the application lifecycle:

```python title="app.py"
"""gas2mqtt — lifecycle hooks for serial port management."""

from __future__ import annotations

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


# --- App ---

app = cosalette.App(
    name="gas2mqtt",
    version="1.0.0",
    settings_class=Gas2MqttSettings,
)


# --- Lifecycle hooks ---

@app.on_startup
async def init_serial(ctx: cosalette.AppContext) -> None:
    """Open serial connection before devices start."""
    meter = ctx.adapter(GasMeterPort)
    settings = ctx.settings
    assert isinstance(settings, Gas2MqttSettings)
    meter.connect(settings.serial_port, settings.baud_rate)


@app.on_shutdown
async def close_serial(ctx: cosalette.AppContext) -> None:
    """Close serial connection after devices stop."""
    meter = ctx.adapter(GasMeterPort)
    meter.close()


# --- Telemetry (uses the pre-initialised adapter) ---

@app.telemetry("counter", interval=60)
async def counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    meter = ctx.adapter(GasMeterPort)  # (1)!
    return {"impulses": meter.read_impulses()}


app.run()
```

1. By the time this runs, the startup hook has already called `meter.connect()`.
   The adapter instance is shared — same object in hooks and devices.

---

## See Also

- [Application Lifecycle](../concepts/lifecycle.md) — conceptual overview of the
  startup/shutdown sequence
- [Architecture](../concepts/architecture.md) — how hooks fit into the framework
- [ADR-001](../adr/ADR-001-framework-architecture-style.md) — framework architecture
  decisions
