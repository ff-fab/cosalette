---
icon: material/file-document-check
---

# Contract-First Route Design

Every `@app.telemetry`, `@app.command`, and `@app.device` registration is also
a **contract declaration** — a machine-readable description of what a device
produces and what it accepts. Adding contract metadata turns `main.py` into an
auditable, declarative interface document that humans and AI coding assistants
can inspect without reading implementation code.

The pattern is directly analogous to FastAPI's route decorators: just as
`@app.get("/items", response_model=Item, summary="List items")` declares both
the route and its schema, a cosalette registration declares both the MQTT topic
wiring and the data contract.

## Declaring Contract Metadata

All three registration decorators accept optional contract fields:

| Parameter       | Type                 | Applies to                     | Description                                    |
| --------------- | -------------------- | ------------------------------ | ---------------------------------------------- |
| `summary`       | `str`                | telemetry, command, device     | Human-readable description                     |
| `state_model`   | `type`               | telemetry, command             | Pydantic model or dataclass for state          |
| `payload_model` | `type`               | command, triggerable telemetry | Expected inbound payload type                  |
| `behavior`      | `list[str]`          | telemetry, command, device     | Ordered description of what the handler does   |
| `effects`       | `list[str]`          | telemetry, command, device     | Side effects and mutations                     |

`summary`, `behavior`, and `effects` are **introspection metadata** — surfaced by
the manifest and MCP tools with no runtime effect. `state_model` and
`payload_model` are also introspection metadata for documentation and tooling,
but typed handler annotations and `state_model` now additionally participate in
**runtime validation and serialization** — see [Typed Payloads and Returns](#typed-payloads-and-returns) below.

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

`@app.device` supports `summary`, `behavior`, and `effects`. It does not accept
`state_model` or `payload_model` because device handlers manage their own
publishing loop rather than returning a typed state snapshot.

```python title="main.py"
@app.device(
    "receiver",
    summary="Read sensor frames from serial port and publish per-sensor state",
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
metadata. Abbreviated example for the `gas2mqtt` read/write pair above:

```json
{
  "asyncapi": "3.0.0",
  "info": {
    "title": "gas2mqtt",
    "version": "1.0.0",
    "x-cosalette-contract-version": "1"
  },
  "channels": {
    "gas_counterState": {
      "address": "gas2mqtt/gas_counter/state",
      "messages": {"message": {"payload": {"$ref": "#/components/schemas/GasCounterState"}}},
      "x-cosalette-archetype": "telemetry",
      "x-cosalette-summary": "Current gas meter impulse count"
    },
    "gas_counterCommand": {
      "address": "gas2mqtt/gas_counter/set",
      "messages": {"message": {"payload": {"$ref": "#/components/schemas/GasCounterCommand"}}},
      "x-cosalette-archetype": "command",
      "x-cosalette-summary": "Reset or adjust the impulse counter"
    }
  },
  "operations": { "..." : "..." },
  "components": {
    "schemas": {
      "GasCounterCommand": { "..." : "..." },
      "GasCounterState": { "..." : "..." }
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

## See Also

- [Telemetry Device](telemetry-device.md) — polling loops and publish strategies
- [Command & Control Device](command-device.md) — `@app.command` handler patterns
- [Device Archetypes](../concepts/device-archetypes.md) — choosing the right decorator
- [MCP Server](mcp-server.md) — AI assistant integration
