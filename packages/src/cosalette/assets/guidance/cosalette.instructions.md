---
description: 'cosalette framework development guidance for AI agents'
applyTo: '**/*.py'
---

# cosalette Framework Instructions

Quick-reference only. For depth: `cosalette ai help <topic>`
Topics: `telemetry` · `testing` · `configuration` · `architecture` · `commands` · `health` · `scheduling` · `resilience` · `sub-entities` · `triggerable` · `multi-device` · `contracts` · `manifest` · `router` · `migration`

## Archetype — Pick One

| Archetype | When | Decorator |
|-----------|------|-----------|
| **Telemetry** | Periodic sensor read / scheduled publish | `@app.telemetry(name, interval=N)` |
| **Command** | Handle inbound MQTT `…/set` payloads | `@app.command(name)` |
| **Device** | Explicit `while` loop / state machine | `@app.device(name)` — async generator |

Default to **telemetry**. Multiple similar devices → `name=lambda s: {…}` dict form (not `@app.on_configure`).
See `cosalette ai help architecture`.

## Router — Multi-Module Composition

**App-level decorators remain first-class for small apps.** Router is for production multi-module organization.

```python
# sensors.py — router module
import cosalette

router = cosalette.Router(prefix="sensors", tags=["environment"])

@router.telemetry("temperature", interval=30)
async def temp() -> dict[str, object]:
    return {"celsius": 22.5}

# main.py — composition root
from myapp import sensors

app = cosalette.App(name="home2mqtt", version="1.0.0")
app.include_router(sensors.router)
```

**When to use Router:**
- Multi-module projects (sensors.py, controls.py, etc.)
- Shared libraries exporting device bundles
- Testable module boundaries
- Apps with >3 devices or multiple hardware subsystems

**When NOT to use Router:**
- Single-file apps — use `@app.telemetry` directly
- Quickstart examples or tutorials
- Simple bridges (≤3 devices)

Topic prefixing: `{app}/sensors/temperature/state`. Tags accumulate. Scoped adapters override app-level.

See `cosalette ai help router`, `cosalette ai help migration`.

## `@app.device` — Async Generator (Breaking Change)

`@app.device` handlers **must** be async generators. `yield` marks the reaction boundary:

```python
@app.device("sensor")
async def sensor(ctx: cosalette.DeviceContext):   # no return annotation
    while not ctx.shutdown_requested:
        data = await read_sensor()
        await ctx.publish_state(data)
        yield                                      # reaction boundary
        await ctx.sleep(30)
```

Plain coroutines (`async def … -> None`) now raise `TypeError`. Remove `-> None` return annotations.

## `@app.react` — Domain-Event Reactors

Use `@app.react` to keep state objects pure domain models. The framework calls the reactor
automatically when the state has pending events — no manual flush calls in handlers:

```python
@app.state
def shared_state() -> SharedState:
    return SharedState()

@app.react(SharedState, drain=lambda s: s.registry.drain_events())
async def on_registry_events(
    events: list[RegistryEvent],   # reserved name — injected by framework
    ctx: cosalette.DeviceContext,
    store: DeviceStore,
    state: SharedState,
) -> None:
    for event in events:
        await ctx.publish("registry/event", event.to_dict())
    store["registry"] = state.registry.to_dict()
```

Rules:
- `StateType` must be registered via `@app.state` first
- `events` is a **reserved parameter name** — injected directly, not via type-DI
- `drain=None` → framework calls `state_instance.drain_events()` structurally
- Reactors fire after `yield` in `@app.device`; after successful return in other handlers
- See `cosalette ai help react`

## `main.py` — Declarative Only

```python
import cosalette

app = cosalette.App(name="mybridge", version="0.1.0", settings_class=MySettings)
app.adapter(SensorPort, "myapp.adapters:SensorAdapter", dry_run="myapp.adapters:DryRunAdapter")

@app.telemetry("sensor", interval=cosalette.setting_ref("poll_interval"))
async def sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"value": await ctx.adapter(SensorPort).read()}

if __name__ == "__main__":
    app.run()
```

Rules:
- Decorators and `app.run()` only — no business logic in `main.py`
- `ctx.sleep(N)` — never `asyncio.sleep()` or `time.sleep()` in device coroutines
- `ctx.adapter(Port)` — never import adapter classes inside device handlers
- `ctx.settings` — never import settings as a global

## Testing — Mandatory Rules

```python
# conftest.py
pytest_plugins = ["cosalette.testing._plugin"]
# Fixtures: mock_mqtt · fake_clock · device_context
```

**Never patch `time.monotonic`, `asyncio.sleep`, or `time.sleep` globally.**
asyncio uses these internally; global patches corrupt loop timing (Python 3.14+ fails with an infinite loop / timeout).

| Layer | Tool |
|-------|------|
| Domain (pure functions, parsers) | Plain pytest — zero cosalette imports |
| Device handler | `device_context` fixture |
| Full MQTT round-trip | `AppHarness.create()` |

Device coroutines call `ctx.sleep(N)` — the `fake_clock` fixture intercepts this, advancing
virtual time with no wall-clock delay.

When domain code holds a bare `time_module` reference, swap the **module object**, not the attribute:

```python
import myapp.domain.device as mod
mod.time_module = fake_time_module   # ✓ only intercepts calls through this module

# NOT: mock.patch("myapp.domain.device.time_module.monotonic", ...)  # ✗ patches globally
```

See `cosalette ai help testing`.

## Configuration

```python
from pydantic_settings import SettingsConfigDict

class MySettings(cosalette.Settings):
    poll_interval: float = 30.0
    model_config = SettingsConfigDict(env_prefix="MYAPP_", env_nested_delimiter="__")
```

Built-in MQTT settings include `mqtt.tls`, `mqtt.tls_ca_file`, and mutual-TLS
`mqtt.tls_cert_file`/`mqtt.tls_key_file` for broker TLS on port 8883.

See `cosalette ai help configuration`.

## Typed Handler Contracts

Annotate parameters with Pydantic models for automatic parse/validate/serialize:

```python
from typing import Annotated
from pydantic import BaseModel
from cosalette.di import Depends
from cosalette.mqtt import Payload, Topic, Message

class Cmd(BaseModel):
    position: int

class State(BaseModel):
    position: int

@app.command("valve")
async def handle(
    cmd: Annotated[Cmd, Payload()],       # parsed from MQTT JSON
    topic: Annotated[str, Topic()],       # full topic string
    audit: Annotated[Logger, Depends(get_logger)],  # sync dep
) -> State:                               # serialized via Pydantic
    return State(position=cmd.position)
```

Raw escape hatch: `payload: str` (by name) or `Annotated[str, Payload(raw=True)]`.

Triggerable typed payload: `Annotated[Model | None, Payload()]` — `None` on scheduled runs.

Return normalization: return annotation → `state_model` → dict (as-is); primitive/list → `{"value": ...}`.

Errors: `PayloadValidationError`, `ReturnValidationError` — caught and published to error topic.

See `cosalette ai help contracts`.

## Ports & Adapters

```python
# String path → lazy import (hardware libs absent on dev machines)
app.adapter(SensorPort, "myapp.adapters:SensorAdapter", dry_run="myapp.adapters:DryRunAdapter")
```

Domain layer must never import cosalette or adapters. See `cosalette ai help architecture`.

## AsyncAPI Manifest

Introspect app registrations as JSON or table:

```bash
cosalette manifest myapp.main:app           # JSON (parseable by tooling)
cosalette manifest myapp.main:app --table   # human-readable table
```

Decorator metadata (summary, state_model, payload_model, behavior, effects) appears in manifest.
Code generators and doc tooling can consume this for canonical AsyncAPI schemas.

See `cosalette ai help manifest`, `cosalette ai help contracts`.

---

Refresh this file: `cosalette ai init`
Inspect registrations: `cosalette manifest myapp.main:app [--table]`
