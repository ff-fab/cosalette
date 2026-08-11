---
description: 'cosalette framework development guidance for AI agents'
applyTo: '**/*.py'
---

# cosalette Framework Instructions

Quick-reference only. For depth: `cosalette ai help <topic>`
Topics: `telemetry` · `testing` · `configuration` · `architecture` · `commands` · `health` · `scheduling` · `resilience` · `sub-entities` · `triggerable` · `multi-device` · `contracts` · `manifest` · `router` · `migration` · `availability` · `persistence`

## Archetype — Pick One

| Archetype | When | Decorator |
|-----------|------|-----------|
| **Telemetry** | Periodic sensor read / scheduled publish | `@app.telemetry(name, interval=N)` |
| **Command** | Handle inbound MQTT `…/set` payloads | `@app.command(name)` |
| **Device** | Explicit `while` loop / state machine | `@app.device(name)` — async generator |

Default to **telemetry**. Multiple similar devices → `name=lambda s: {…}` dict form (not `@app.on_configure`).
See `cosalette ai help architecture`.

Telemetry key params: `interval=N` (required), `timeout=N` (per-invocation backstop; omit → auto=interval; `timeout=None` → disabled). See `cosalette ai help resilience`.

## Command Handling — Concurrent Per-Entity Dispatch

Commands dispatch **concurrently per entity** by default. The MQTT read loop enqueues messages
onto a per-entity worker queue and returns immediately — it **never awaits user command code**.
Each entity (device/command) gets its own FIFO worker task.

- **Per-entity ordering** — two commands to the same entity execute in FIFO order
- **Cross-entity concurrency** — one entity's slow handler does NOT block other entities
- **No cross-entity ordering** — commands to different entities may complete in any order

```python
@app.command("valve", timeout=5.0, unavailable_on=(TimeoutError,))
async def valve(payload: str) -> dict[str, object]:
    # Does NOT block other entities; TimeoutError after 5s → offline
    await slow_hardware()
    return {"state": payload}


@app.command("relay", maxsize=10, backpressure="drop_oldest")
async def relay(payload: str) -> dict[str, object]:
    return {"state": payload}  # Queue bounded, drops oldest when full
```

Key params:
- `timeout=N` (float | None) — per-invocation backstop via `asyncio.wait_for`; `TimeoutError` flows
  to error topic; composes with `unavailable_on=(TimeoutError,)` to mark device offline on timeout.
  Default `None` (no timeout).
- `maxsize=N` (int) — bound the command queue (default `0` = unbounded). When full, `backpressure=`
  applies.
- `backpressure=` ("drop_newest" | "drop_oldest" | "raise") — what happens when queue is full.
  Default `"drop_newest"`.

`@app.device` accepts the same `timeout=`, `maxsize=`, and `backpressure=` params; they apply to
the device context's internal `ctx.commands()` queue.

`@app.device` with `payload_model=` now emits a **receive channel** in the AsyncAPI contract on
`{prefix}/{device}/set` (archetype: `device`), alongside the existing `/state` send channel.
`payload_model` is **metadata only** — does NOT runtime-validate inbound payloads; only
`state_model` is runtime load-bearing for `ctx.publish_state`. Devices without `payload_model`
unchanged (no `/set` channel emitted).

See `cosalette ai help commands`, ADR-055.

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

Router params: `prefix`, `tags`, `adapters`. There is **no** `dependencies=` on `Router`, `include_router`, or any router decorator — unlike FastAPI's `APIRouter`; passing it raises `TypeError`. Use `Depends()` per handler parameter (see below).

See `cosalette ai help router`, `cosalette ai help migration`.

## `@app.device` — Async Generator (Breaking Change)

`@app.device` handlers **must** be async generators. `yield` marks the reaction boundary:

```python
@app.device("sensor")
async def sensor(ctx: cosalette.DeviceContext):  # no return annotation
    while not ctx.shutdown_requested:
        data = await read_sensor()
        await ctx.publish_state(data)
        yield  # reaction boundary
        await ctx.sleep(30)
```

Plain coroutines (`async def … -> None`) now raise `TypeError`. Remove `-> None` return annotations.

`@app.device` also accepts `state_model=` (types the state channel in AsyncAPI schema **and**,
since 0.6.0, validates every `ctx.publish_state()` payload — see below) and
`payload_model=` (manifest metadata; **introspection-only** — no `/set` channel emitted for devices).

## `state_model=` Validates Published State (Breaking Change, 0.6.0)

One rule across every publishing archetype: **if you declare `state_model`, published state is
validated.** `@app.telemetry`/`@app.command` validate the handler return value; `@app.device` and
`@app.stream` have no return value, so they validate each `ctx.publish_state()` payload against the
model and raise `ReturnValidationError` on a mismatch.

```python
@app.device("valve", state_model=ValveState)
async def valve(ctx: cosalette.DeviceContext):
    await ctx.publish_state({"position": 40, "flow_lpm": 2.5})  # validated + normalized
    yield


@app.stream("readings", state_model=SensorReading)
async def readings(
    stream: cosalette.Stream[SensorReading], ctx: cosalette.DeviceContext
):
    async for reading in stream:
        await ctx.publish_state(
            {"celsius": reading.celsius, "humidity": reading.humidity}
        )
        yield
```

- Breaking for `@app.device` handlers that declared `state_model` and published non-conforming
  payloads — they used to publish silently. Fix the payload, or drop `state_model=`.
- Validation **normalizes**: aliases, custom serializers and coercion apply, so an `int` `3` for a
  `float` field goes on the wire as `3.0`.
- Only the static `{prefix}/{name}/state` topic is covered. `ctx.publish()` and `ctx.sub_entity(...)`
  channels are escape hatches and stay unvalidated.
- Omitting `state_model` (the default) skips the path entirely — no `TypeAdapter`, no per-publish cost.
- Errors name the field paths, the model and the handler, and never echo the rejected payload.

## `@app.react` — Domain-Event Reactors

Use `@app.react` to keep state objects pure domain models. The framework calls the reactor
automatically when the state has pending events — no manual flush calls in handlers:

```python
@app.state
def shared_state() -> SharedState:
    return SharedState()


@app.react(SharedState, drain=lambda s: s.registry.drain_events())
async def on_registry_events(
    events: list[RegistryEvent],  # reserved name — injected by framework
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

# store= is optional: omit → auto-resolved JsonFileStore (<NAME>_STORE_PATH env,
# name upper-cased with non-alphanumeric chars → underscores, e.g. sensor.hub →
# SENSOR_HUB_STORE_PATH → $XDG_STATE_HOME/<name>/store.json).
# Pass store=None to opt out; pass an explicit Store for a custom backend.
# High-write apps: cosalette.set_default_store_backend(SqliteStore) at startup.
# retained_cleanup=False → keep store for persist= but skip ADR-048 cleanup +
# ephemeral warning (self-documenting for @app.on_configure apps that don't vary entities).
app = cosalette.App(name="mybridge", version="0.1.0", settings_class=MySettings)
app.adapter(
    SensorPort, "myapp.adapters:SensorAdapter", dry_run="myapp.adapters:DryRunAdapter"
)


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

mod.time_module = fake_time_module  # ✓ only intercepts calls through this module

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

Config files: set `config_file="app.toml"` in `model_config` or pass `--config-file app.toml` on the CLI. Precedence: `env > .env > config file > defaults`. TOML and JSON use stdlib (no extra); YAML needs `cosalette[config-yaml]`. Secrets (`SecretStr`) belong in env vars — env always wins.

## Typed Handler Contracts

Annotate parameters with Pydantic models for automatic parse/validate/serialize:

```python
from typing import Annotated
from pydantic import BaseModel
from cosalette.di import Depends, Optional
from cosalette.mqtt import Payload, Topic, Message


class Cmd(BaseModel):
    position: int


class State(BaseModel):
    position: int


@app.command("valve")
async def handle(
    cmd: Annotated[Cmd, Payload()],  # parsed from MQTT JSON
    topic: Annotated[str, Topic()],  # full topic string
    audit: Annotated[Logger, Depends(get_logger)],  # sync dep
    store: Annotated[DeviceStore | None, Optional()] = None,  # optional provider
) -> State:  # serialized via Pydantic
    return State(position=cmd.position)
```

Raw escape hatch: `payload: str` (by name) or `Annotated[str, Payload(raw=True)]`.

Triggerable typed payload: `Annotated[Model | None, Payload()]` — `None` on scheduled runs.

Optional injection: `Annotated[T | None, Optional()]` resolves the provider if registered,
else falls back to the parameter default. `param.default` is never read to decide *whether*
to apply optional binding — that requires the `Optional()` marker — but when `Optional()` is
present and no provider resolves, the explicit default is used as the fallback (implicitly
`None`). Bare `T | None` without `Optional()` is rejected.

Return normalization: return annotation → `state_model` → dict (as-is); primitive/list → `{"value": ...}`.

Errors: `PayloadValidationError`, `ReturnValidationError` — caught and published to error topic.

See `cosalette ai help contracts`.

## Transport Availability Signaling

Use `unavailable_on` to automatically mark a device offline when a transport fails:

```python
@app.command("sensor", unavailable_on=(SSHError, TimeoutError))
async def handle_sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    return {"value": await ssh.read()}  # exception → "offline" published + suppressed
```

Or call `ctx.mark_unavailable()` inside the handler body for conditional unavailability.
Auto-recovery: the framework publishes `"online"` after the next successful invocation.
Topic: `{app}/{device}/availability`, values `"online"` / `"offline"` (retained, QoS 1).

Removed entities: the framework automatically clears the retained `state`/`availability`
topics of entities deleted from config on the first MQTT connect (prevents Home Assistant
ghost entities). Works by default — no `store=` wiring needed. Pass `store=None` to
opt out of persistence entirely. Use `retained_cleanup=False` to opt out of only the
ADR-048 cleanup (keeping persistence for `persist=`), vs `store=None` which drops
persistence too. See ADR-048, `cosalette ai help persistence`.

See `cosalette ai help availability`.

## Ports & Adapters

```python
# String path → lazy import (hardware libs absent on dev machines)
app.adapter(
    SensorPort, "myapp.adapters:SensorAdapter", dry_run="myapp.adapters:DryRunAdapter"
)
```

Domain layer must never import cosalette or adapters. See `cosalette ai help architecture`.

## AsyncAPI Manifest

Introspect app registrations as JSON or table:

```bash
cosalette manifest myapp.main:app           # JSON (parseable by tooling)
cosalette manifest myapp.main:app --table   # human-readable table
```

Decorator metadata (summary, state_model, payload_model, behavior, effects) appears in the manifest.
Code generators and doc tooling can consume this for canonical AsyncAPI schemas.

Periodic tasks are **not** in the generated AsyncAPI document: they have no MQTT presence
(ADR-041). Streams now emit an AsyncAPI state channel (`x-cosalette-archetype: stream`, ADR-054);
their registry snapshot entries additionally carry fields AsyncAPI does not (maxsize, backpressure,
dependencies). Contract metadata also surfaces in the **registry snapshot** —
`build_registry_snapshot()` / `format_registry_table()` and the `cosalette_inspect_app` MCP tool,
which gained `streams` and `periodic` sections in 0.6.0.

See `cosalette ai help manifest`, `cosalette ai help contracts`.

### Consumer discovery metadata

Attach Home Assistant / OpenHAB discovery metadata to a payload field with the
typed `consumer()` producer instead of hand-building `x-cosalette-consumer` dicts:

```python
from typing import Annotated
import pydantic
from cosalette.schema import consumer


class CoverState(pydantic.BaseModel):
    position: Annotated[
        int,
        pydantic.Field(
            json_schema_extra=consumer(
                display_name="Cover Position",
                unit="%",
                state_class="measurement",
            )
        ),
    ]
```

Keys are typo-checked under a type checker (ty/pyright) at author time against
`ConsumerMeta` (a static check only — at runtime the reader ignores unknown keys).
The block rides on the field, so it
survives regeneration via `TypeAdapter(model).json_schema()` and feeds the
HA/OpenHAB discovery generators. See `cosalette ai help consumer`.

For the common temperature/percent field shapes, use the semantic presets instead:
`temperature(display_name)` and `percent(display_name, *, icon=None)` wrap `consumer()`
with standard metadata; `temperature()` sets `device_class`, `unit`, and `state_class`;
`percent()` sets only `unit` and `state_class`.

Platform-specific overrides use the same pattern: `ha_discovery(**meta)` and
`openhab(**meta)`, typo-checked against `HaDiscoveryMeta`/`OpenHabMeta`. Each also
carries an open, untyped passthrough (`extra` / `channel_params`) for platform keys
the curated fields don't reach, merged in last. Combine multiple producers on one
field with `merge()`, since `json_schema_extra` accepts only one dict:

```python
from cosalette.schema import consumer, merge, openhab

hsb: Annotated[
    list[int],
    pydantic.Field(
        json_schema_extra=merge(
            consumer(display_name="HSB"),
            openhab(
                item_type="Color",
                channel_type="color",
                channel_params={"colorMode": "HSB"},
            ),
        )
    ),
]
```

See `cosalette ai help consumer-overrides`, ADR-056.

For one HA entity spanning several JSON fields (a `light` with `state` +
`brightness` + `color_temp`, a `climate`, a `cover`), use `ha_entities(ha_entity(...))`
on the payload MODEL, not a field — a channel with `ha_entities` skips per-property
generation entirely:

```python
from cosalette.schema import consumer, ha_entities, ha_entity


class BulbState(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        json_schema_extra=ha_entities(
            ha_entity(
                component="light",
                name="Desk Lamp",
                extra={"schema": "json", "brightness": True},
            ),
        )
    )
    state: Annotated[bool, pydantic.Field(json_schema_extra=consumer())]
    brightness: Annotated[int, pydantic.Field(json_schema_extra=consumer())]
```

`component` selects a real payload builder (`light` defaults `schema: "json"`;
`climate` drops the generic state/command topics since every capability needs its
own `<x>_state_topic`/`<x>_command_topic` via `extra`; `cover` keeps them). A
`device` archetype's paired `/state` + `/set` channels share one model and merge
into one entity automatically. See `cosalette ai help consumer-overrides`, ADR-057.

---

Refresh this file: `cosalette ai init`
Inspect registrations: `cosalette manifest myapp.main:app [--table]`
