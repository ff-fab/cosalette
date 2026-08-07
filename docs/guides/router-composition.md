---
icon: material/source-branch
---

# Router Composition

This guide shows how to structure multi-module cosalette applications using `Router` —
cosalette's composition primitive. You'll learn when to use routers vs app-level
decorators, how to organize code, and how to test router modules independently.

!!! tip "Start with app-level decorators"

    If you're building a quickstart example or a simple single-file bridge, use
    app-level decorators (`@app.telemetry()`, `@app.command()`) directly. Router
    is for production apps that need module boundaries — not a replacement for the
    simpler pattern.

## Prerequisites

- Familiarity with app-level decorators (see [Quickstart](../getting-started/quickstart.md))
- Understanding of [Device Archetypes](../concepts/device-archetypes.md)

## Problem: Circular Imports

When you try to split a growing cosalette app into multiple modules without routers,
you hit circular import problems:

```python title="main.py — circular import problem"
from sensors import register_sensors  # imports app
from controls import register_controls  # imports app

app = cosalette.App(name="home2mqtt", version="1.0.0")
register_sensors(app)
register_controls(app)
```

```python title="sensors.py — needs app reference"
import cosalette

def register_sensors(app: cosalette.App) -> None:
    @app.telemetry("temperature", interval=30)
    async def temp() -> dict[str, object]:
        return {"celsius": 22.5}
```

This works, but:

- `sensors.py` can't be unit-tested without creating an `App`
- `app` must be passed explicitly to every registration function
- Import order matters — easy to break

## Solution: Router

Routers let you define devices in separate modules without importing `App`:

```python title="sensors.py — self-contained router module"
import cosalette

router = cosalette.Router(prefix="sensors", tags=["environment"])


@router.telemetry("temperature", interval=30)
async def read_temperature() -> dict[str, object]:
    """Read I2C temperature sensor."""
    return {"celsius": 22.5}


@router.telemetry("humidity", interval=30)
async def read_humidity() -> dict[str, object]:
    """Read I2C humidity sensor."""
    return {"percent": 55.0}
```

```python title="controls.py — another router module"
import cosalette

router = cosalette.Router(prefix="controls", tags=["actuators"])


@router.command("relay")
async def relay_command(payload: str) -> dict[str, object]:
    """Control relay state."""
    return {"state": payload}
```

```python title="main.py — composition root"
import cosalette
from sensors import router as sensors_router
from controls import router as controls_router

app = cosalette.App(name="home2mqtt", version="1.0.0")
app.include_router(sensors_router)
app.include_router(controls_router)

if __name__ == "__main__":
    app.run()
```

**MQTT topics:**

- `home2mqtt/sensors/temperature/state`
- `home2mqtt/sensors/humidity/state`
- `home2mqtt/controls/relay/set`

Each router module is independent, testable, and has no dependency on `main.py`.

## Organizing Multi-Module Apps

### Recommended Directory Structure

```text
myapp/
├── main.py              # Composition root (App + include_router calls)
├── settings.py          # Shared Settings class
├── ports.py             # Protocol definitions (I2C, GPIO, BLE)
├── adapters.py          # Adapter implementations
├── sensors/
│   ├── __init__.py
│   ├── temperature.py   # Router for temp devices
│   └── pressure.py      # Router for pressure devices
├── controls/
│   ├── __init__.py
│   ├── relay.py         # Router for relay control
│   └── valve.py         # Router for valve control
└── tests/
    ├── test_sensors.py
    └── test_controls.py
```

Each domain gets its own router module or package.

### Example: Sensors Package

```python title="sensors/temperature.py"
import cosalette
from myapp.ports import TemperatureSensorPort

router = cosalette.Router(prefix="temp", tags=["environment"])


@router.telemetry("indoor", interval=30)
async def indoor_temp(ctx: cosalette.DeviceContext) -> dict[str, object]:
    sensor = ctx.adapter(TemperatureSensorPort)
    return {"celsius": sensor.read_indoor()}


@router.telemetry("outdoor", interval=30)
async def outdoor_temp(ctx: cosalette.DeviceContext) -> dict[str, object]:
    sensor = ctx.adapter(TemperatureSensorPort)
    return {"celsius": sensor.read_outdoor()}
```

```python title="sensors/__init__.py"
from sensors.temperature import router as temperature_router

__all__ = ["temperature_router"]
```

```python title="main.py"
from sensors import temperature_router

app = cosalette.App(name="home2mqtt", version="1.0.0")
app.include_router(temperature_router, prefix="sensors")

# Result: home2mqtt/sensors/temp/indoor/state
#         home2mqtt/sensors/temp/outdoor/state
```

Notice the two-level prefixing: `sensors` (from `include_router`) + `temp` (from `Router` constructor).

## Adapters in Routers

Routers can declare their own adapter registrations:

```python title="sensors/temperature.py"
import cosalette
from myapp.ports import TemperatureSensorPort
from myapp.adapters import I2CTemperatureSensor, DummyTemperatureSensor

router = cosalette.Router(
    prefix="temp",
    tags=["environment"],
    adapters={
        TemperatureSensorPort: (I2CTemperatureSensor, DummyTemperatureSensor),
    },
)


@router.telemetry("reading", interval=30)
async def read_temp(ctx: cosalette.DeviceContext) -> dict[str, object]:
    sensor = ctx.adapter(TemperatureSensorPort)
    return {"celsius": sensor.read()}
```

Adapters from `Router(adapters={...})` and `include_router(adapters={...})` are merged
into the app's adapter registry. Conflicts (same port with different implementations)
raise `ValueError` at include time.

## Testing Router Modules

### Unit Testing: Verify Registration

Test that devices are registered correctly without running the full app:

```python title="tests/test_sensors.py"
from sensors.temperature import router


def test_router_has_indoor_temp() -> None:
    """Verify indoor temperature telemetry is registered."""
    assert "indoor" in router.registered_names
    assert "outdoor" in router.registered_names
```

### Integration Testing: Use AppHarness

Test routers in a full application context using `AppHarness`:

```python title="tests/test_sensors_integration.py"
import pytest
from cosalette.testing import AppHarness
from sensors.temperature import router


@pytest.fixture
def harness() -> AppHarness:
    harness = AppHarness.create(name="testapp")
    harness.app.include_router(router, prefix="sensors")
    return harness


async def test_indoor_temp_publishes(harness: AppHarness) -> None:
    """Integration test for indoor temperature telemetry."""
    await harness.run()

    # Advance past first poll interval
    await harness.advance_time(30)

    # Verify MQTT publish
    harness.assert_published("testapp/sensors/temp/indoor/state", contains="celsius")
```

See the [Testing guide](testing.md) for full `AppHarness` patterns.

## Typed Contracts with Routers

Router operations support the same typed payload/return annotations as app-level
decorators:

```python title="controls/valve.py"
from __future__ import annotations
from typing import Annotated
from pydantic import BaseModel
import cosalette
from cosalette.mqtt import Payload

class ValveCommand(BaseModel):
    position: int  # 0–100

class ValveState(BaseModel):
    position: int
    flow_lpm: float

router = cosalette.Router(prefix="valves")


@router.command(
    "irrigation",
    summary="Control irrigation valve",
    payload_model=ValveCommand,
    state_model=ValveState,
)
async def handle_valve(
    cmd: Annotated[ValveCommand, Payload()],
    ctx: cosalette.DeviceContext,
) -> ValveState:
    driver = ctx.adapter(ValvePort)
    await driver.set_position(cmd.position)
    return ValveState(
        position=cmd.position,
        flow_lpm=await driver.read_flow(),
    )
```

See [Contract-First Route Design](contract-first-route-design.md) for full typed contract patterns.

Dependencies are declared the same way too — per handler parameter with
`cosalette.Depends()`. There is no router-level or `include_router`-level
`dependencies=` argument; passing one raises `TypeError`.

## Advanced Patterns

### Multi-Level Topic Hierarchy

Combine router prefix with `include_router` prefix for deep hierarchies:

```python
# Router with prefix
env_router = cosalette.Router(prefix="environment")

@env_router.telemetry("temperature", interval=30)
async def temp() -> dict[str, object]:
    return {"celsius": 22.5}

# Include with additional prefix
app.include_router(env_router, prefix="indoor")

# Result: myapp/indoor/environment/temperature/state
```

### Conditional Router Inclusion

Include routers based on configuration:

```python title="main.py"
from sensors import indoor_router, outdoor_router

app = cosalette.App(name="home2mqtt", version="1.0.0")

# Always include indoor sensors
app.include_router(indoor_router)

# Conditionally include outdoor sensors
if app.settings.enable_outdoor:
    app.include_router(outdoor_router)
```

### Shared Lifespan Logic

Routers don't have their own lifespan hooks — use the app-level lifespan to initialize
shared resources:

```python title="main.py"
import cosalette
from sensors import sensor_router

app = cosalette.App(name="home2mqtt", version="1.0.0")


@app.lifespan
async def lifespan(ctx: cosalette.AppContext) -> None:
    # Initialize resources for all routers
    i2c_bus = await initialize_i2c()
    ctx.adapter(I2CBusPort, i2c_bus)
    yield
    await i2c_bus.close()


app.include_router(sensor_router)
```

The lifespan hook runs once for the entire app, regardless of how many routers are
included.

## When to Use App vs Router

| Pattern              | Use when                                                   |
| -------------------- | ---------------------------------------------------------- |
| App-level decorators | Single-file app, quickstart, simple bridge (≤3 devices)    |
| Router composition   | Multi-file production app, shared libraries, testable boundaries |

**Hybrid approach:**

Mix both patterns in the same app — use routers for grouped devices and app-level
decorators for singleton utilities:

```python
from sensors import sensor_router
from controls import control_router

app = cosalette.App(name="home2mqtt", version="1.0.0")

# Routers for domain modules
app.include_router(sensor_router)
app.include_router(control_router)

# App-level decorator for a singleton heartbeat
@app.telemetry("heartbeat", interval=60)
async def heartbeat() -> dict[str, object]:
    return {"uptime_seconds": get_uptime()}
```

## See Also

- [Router & Composition](../concepts/router.md) — Router concepts and API reference
- [ADR-044: Public Router and Composition API](../adr/ADR-044-public-router-and-composition-api.md) — Design rationale
- [Testing](testing.md) — Testing routers with `AppHarness`
- [Contract-First Route Design](contract-first-route-design.md) — Typed router operations
