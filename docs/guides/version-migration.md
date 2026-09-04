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

### New: local triggers on `@app.device`

`@app.device` accepts `triggerable="local"` too, so a device entity can publish
the instant something happens instead of on its next poll. A device owns its own
loop, so instead of the framework racing an `interval=` on its behalf the handler
awaits an injected `DeviceTrigger` (ADR-065):

```python
from cosalette import DeviceContext, DeviceTrigger


@app.device("gadget", triggerable="local")
async def gadget(ctx: DeviceContext, trigger: DeviceTrigger) -> AsyncIterator[None]:
    while True:
        # Wakes on notify("gadget"); the timeout is a heartbeat so the
        # retained state topic still refreshes if the hardware goes quiet.
        await trigger.wait(timeout=60.0)
        await ctx.publish_state(read_gadget())
        yield
```

Two differences from telemetry are worth noting:

- Devices accept `triggerable="local"` **only**. `{prefix}/{device}/set` is
  already the device's command topic, so `True`/`"mqtt"`/`"both"` are rejected at
  registration time — handle those messages with `ctx.on_command()` instead.
- `triggerable=` and the `DeviceTrigger` parameter must agree. Declaring either
  one without the other raises at registration rather than silently never waking.

This is purely additive: existing `@app.device` registrations are unaffected.

### New: `min_interval=` storm throttle

A push source can wake a handler far faster than the handler is useful. Before
0.8.0 the only remedy was a rate limit at the `notify()` call site. `@app.telemetry`
and `@app.device` now accept `min_interval=` (ADR-066), which bounds the minimum
spacing between trigger-initiated run **starts**:

```python
# Before (≤ 0.7.x) — hand-rolled dedup at the call site
def _on_reading(value: float) -> None:
    if value != _last_value:      # manual guard against a push storm
        notify("pressure")


# After (0.8.0+) — the framework throttles, nothing is dropped
@app.telemetry("pressure", interval=300, triggerable="local", min_interval=2.0)
async def pressure(trigger: cosalette.TriggerPayload) -> dict[str, object]:
    return {"bar": await read_pressure()}
```

The throttle is leading edge plus trailing edge: the first wake after a quiet
period runs immediately, wakes arriving inside the window coalesce into exactly
one run when it reopens, and that run carries the **last** payload. `interval=`
heartbeats are never throttled and never consume a held wake.

`min_interval=` requires `triggerable=` and must be a finite, strictly positive
number of seconds; both mistakes raise `ValueError` at registration. The default
`None` leaves existing behaviour byte-for-byte unchanged, so this is purely
additive. See
[Throttling a Trigger Storm](telemetry-advanced.md#throttling-a-trigger-storm).

---

## `state_model=` Return-Value Enforcement (v0.9.0+)

**Breaking.** ADR-068 makes the documented rule unconditional: *if you declare
`state_model`, published state is validated* — on all four publishing archetypes.
Through 0.8.x that only held for `@app.device` / `@app.stream`. Five things change.

### 1. Non-conforming payloads now fail at boot

A `@app.telemetry` / `@app.command` handler whose payload never matched its declared
`state_model` raises `ReturnValidationError` on the first cycle after upgrade —
usually a missing required field. The error is published to `{prefix}/{name}/error`
and the state publish is suppressed, so the retained state topic goes stale instead
of carrying a bad payload.

```python
class Reading(BaseModel):
    sensor: str
    value: float

@app.telemetry("rx", interval=30, state_model=Reading)
async def rx():
    return {"sensor": "a"}      # 0.8.x: published verbatim. 0.9.0: raises.
```

Migration — one of two one-line choices:

- **Fix the payload** so it matches the model (add the missing field), or
- **Drop `state_model=`** to go back to unvalidated publishing.

### 2. `state_model=` outranks the return annotation

`normalize_handler_return` resolved `get_return_annotation(func) or state_model`;
it now resolves `state_model or get_return_annotation(func)` (clause A). A handler
declaring **both**, with different types, changes behaviour: the annotation used to
govern, `state_model=` governs now.

```python
# 0.8.x: dict[str, object] wins — TypeAdapter accepts anything, nothing is validated
# 0.9.0: Reading wins — the payload is validated
@app.telemetry("rx", interval=30, state_model=Reading)
async def rx() -> dict[str, object]: ...
```

### 3. The wire payload omits `None` instead of publishing `null`

Validated payloads dump with `exclude_none=True` on **every** archetype (clauses C
and D), so an optional field the handler left out is an **absent key**, not an
explicit `null`. This changes the `@app.device` / `@app.stream` wire payload for any
`state_model` with optional fields:

```json
// 0.8.x
{"sensor": "a", "brightness": null}
// 0.9.0
{"sensor": "a"}
```

Migration: update any consumer that reads those keys — Home Assistant
`value_template`s in particular (a template that assumed the key is always present
now needs `value_json.get('brightness')` or a `default`), plus retained-topic
snapshots and exact-payload contract tests. It is also no longer possible to publish
a deliberate `null` through a `state_model`.

### 4. The registration warning is an error under `filterwarnings = ["error"]`

Clause F emits a `UserWarning` at registration when `state_model=M` and the return
annotation name different types. Under pytest's `filterwarnings = ["error"]` — a
common strict-test configuration, and the one this repository uses — that warning is
an **error**, so affected tests fail on upgrade. It broke 75 tests in cosalette's own
suite, every one a genuine `state_model=M` + `-> dict[str, object]` contradiction.

Migration: remove the loose return annotation and leave `state_model=` as the sole
contract.

```python
# Before — warns (and fails under filterwarnings = ["error"])
@app.telemetry("rx", interval=30, state_model=Reading)
async def rx() -> dict[str, object]: ...

# After
@app.telemetry("rx", interval=30, state_model=Reading)
async def rx(): ...
```

### 5. `-> None` and `-> M | None` stay silent

These are not contradictions and are unaffected. `-> M | None` is the same contract
with a suppress-publish case; `-> None` promises no return value at all, so clause A
never overrides anything — `state_model=` there is **channel metadata**, and it is
what gives an `@app.command` its AsyncAPI state channel. Keep it.

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
co-located with the handler signature. Do not declare **both** with different types:
since 0.9.0 `state_model=` also outranks the return annotation at runtime, and the
combination warns at registration. See
[`state_model=` Return-Value Enforcement](#state_model-return-value-enforcement-v090).

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
6. **Audit every `state_model=`** (v0.9.0+) — confirm the payload really matches the
   model, and drop return annotations that disagree with it
7. **Regenerate AsyncAPI contracts** — `cosalette schema dump > asyncapi.yaml`
8. **Run quality gates** — `task check` (lint + typecheck + tests)
9. **Update downstream consumers** — if MQTT topics changed due to Router prefixes,
   or if a `state_model` with optional fields now omits keys it used to publish as
   `null` (v0.9.0+)

---

## Getting Help

- **AI agent support:** `cosalette ai help <topic>` — topics: `contracts`, `router`, `testing`, `architecture`
- **GitHub Discussions:** [ff-fab/cosalette/discussions](https://github.com/ff-fab/cosalette/discussions)
- **Issues:** [ff-fab/cosalette/issues](https://github.com/ff-fab/cosalette/issues)
