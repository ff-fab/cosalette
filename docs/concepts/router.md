---
icon: material/router
---

# Router and Composition

The `Router` class lets you organize related devices, commands, and telemetry into
separate modules without circular import dependencies. It's the cosalette equivalent
of FastAPI's `APIRouter` — a composition primitive that makes multi-module applications
clean and testable.

!!! info "App-level decorators are still first-class"

    For small, single-file applications, using `@app.telemetry()` and `@app.command()`
    directly is idiomatic and preferred. Router is for production apps that need
    multi-module organization — not a replacement for the app-level pattern.

## When to Use Router

| Pattern              | Use when                                                      |
| -------------------- | ------------------------------------------------------------- |
| App-level decorators | Single-file apps, quickstart examples, simple bridges (≤3 devices) |
| Router composition   | Multi-module projects, shared libraries, testable boundaries  |

**Key benefits of Router:**

- **Module independence** — Define devices in separate files without importing `App`
- **Testable boundaries** — Unit test router modules in isolation
- **Topic prefixing** — Group related devices under a common MQTT topic segment
- **Tag accumulation** — Apply metadata (e.g., `["environment"]`, `["production"]`) at
  multiple layers
- **Adapter scoping** — Register adapters at the router level

## Basic Usage

Create a router in a separate module, register devices on it, then include it in your
app's composition root:

```python title="sensors.py — router module"
import cosalette

router = cosalette.Router(prefix="sensors", tags=["environment"])


@router.telemetry("temperature", interval=30)
async def read_temperature() -> dict[str, object]:
    """Read I2C temperature sensor."""
    return {"celsius": 22.5}


@router.command("calibrate")
async def calibrate(payload: str, ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Calibrate the temperature sensor."""
    # Calibration logic here
    return {"calibrated": True}
```

```python title="main.py — composition root"
import cosalette
from sensors import router as sensors_router

app = cosalette.App(name="home2mqtt", version="1.0.0")
app.include_router(sensors_router)

if __name__ == "__main__":
    app.run()
```

**MQTT topics:**

- Telemetry: `home2mqtt/sensors/temperature/state`
- Command: `home2mqtt/sensors/calibrate/set`

The `prefix="sensors"` parameter adds a topic segment between the app name and the
device name.

## Router Constructor Parameters

```python
router = cosalette.Router(
    prefix="sensors",            # Single MQTT topic segment (no `/` allowed)
    tags=["environment", "production"],  # Metadata for introspection
    adapters={...},              # Adapter registrations (see below)
    dependencies=None,           # Reserved for future DI epic (raises NotImplementedError)
)
```

| Parameter      | Type         | Description                                              |
| -------------- | ------------ | -------------------------------------------------------- |
| `prefix`       | `str \| None` | Single MQTT topic segment prepended to all device names |
| `tags`         | `list[str]`  | Tags accumulate across router → include_router → operation |
| `adapters`     | `dict`       | Adapter registrations merged at include time             |
| `dependencies` | Reserved     | Raises `NotImplementedError` until dependency injection epic ships |

**Prefix validation:**

- Must be a single MQTT topic segment (no `/` character, no wildcards)
- Validated by the same rules as device names ([ADR-002](../adr/ADR-002-mqtt-topic-conventions.md))

## Router Decorators

Router provides the same decorator surface as `App`:

- `@router.telemetry(name, interval=...)`
- `@router.command(name)`
- `@router.device(name)`
- `@router.stream(name)`
- `@router.periodic(interval=...)`
- `@router.react(state_type)`

All parameters, behavior, and semantics are identical to their `@app.*` equivalents.
See the respective guides for usage patterns.

## Including a Router

Use `App.include_router()` to merge router registrations into your app:

```python
app.include_router(
    router,
    prefix="sensors",         # Additional prefix level (optional)
    tags=["production"],      # Additional tags (optional)
    adapters={...},           # Additional adapters (optional)
)
```

**Prefix combination:**

| Router prefix | include_router prefix | Combined result |
| ------------- | --------------------- | --------------- |
| `None`        | `None`                | `None`          |
| `"sensors"`   | `None`                | `"sensors"`     |
| `None`        | `"env"`               | `"env"`         |
| `"temp"`      | `"sensors"`           | `"sensors/temp"` |

**Example with two prefix levels:**

```python
router = cosalette.Router(prefix="temp")

@router.telemetry("outside", interval=30)
async def outside_temp() -> dict[str, object]:
    return {"celsius": 15.2}

app.include_router(router, prefix="sensors")
# → Topic: myapp/sensors/temp/outside/state
```

## Tag Accumulation

Tags accumulate from three sources, deduplicated while preserving insertion order:

1. Router constructor (`tags=["environment"]`)
2. `include_router()` call (`tags=["production"]`)
3. Operation decorator (`@router.telemetry(..., tags=["critical"])`)

```python
router = cosalette.Router(prefix="sensors", tags=["environment"])

@router.telemetry("temp", interval=30, tags=["critical"])
async def read_temp() -> dict[str, object]:
    return {"celsius": 22.5}

app.include_router(router, tags=["production"])

# Final tags on the registration: ["environment", "production", "critical"]
```

Tags are introspection metadata — they appear in `app.asyncapi()` output and can be
queried by MCP tools or monitoring systems. They have no runtime effect on MQTT behavior.

## Adapter Scoping

Routers can declare their own adapter registrations:

```python title="sensors.py"
from sensors.ports import TemperatureSensorPort
from sensors.adapters import I2CTemperatureSensor

router = cosalette.Router(
    prefix="sensors",
    adapters={
        TemperatureSensorPort: I2CTemperatureSensor,
    },
)


@router.telemetry("temperature", interval=30)
async def read_temperature(ctx: cosalette.DeviceContext) -> dict[str, object]:
    sensor = ctx.adapter(TemperatureSensorPort)
    return {"celsius": sensor.read()}
```

Adapters from `Router(adapters={...})` and `include_router(adapters={...})` are merged
into the app's adapter registry. Conflicts (same port registered twice with different
implementations) raise `ValueError` at `include_router()` call time.

## Testing Routers

Routers can be tested independently without instantiating an `App`:

```python title="test_sensors.py"
import pytest
from sensors import router


def test_router_has_temperature_device() -> None:
    """Verify temperature telemetry is registered."""
    assert "temperature" in router.registered_names
```

### Asserting registration metadata

`registered_names` answers "is this name registered?" but gives no metadata.
The five collection properties expose the registration objects directly — including
fields such as `payload_model`, `state_model`, `init`, and scheduling metadata — and
mirror the equivalent properties on `App`:

| Property                          | Return type                         |
| --------------------------------- | ----------------------------------- |
| `router.commands`                 | `Sequence[CommandRegistration]`     |
| `router.telemetry_registrations`  | `Sequence[TelemetryRegistration]`   |
| `router.devices`                  | `Sequence[DeviceRegistration]`      |
| `router.periodic_registrations`   | `Sequence[PeriodicRegistration]`    |
| `router.adapters`                 | `Mapping[type, ...]`                |

```python title="test_system_device.py"
from myapp.devices.system import router, SystemActionCommand, SystemActionState


def test_action_command_metadata() -> None:
    """The action command wires the expected payload and state models."""
    reg = next(r for r in router.commands if r.name == "action")
    assert reg.payload_model is SystemActionCommand
    assert reg.state_model is SystemActionState
```

All five properties return immutable snapshots (`tuple` or `MappingProxyType`), so
router internals cannot be mutated through them. The `_registrations` suffix on
`telemetry_registrations` and `periodic_registrations` avoids shadowing the
`@router.telemetry` and `@router.periodic` decorators.

For integration tests, use `AppHarness` with `include_router()`:

```python title="test_sensors_integration.py"
from cosalette.testing import AppHarness
from sensors import router


async def test_temperature_telemetry(harness: AppHarness) -> None:
    """Integration test for temperature telemetry via router."""
    harness.app.include_router(router)

    await harness.run()
    await harness.advance_time(30)

    harness.assert_published("testapp/sensors/temperature/state", contains="celsius")
```

See the [Testing guide](../guides/testing.md) for full harness patterns.

## Snapshot Semantics

`include_router()` captures registrations at **call time** — modifications to the router
after inclusion do not affect the app:

```python
router = cosalette.Router()

@router.telemetry("temp", interval=30)
async def temp() -> dict[str, object]:
    return {"celsius": 22.5}

app.include_router(router)  # Snapshot taken here

# This registration is NOT included in the app
@router.telemetry("humidity", interval=30)
async def humidity() -> dict[str, object]:
    return {"percent": 55.0}
```

This matches FastAPI's behavior and makes composition order explicit.

## Multiple Inclusion

A router can be included multiple times, optionally with different prefixes or tags:

```python
router = cosalette.Router(tags=["sensor"])

@router.telemetry("reading", interval=30)
async def reading() -> dict[str, object]:
    return {"value": 42}

app.include_router(router, prefix="indoor", tags=["environment"])
app.include_router(router, prefix="outdoor", tags=["environment"])

# Two registrations created:
# - myapp/indoor/reading/state with tags: ["sensor", "environment"]
# - myapp/outdoor/reading/state with tags: ["sensor", "environment"]
```

Each inclusion creates independent device contexts and task groups.

## Nested Routers (Not Supported)

`Router.include_router()` does not exist — routers cannot include other routers.
Multi-level composition must be done at the `App` level:

```python
# WRONG — Router has no include_router method
parent_router.include_router(child_router)  # AttributeError

# RIGHT — App composes multiple routers
app.include_router(sensors_router, prefix="env")
app.include_router(controls_router, prefix="cmd")
```

This constraint is documented in [ADR-044](../adr/ADR-044-public-router-and-composition-api.md)
and may be revisited in a future release based on user feedback.

## When to Use App-Level vs Router

**Use app-level decorators when:**

- Building a quickstart example or tutorial project
- Single-file application (≤200 lines, ≤3 devices)
- Prototyping or exploratory development
- No shared libraries or reusable modules needed

**Use Router when:**

- Multi-file production application
- Separating devices by domain (sensors, controls, diagnostics)
- Building a library of reusable device groups
- Testing device logic independently from app wiring

**Hybrid approach:**

You can mix both patterns in the same app — use app-level decorators for singleton
devices and routers for grouped modules:

```python
from sensors import router as sensors_router

app = cosalette.App(name="home2mqtt", version="1.0.0")

# Router for grouped sensors
app.include_router(sensors_router)

# App-level decorator for a singleton heartbeat
@app.telemetry("heartbeat", interval=60)
async def heartbeat() -> dict[str, object]:
    return {"uptime_seconds": get_uptime()}
```

## See Also

- [ADR-044: Public Router and Composition API](../adr/ADR-044-public-router-and-composition-api.md) — Full design rationale
- [Architecture](architecture.md) — Composition root pattern
- [Testing](testing.md) — Testing routers with `AppHarness`
- [Contract-First Route Design](../guides/contract-first-route-design.md) — Typed router operations
