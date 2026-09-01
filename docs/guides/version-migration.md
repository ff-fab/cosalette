---
icon: material/transfer
---

# Migrate Between cosalette Versions

This guide covers breaking changes and mechanical rewrites when upgrading between
cosalette versions. For AI-assisted migration from non-cosalette IoT apps, see
[AI-Assisted Development](../getting-started/ai-assisted-development.md).

!!! info "Router is opt-in"

    **App-level decorators (`@app.telemetry()`, `@app.command()`, `@app.device()`)
    remain first-class.** Router is for multi-module composition in production apps,
    not a forced migration. Small, single-file applications should continue using
    app-level decorators directly.

---

## Router Composition (v0.2.0+)

### When to Adopt Router

| Pattern              | Use when                                                      |
| -------------------- | ------------------------------------------------------------- |
| App-level decorators | Single-file apps, quickstart examples, simple bridges (≤3 devices) |
| Router composition   | Multi-module projects, shared libraries, testable boundaries  |

**Router is not `Router.include_router` — it's single-level composition only.**

### Mechanical Migration

**Before** — direct app-level registration:

```python title="main.py"
import cosalette

app = cosalette.App(name="home2mqtt", version="1.0.0")


@app.telemetry("temperature", interval=30)
async def read_temperature() -> dict[str, object]:
    return {"celsius": 22.5}
```

**After** — Router module with `app.include_router()`:

```python title="sensors.py"
import cosalette

router = cosalette.Router(prefix="sensors", tags=["environment"])


@router.telemetry("temperature", interval=30)
async def read_temperature() -> dict[str, object]:
    return {"celsius": 22.5}
```

```python title="main.py"
import cosalette
from sensors import router as sensors_router

app = cosalette.App(name="home2mqtt", version="1.0.0")
app.include_router(sensors_router)
```

**MQTT topic changes:**

- Before: `home2mqtt/temperature/state`
- After: `home2mqtt/sensors/temperature/state`

The `prefix="sensors"` parameter adds a topic segment. Omit `prefix` to keep original topics.

See [Router Composition](router-composition.md) for multi-module organization patterns.

---

## Typed Payloads and Returns (v0.4.0+)

### Raw String → Pydantic Models

**Before** — raw string payload:

!!! warning "Untrusted input"

    Manual `json.loads` + direct field access provides no type safety or
    validation. For handlers that receive user-controlled MQTT payloads, prefer
    the `Annotated[T, Payload()]` approach below.

```python
@app.command("valve")
async def handle_valve(payload: str, ctx: cosalette.DeviceContext) -> None:
    import json
    data = json.loads(payload)
    position = data["position"]
    # ... driver logic ...
    await ctx.publish_state(json.dumps({"position": position}))
```

**After** — typed payloads with `Annotated[T, Payload()]`:

```python
from typing import Annotated
from pydantic import BaseModel
from cosalette.mqtt import Payload


class ValveCommand(BaseModel):
    position: int  # 0–100


class ValveState(BaseModel):
    position: int
    flow_lpm: float


@app.command("valve")
async def handle_valve(
    cmd: Annotated[ValveCommand, Payload()],
) -> ValveState:
    # ... driver logic ...
    return ValveState(position=cmd.position, flow_lpm=2.3)
```

**Raw escape hatch** — when you need the unmodified string:

```python
# By parameter name convention
async def handler(payload: str) -> dict[str, object]: ...

# Or with explicit marker
async def handler(
    raw: Annotated[str, Payload(raw=True)]
) -> dict[str, object]: ...
```

See [Contract-First Route Design](contract-first-route-design.md) for full patterns.

### Triggerable Telemetry with Typed Payloads

**Before** — `TriggerPayload` data wrapper:

```python
from cosalette.contracts import TriggerPayload


@app.telemetry("sensor", interval=300, triggerable=True)
async def sensor(trigger: TriggerPayload) -> dict[str, object]:
    if trigger.is_triggered:
        days = int(trigger.data or "7")
    else:
        days = 7
    return {"data": await read_sensor(days=days)}
```

**After** — `Annotated[Model | None, Payload()]`:

```python
from typing import Annotated
from pydantic import BaseModel
from cosalette.mqtt import Payload


class RefreshCommand(BaseModel):
    days: int = 7


@app.telemetry("sensor", interval=300, triggerable=True)
async def sensor(
    cmd: Annotated[RefreshCommand | None, Payload()],
) -> dict[str, object]:
    days = cmd.days if cmd is not None else 7
    return {"data": await read_sensor(days=days)}
```

On scheduled runs, `cmd` is `None`. On triggered runs, it holds the validated model.

---

## Trigger Sources (v0.8.0+)

`triggerable=` widened from a `bool` flag into a **trigger-source declaration**
(ADR-064). `True`/`False` still work and mean exactly what they always did, so
no application code has to change.

| Value             | Arms on `{prefix}/{device}/set` | Arms on `EntityNotifier` call |
| ----------------- | ------------------------------- | ----------------------------- |
| `False` (default) | no                              | no                            |
| `True` / `"mqtt"` | yes                             | no                            |
| `"local"`         | no                              | yes                           |
| `"both"`          | yes                             | yes                           |

`interval=` is still required for every triggerable entity — it is the heartbeat
and the fallback poll when no trigger arrives.

### Breaking change: `TelemetryRegistration.triggerable`

The introspection field is no longer a `bool`. It is now
`TriggerSource | None` — `"mqtt"`, `"local"`, `"both"`, or `None`:

```python
# Before (≤ 0.7.x)
if reg.triggerable:          # bool
    ...

# After (0.8.0+)
if reg.triggerable is not None:              # any trigger source
    ...
if reg.triggerable in ("mqtt", "both"):      # subscribes /set
    ...
```

Truthiness is unchanged for the common case (`None` is falsy, every source
string is truthy), so `if reg.triggerable:` keeps working; only code that
compared against `True`/`False` or annotated the field as `bool` needs updating.

The manifest gained a matching `trigger_source` field; its existing
`triggerable` field stays a `bool` and is unchanged.

### New: local (in-process) triggers

`triggerable="local"` wakes a telemetry entity from your own code instead of
from MQTT, via the new injectable `EntityNotifier`:

```python
import cosalette
from cosalette import EntityNotifier


@app.state
def bus(notify: EntityNotifier) -> SensorBus:
    return SensorBus(on_reading=lambda: notify("pressure"))


@app.telemetry("pressure", interval=300, triggerable="local")
async def pressure(trigger: cosalette.TriggerPayload) -> dict[str, object]:
    return {"bar": await read_pressure(), "why": trigger.source}
```

`TriggerPayload.source` is new and reports `"scheduled"`, `"mqtt"` or
`"local"`. See
[Local (In-Process) Triggers](telemetry-advanced.md#local-in-process-triggers).

**No MQTT, discovery or AsyncAPI output changes.** `triggerable="local"`
subscribes nothing, and no generated artifact reads `triggerable=`.

---

## `payload_model` / `state_model` vs Type Annotations

**Both forms are supported** — explicit decorator metadata wins over annotation inference.

### Explicit Decorator Metadata (v0.1.0+)

```python
@app.command(
    "valve",
    payload_model=ValveCommand,  # inbound /set channel
    state_model=ValveState,      # outbound /state channel
)
async def handle_valve(payload: str) -> None:
    # Handler uses raw strings; schema enforced externally
    ...
```

### Type Annotation Inference (v0.4.0+)

```python
@app.command("valve")
async def handle_valve(
    cmd: Annotated[ValveCommand, Payload()],
) -> ValveState:
    # payload_model inferred from `cmd` parameter annotation
    # state_model inferred from return annotation
    ...
```

**Schema inference priority:**

- **Commands** (inbound `/set`): `payload_model` → injection plan (`Annotated[T, Payload()]` or `payload: T`) → `{"type": "object"}`
- **Commands** (outbound `/state`): `state_model` → return annotation → omitted (no noise for voids)
- **Telemetry/devices**: `state_model` → return annotation → `{"type": "object"}`

`@app.device` now accepts an explicit `state_model=` kwarg (previously only return-annotation
inference was available for devices). `payload_model=` is also accepted on devices but is
**introspection-only** — no device `/set` channel is emitted, so it does not affect schema output.

**Prefer annotation inference for new code** — it's more concise and the schema stays
co-located with the handler signature.

---

## `@app.device` Async Generator Requirement (v0.4.0+)

!!! warning "Breaking change in v0.4.0"

    **`@app.device` handlers must be async generators** — plain coroutines now raise
    `TypeError`. Add `yield` after each unit of work to create reaction boundaries.

**Before** — plain async function (v0.1.0–v0.3.x):

```python
@app.device("valve")
async def valve(ctx: cosalette.DeviceContext) -> None:
    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        await ctx.publish_state({"state": payload})

    await ctx.publish_state({"state": "closed"})
    while not ctx.shutdown_requested:
        await ctx.sleep(30)
```

**After** — async generator with `yield` (v0.4.0+):

```python
@app.device("valve")
async def valve(ctx: cosalette.DeviceContext):
    @ctx.on_command
    async def handle(topic: str, payload: str) -> None:
        await ctx.publish_state({"state": payload})

    await ctx.publish_state({"state": "closed"})
    yield  # Reaction boundary — reactors fire here
    while not ctx.shutdown_requested:
        await ctx.sleep(30)
        yield  # Reaction boundary
```

**Why:** `yield` creates reaction boundaries for domain-event reactors (`@app.react`).
Reactors fire at execution boundaries before the next `ctx.sleep()`.

See the [Shared State guide](shared-state.md) for domain-event reactor patterns.

---

## AsyncAPI and Manifest Introspection

### `app.asyncapi()` (v0.2.0+)

**Canonical AsyncAPI document generation** — replaces older registry introspection wording.

```python
import json
from pathlib import Path

# Generate AsyncAPI 3.0.0 document
doc = app.asyncapi()
Path("asyncapi.yaml").write_text(json.dumps(doc, indent=2))
```

Used by:

- `cosalette schema dump` CLI subcommand
- `cosalette_manifest` MCP tool
- CI/CD contract enforcement

**Document structure:**

- `channels`: MQTT topic definitions with payload schemas
- `operations`: Send/receive operations for each channel
- `components.schemas`: Pydantic model schemas as JSON Schema
- `info.x-cosalette-contract-version`: Contract-shape version (independent from app version)

### Schema Inference Priority

See [`payload_model` / `state_model` vs Type Annotations](#payload_model-state_model-vs-type-annotations) above.

### Exporting for External Tools

```bash
# Generate AsyncAPI YAML for schema enforcement
cosalette schema dump > asyncapi.yaml

# Validate with AsyncAPI CLI
asyncapi validate asyncapi.yaml
```

See [Schema Enforcement](schema-enforcement.md) for contract-first development workflows.

---

## Testing Harness Updates

### `AppHarness.create()` (v0.2.0+)

**Before** — manual app and double wiring:

```python
from cosalette.testing import MockMqttClient, FakeClock

app = cosalette.App(name="testapp", version="1.0.0")
mqtt = MockMqttClient()
clock = FakeClock(0.0)
# ... manual wiring ...
```

**After** — `AppHarness.create()` with test doubles:

```python
from cosalette.testing import AppHarness

harness = AppHarness.create(name="testapp")
# harness.app, harness.mqtt, harness.clock, harness.settings pre-wired
```

### Testing Patterns (v0.2.0+)

| Pattern                     | Method                             | Use when                           |
| --------------------------- | ---------------------------------- | ---------------------------------- |
| Simulate inbound command    | `harness.mqtt.deliver(topic, payload)` | Inject MQTT messages               |
| Assert published messages   | `harness.mqtt.get_messages_for(topic)` | Verify telemetry/command responses |
| Advance time                | `harness.advance_time(seconds)`    | Fast-forward time for interval tests |

**Example:**

```python
import asyncio

import pytest
from cosalette.testing import AppHarness


@pytest.mark.asyncio
async def test_telemetry_publishes_on_interval():
    """Telemetry handler publishes state after interval elapses."""
    harness = AppHarness.create(name="testapp")

    @harness.app.telemetry("sensor", interval=30)
    async def sensor() -> dict[str, object]:
        return {"value": 42}

    # Orchestrate time advancement and shutdown
    async def advance_and_shutdown():
        await harness.advance_time(30)
        harness.trigger_shutdown()

    asyncio.create_task(advance_and_shutdown())
    await harness.run()

    # Assert published message
    messages = harness.mqtt.get_messages_for("testapp/sensor/state")
    assert len(messages) >= 1
    assert '"value": 42' in messages[0][0]
```

### Fixture Conventions (v0.2.0+)

Register the pytest plugin in `conftest.py`:

```python title="tests/conftest.py"
pytest_plugins = ["cosalette.testing._plugin"]
```

This registers three fixtures:

| Fixture          | Type              | Description                           |
| ---------------- | ----------------- | ------------------------------------- |
| `mock_mqtt`      | `MockMqttClient`  | In-memory MQTT double                 |
| `fake_clock`     | `FakeClock`       | Deterministic clock starting at 0     |
| `device_context` | `DeviceContext`   | Pre-wired context with test doubles   |

See [Testing](testing.md) for three-layer test patterns and shared fixture conventions.

---

## Migration Checklist

Before upgrading cosalette:

1. **Review the [CHANGELOG](https://github.com/ff-fab/cosalette/blob/main/CHANGELOG.md)** for your target version
2. **Run tests** — `task test:unit` and `task test:integration`
3. **Update handler signatures** — add `yield` to `@app.device` handlers (v0.4.0+)
4. **Migrate to typed payloads** (optional but recommended) — replace raw strings with Pydantic models
5. **Adopt `AppHarness.create()`** in tests (v0.2.0+)
6. **Regenerate AsyncAPI contracts** — `cosalette schema dump > asyncapi.yaml`
7. **Run quality gates** — `task check` (lint + typecheck + tests)
8. **Update downstream consumers** — if MQTT topics changed due to Router prefixes

---

## Getting Help

- **AI agent support:** `cosalette ai help <topic>` — topics: `contracts`, `router`, `testing`, `architecture`
- **GitHub Discussions:** [ff-fab/cosalette/discussions](https://github.com/ff-fab/cosalette/discussions)
- **Issues:** [ff-fab/cosalette/issues](https://github.com/ff-fab/cosalette/issues)
