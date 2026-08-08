---
icon: material/file-document-check
---

# Contract-First Route Design

`@app.telemetry`, `@app.command`, `@app.device`, `@app.stream`, and
`@app.periodic` each act as **contract declarations** — machine-readable
descriptions of what a device produces and what it accepts. Adding contract
metadata turns `main.py` into an auditable, declarative interface document that
humans and AI coding assistants can inspect without reading implementation code.

The pattern is directly analogous to FastAPI's route decorators: just as
`@app.get("/items", response_model=Item, summary="List items")` declares both
the route and its schema, a cosalette registration declares both the MQTT topic
wiring and the data contract.

## Declaring Contract Metadata

The five registration decorators that accept contract fields have asymmetric
accepted subsets:

| Decorator | `summary` | `state_model` | `payload_model` | `behavior` | `effects` |
| --------- | :-------: | :-----------: | :-------------: | :--------: | :-------: |
| `@app.telemetry` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `@app.command`   | ✓ | ✓ | ✓ | ✓ | ✓ |
| `@app.device`    | ✓ | ✓ | ✓ | ✓ | ✓ |
| `@app.stream`    | ✓ | ✓ | — | ✓ | ✓ |
| `@app.periodic`  | ✓ | — | — | ✓ | — |

`@app.state`, `@app.react`, and `@app.on_configure` accept **no contract
fields** — they are DI wiring and lifecycle hooks, not MQTT contract surfaces.

On `Router`, every operation decorator additionally accepts `tags=` (accumulates
with router constructor and `include_router` tags) — see
[Typed Contracts with Router](#typed-contracts-with-router).

`summary`, `behavior`, and `effects` are **introspection metadata** — surfaced by
the manifest and MCP tools with no runtime effect. `payload_model` is likewise
documentation and tooling only. For streams and periodic tasks the surfacing point
is the registry snapshot rather than AsyncAPI — see
[Streams and Periodic Tasks](#streams-and-periodic-tasks).

`state_model` is **runtime load-bearing on every publishing archetype**. One rule
covers all of them: *if you declare `state_model`, published state is validated.*
For `@app.telemetry` and `@app.command` that means the handler return value; for
`@app.device` and `@app.stream`, which have no return value, it means every
`ctx.publish_state()` payload — see [Validated Published State](#validated-published-state)
and [Typed Payloads and Returns](#typed-payloads-and-returns) below.

`@app.periodic` deliberately has no `state_model`, `payload_model`, or `effects`:
periodic tasks have no MQTT presence at all (ADR-041).

## Typed Payloads and Returns

Runtime type contracts let the framework parse, validate, and serialize values
automatically — no manual `json.loads` / `json.dumps` in handlers.

### Imports

```python
from cosalette.di import Depends
from cosalette.mqtt import Payload, Topic, Message
```

These are also re-exported from the top-level `cosalette` package:

```python
import cosalette
# cosalette.Depends, cosalette.Payload, cosalette.Topic, cosalette.Message
# cosalette.PayloadValidationError, cosalette.ReturnValidationError
```

### Typed Command Handler

When a parameter is annotated with a Pydantic model, the framework parses the
MQTT payload JSON into that model before calling the handler. A non-`None`
return is serialized using the return annotation first, then `state_model` as
fallback; plain `dict` publishes as-is; primitive / list values are wrapped as
`{"value": ...}`.

```python title="main.py"
from __future__ import annotations
from typing import Annotated
from pydantic import BaseModel
import cosalette
from cosalette.di import Depends
from cosalette.mqtt import Payload, Topic

class ValveCommand(BaseModel):
    position: int  # 0–100

class ValveState(BaseModel):
    position: int
    flow_lpm: float

def get_audit_logger() -> AuditLogger:  # synchronous dependency
    return AuditLogger()

@app.command(
    "valve",
    summary="Open/close irrigation valve",
    state_model=ValveState,
)
async def handle_valve(
    cmd: Annotated[ValveCommand, Payload()],  # (1)!
    full_topic: Annotated[str, Topic()],      # (2)!
    audit: Annotated[AuditLogger, Depends(get_audit_logger)],  # (3)!
) -> ValveState:                              # (4)!
    driver = ...
    await driver.set_position(cmd.position)
    audit.record(full_topic, cmd)
    return ValveState(position=cmd.position, flow_lpm=await driver.read_flow())
```

1. `Annotated[ValveCommand, Payload()]` parses MQTT payload JSON into `ValveCommand`.
   A parameter named `payload` with model annotation also works without `Payload()`.
2. `Annotated[str, Topic()]` binds the full MQTT topic string.
3. `Depends(fn)` injects the result of a synchronous factory — nested deps supported.
   Async factories are rejected: `async def`, an async `__call__`, and a sync
   callable that returns a coroutine all raise `TypeError`.
4. Returning `ValveState` is serialized via Pydantic TypeAdapter / JSON-mode serialization before publishing.

**Raw escape hatch** — when you need the plain string:

```python
async def handle(payload: str) -> dict[str, object]: ...          # by name → always raw
async def handle(cmd: Annotated[str, Payload(raw=True)]) -> ...: ...  # explicit raw
```

### Typed Triggerable Telemetry

A triggerable handler can declare `Annotated[Model | None, Payload()]` — the
payload is parsed on triggered runs; scheduled runs bind `None` when the type
is optional:

```python title="main.py"
from __future__ import annotations
from typing import Annotated
from pydantic import BaseModel
from cosalette.mqtt import Payload

class RefreshCommand(BaseModel):
    days: int = 7

@app.telemetry(
    "climate",
    interval=300,
    triggerable=True,
    summary="Temperature and humidity from I2C sensor",
    state_model=SensorReading,
)
async def climate(
    cmd: Annotated[RefreshCommand | None, Payload()],  # None on scheduled runs
) -> SensorReading:
    days = cmd.days if cmd is not None else 7
    return SensorReading(celsius=read_temp(), humidity=read_rh())
```

### Telemetry with Full Metadata

```python title="main.py"
from pydantic import BaseModel
import cosalette

class SensorReading(BaseModel):
    celsius: float
    humidity: float

class RefreshCommand(BaseModel):
    days: int = 7

@app.telemetry(
    "climate",
    interval=cosalette.setting_ref("poll_interval"),
    triggerable=True,
    summary="Temperature and humidity from the I2C sensor",
    state_model=SensorReading,
    payload_model=RefreshCommand,       # accepted on /set when triggerable
    behavior=["reads I2C bus", "applies PT1 low-pass filter"],
    effects=["updates HA dashboard state"],
)
async def climate(ctx: cosalette.DeviceContext) -> dict[str, object]:
    sensor = ctx.adapter(ClimatePort)
    return {"celsius": sensor.read_temp(), "humidity": sensor.read_rh()}
```

### Command with Full Metadata

```python title="main.py"
from pydantic import BaseModel

class ValveCommand(BaseModel):
    position: int  # 0–100

class ValveState(BaseModel):
    position: int
    flow_lpm: float

@app.command(
    "valve",
    summary="Opens or closes the irrigation valve",
    payload_model=ValveCommand,
    state_model=ValveState,
    behavior=["validates position range", "logs to audit trail"],
    effects=["mutates valve position", "triggers flow sensor update"],
)
async def handle_valve(
    payload: ValveCommand, ctx: cosalette.DeviceContext
) -> dict[str, object]:
    driver = ctx.adapter(ValvePort)
    await driver.set_position(payload.position)
    return {"position": payload.position, "flow_lpm": await driver.read_flow()}
```

### Device with Metadata

`@app.device` accepts the same contract metadata as telemetry and command — including
`state_model` and `payload_model`. `state_model` types the device's state channel in
the AsyncAPI schema (resolution: explicit `state_model` → return annotation →
`{"type": "object"}`) **and** validates every `ctx.publish_state()` payload at runtime
(see [Validated Published State](#validated-published-state)). `payload_model` is stored
in the manifest for API symmetry but is **introspection-only for devices**: no device
`/set` channel is emitted, so `payload_model` does not affect schema output today.

```python title="main.py"
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FrameState:
    sensor_id: str
    rssi: int


@app.device(
    "receiver",
    summary="Read sensor frames from serial port and publish per-sensor state",
    state_model=FrameState,
    behavior=[
        "opens serial port at startup",
        "reads LaCrosse protocol frames in a loop",
        "publishes per-sensor state through a sub-entity per discovered sensor",
    ],
    effects=["publishes to {name}/{sensor_id}/state for each discovered sensor"],
)
async def receiver(ctx: cosalette.DeviceContext):
    port = ctx.adapter(SerialPort)
    async for frame in port.read_frames():
        await ctx.sub_entity(frame.sensor_id).publish_state(frame.to_state())
        yield
```

!!! note "Sub-entities are not validated"

    This handler publishes only through `ctx.sub_entity(...)`, so `state_model` here
    stays documentation. Runtime validation covers the device's own static
    `{prefix}/{name}/state` topic — see the next section.

## Validated Published State

`@app.device` and `@app.stream` handlers have no return value for the framework to
validate: they publish by calling `ctx.publish_state()`. Declaring `state_model`
makes those calls load-bearing — each payload is validated and normalized against
the model, and a mismatch raises `ReturnValidationError`.

```python title="main.py"
from pydantic import BaseModel


class SensorReading(BaseModel):
    celsius: float
    humidity: float


@app.device("thermostat", state_model=SensorReading)
async def thermostat(ctx: cosalette.DeviceContext):
    await ctx.publish_state({"celsius": 21.5, "humidity": 58.0})  # (1)!
    await ctx.publish_state({"celsius": 21.5})                    # (2)!
    yield


@app.stream("readings", state_model=SensorReading)
async def readings(stream: cosalette.Stream[SensorReading], ctx: cosalette.DeviceContext):  # (3)!
    async for reading in stream:
        await ctx.publish_state({"celsius": reading.celsius, "humidity": reading.humidity})
        yield
```

1. Validated, then normalized through the model before publishing.
2. Raises `ReturnValidationError: Published state does not match state_model
   'SensorReading' in handler 'main.thermostat': humidity: type=missing`.
3. Stream handlers are async generators yielding `None`, so there is no return
   annotation to infer a contract from — `state_model` is the only source.

Scope and caveats:

- **Only the static `{prefix}/{name}/state` topic is covered.** `ctx.publish()` and
  `ctx.sub_entity(...)` channels are deliberate escape hatches and stay unvalidated.
- **Validation normalizes.** Field aliases, custom serializers, and type coercion
  apply, so an `int` `3` for a `float` field goes on the wire as `3.0`.
- **Errors are safe to log.** They name the offending field paths, the model, and the
  handler, and never echo the rejected payload.
- **`state_model=None` (the default) skips the path entirely** — no `TypeAdapter` is
  built and nothing is added per publish.

!!! warning "Breaking change in 0.6.0"

    Before 0.6.0, `state_model` on `@app.device` only typed the AsyncAPI state
    channel. A device handler that declared `state_model` and published a
    non-conforming payload published it silently; it now raises. Fix the payload to
    match the model, or drop `state_model=` to keep publishing unvalidated. Handlers
    that never declared `state_model` are unaffected.

    The rationale and the decision to apply this to both decorators are recorded in
    ADR-045's 2026-08-07 amendment.

## Inspectable Settings Bindings

Using a raw lambda for `interval` hides the setting name from the manifest:

```python
# Opaque — manifest shows "<deferred>", tooling cannot resolve the field name
@app.telemetry("sensor", interval=lambda s: s.poll_interval)
async def sensor() -> dict[str, object]: ...
```

`setting_ref("field_name")` wraps the same callable but **preserves the field
name** so it appears in the manifest output:

```python
# Inspectable — manifest shows interval: poll_interval (field name)
@app.telemetry("sensor", interval=cosalette.setting_ref("poll_interval"))
async def sensor() -> dict[str, object]: ...
```

`setting_ref` also works for `enabled`:

```python
@app.telemetry(
    "magnetometer",
    interval=cosalette.setting_ref("poll_interval"),
    enabled=cosalette.setting_ref("enable_magnetometer"),
)
async def magnetometer() -> dict[str, object]: ...
```

The `SettingRef` type is exported from `cosalette` — use it for type annotations
if you build tooling around the registry snapshot.

## The Read/Write Split Pattern

A telemetry registration and a command registration can share the same device
name. They use different MQTT topic suffixes (`/state` vs `/set`), and the
framework creates a shared `DeviceContext` for both.

This is the canonical way to model a **resource with separate read and write
paths**:

```python title="main.py"
import cosalette

app = cosalette.App(name="gas2mqtt", version="1.0.0")


@app.telemetry(
    "gas_counter",
    interval=cosalette.setting_ref("poll_interval"),
    triggerable=True,
    summary="Current gas meter impulse count",
    state_model=GasCounterState,
)
async def read_gas_counter(ctx: cosalette.DeviceContext) -> dict[str, object]:
    """Poll impulse count; also fires immediately on /set trigger."""
    meter = ctx.adapter(GasMeterPort)
    return {"impulses": meter.read_impulses()}


@app.command(
    "gas_counter",               # same name — distinct MQTT suffix
    summary="Reset or adjust the impulse counter",
    payload_model=GasCounterCommand,
    state_model=GasCounterState,
    behavior=["validates offset bounds", "writes to non-volatile storage"],
    effects=["mutates persisted counter value"],
)
async def write_gas_counter(
    payload: GasCounterCommand, ctx: cosalette.DeviceContext
) -> dict[str, object]:
    """Accept counter mutations — reset or offset adjustment."""
    meter = ctx.adapter(GasMeterPort)
    await meter.set_offset(payload.offset)
    return {"impulses": meter.read_impulses()}


app.run()
```

**Topic layout for this pair:**

| Topic                          | Direction | Handler              |
| ------------------------------ | --------- | -------------------- |
| `gas2mqtt/gas_counter/state`   | outbound  | telemetry publishes  |
| `gas2mqtt/gas_counter/set`     | inbound   | command subscribes   |

### Triggerable vs. Read/Write Split

These are different patterns — do not conflate them:

| Pattern | What it does |
| ------- | ------------ |
| `triggerable=True` on `@app.telemetry` | A message on `/set` **re-fires the read handler** immediately — the value returned is still produced by the telemetry function. No mutation. |
| `@app.telemetry` + `@app.command` sharing a name | The telemetry handler **reads** state; the command handler **writes** state. Different code paths, distinct contracts. |

Use `triggerable=True` when the client wants a fresh reading on demand.
Use the read/write split when the client wants to **mutate** the resource.

## Viewing the Manifest

The `cosalette manifest` command prints the canonical AsyncAPI 3.0.0 contract
for an app without running it:

```bash
# JSON output — full AsyncAPI document
cosalette manifest myapp.main:app

# Human-readable table
cosalette manifest myapp.main:app --table
```

Both forms call `app.asyncapi()` under the hood. The JSON output is a complete
AsyncAPI 3.0.0 document with typed payload schemas, operations, and contract
metadata. Abbreviated example for a `thermo2mqtt` temperature/pressure sensor
with a read/write thermostat setpoint:

```json
{
  "asyncapi": "3.0.0",
  "info": {
    "title": "thermo2mqtt",
    "version": "1.0.0",
    "x-cosalette-contract-version": "1"
  },
  "channels": {
    "temperatureState": {
      "address": "thermo2mqtt/temperature/state",
      "x-cosalette-app": "thermo2mqtt",
      "messages": {"message": {"payload": {"$ref": "#/components/schemas/TemperatureReading"}}},
      "x-cosalette-archetype": "telemetry",
      "x-cosalette-summary": "Current temperature and pressure readings"
    },
    "setpointCommand": {
      "address": "thermo2mqtt/setpoint/set",
      "x-cosalette-app": "thermo2mqtt",
      "messages": {"message": {"payload": {"$ref": "#/components/schemas/SetpointCommand"}}},
      "x-cosalette-archetype": "command",
      "x-cosalette-summary": "Update the target temperature setpoint"
    }
  },
  "operations": { "..." : "..." },
  "components": {
    "schemas": {
      "TemperatureReading": { "..." : "..." },
      "SetpointCommand": { "..." : "..." }
    }
  }
}
```

**Schema inference priority** (explicit wins over annotated):

| Registration field | Wins over |
|--------------------|-----------|
| `state_model=` on decorator | handler return-type annotation |
| `payload_model=` on decorator | `Annotated[T, Payload()]` / `payload: T` convention |

!!! note "Module-level code runs"

    `cosalette manifest` imports the app module to resolve registrations.
    Any code at module level (outside functions) runs at import time — the
    same behaviour as `cosalette_inspect_app` in the MCP server.

### Streams and Periodic Tasks

The AsyncAPI document covers telemetry, commands, and devices only. Streams and
periodic tasks are deliberately excluded:

- **Streams** publish to the same static `{prefix}/{name}/state` topic a device
  does, but `x-cosalette-archetype` is a closed enum (`telemetry` / `command` /
  `device`) validated by the schema loader, so a stream channel has no
  representable archetype and older cosalette versions would reject a document
  containing one. AsyncAPI here is not documentation — it is the artifact
  `cosalette schema check` gates against and `cosalette schema ha-discovery`
  derives Home Assistant entities from, so emitting stream channels would
  silently create HA entities on the next regeneration. Adding a fourth
  archetype is a defensible future change that needs its own ADR.
- **Periodic tasks** have no MQTT presence at all (ADR-041).

Their contract metadata surfaces in the **registry snapshot** instead — a flat
view of the registrations themselves:

```python
import cosalette
from myapp.main import app

snapshot = cosalette.build_registry_snapshot(app)
snapshot["streams"]   # name, enabled, is_root, maxsize, backpressure,
                      # summary, state_model, behavior, effects, dependencies
snapshot["periodic"]  # name, interval, enabled, has_init, summary, behavior

print(cosalette.format_registry_table(snapshot))  # human-readable tables
```

The same structure is returned by the `cosalette_inspect_app` MCP tool. The
exclusion of stream channels from AsyncAPI is recorded in ADR-045's 2026-08-07
amendment; adding a fourth archetype is tracked in **cos-qzu5**.

## MCP Integration

AI coding assistants that use the cosalette MCP server can call
`cosalette_manifest` to retrieve the same AsyncAPI document programmatically:

```
cosalette_manifest("myapp.main:app")
```

Both the CLI and MCP tool call `app.asyncapi()` — the output is identical.
Use it to answer questions like "what topics does this app subscribe to?" or
"what payload does the valve command expect?" without reading implementation code.

---

## Typed Contracts with Router

All contract fields from the matrix above work identically on `Router`. In
addition, every router operation decorator (`@router.telemetry`,
`@router.command`, `@router.device`, `@router.stream`, `@router.periodic`)
accepts a `tags=` keyword argument that is not available on the corresponding
`@app.*` decorator. Tags accumulate with the router constructor's `tags` and
`include_router` tags. This includes `@router.device`, which accepts
`state_model=` and `payload_model=` with the same semantics as `@app.device`
(see [Device with Metadata](#device-with-metadata) above).

```python title="valves.py — router module with full contracts"
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

router = cosalette.Router(prefix="valves", tags=["irrigation"])


@router.command(
    "main",
    summary="Control main irrigation valve",
    payload_model=ValveCommand,
    state_model=ValveState,
    behavior=["validates position range 0–100", "logs to audit trail"],
    effects=["mutates valve position", "triggers flow sensor update"],
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

```python title="main.py"
import cosalette
from valves import router as valves_router

app = cosalette.App(name="home2mqtt", version="1.0.0")
app.include_router(valves_router)
```

The manifest output (`app.asyncapi()`) includes all contract metadata from router
operations, with topics prefixed correctly:

- Subscribe: `home2mqtt/valves/main/set`
- Publish: `home2mqtt/valves/main/state`

See [Router Composition](router-composition.md) for multi-module organization patterns.

---

## See Also

- [Router Composition](router-composition.md) — multi-module apps with typed contracts
- [Telemetry Device](telemetry-device.md) — polling loops and publish strategies
- [Command & Control Device](command-device.md) — `@app.command` handler patterns
- [Device Archetypes](../concepts/device-archetypes.md) — choosing the right decorator
- [MCP Server](mcp-server.md) — AI assistant integration
