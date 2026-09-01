---
icon: material/needle
---

# Dependency Injection Reference

Complete reference for the cosalette dependency injection system: injectable types,
per-archetype resolution frequency, and marker syntax.

!!! tip "Concept vs Reference"

    For the **design rationale and resolution rules**, see
    [Dependency Injection (concept)](../concepts/dependency-injection.md).
    For DI-related exceptions, see [Error Taxonomy](errors.md).

## Injectable Types

The following types can be annotated on any handler parameter and will be
supplied by the framework at dispatch time. Resolution is by **exact type
identity** first, then `issubclass` fallback — never by parameter name.

### Framework-provided types

These are available to every archetype without any extra registration.

| Annotation | Value injected | Notes |
|---|---|---|
| `DeviceContext` | Per-device context object | Full API: publish, settings, adapters, clock |
| `Settings` or subclass | App settings instance | Subclasses matched via `issubclass`; your `AppSettings(Settings)` works directly |
| `logging.Logger` | `logging.getLogger("cosalette.<device>")` | Device-scoped logger |
| `ClockPort` | Abstracted clock | Mockable in tests; avoids `datetime.now()` |
| `asyncio.Event` | Shutdown event | Set when the app is stopping |
| `TriggerPayload` | Trigger context | Only meaningful for triggerable telemetry devices; `.source` is `"scheduled"`, `"mqtt"` or `"local"` |
| `EntityNotifier` | In-process trigger notifier | Call it with an entity name to wake a `triggerable="local"` (or `"both"`) telemetry device; see [Local triggers](../guides/telemetry-advanced.md#local-in-process-triggers) |

### Persistence

| Annotation | Value injected | Notes |
|---|---|---|
| `DeviceStore` | Per-device persistence store | Requires a store backend on `App()`; see [Persistence concept](../concepts/persistence.md) |

### Adapters

| Annotation | Value injected | Notes |
|---|---|---|
| Any port type registered via `app.adapter()` | The registered adapter instance | Matched by exact type or `issubclass` |
| `init=` callback return type | Value returned by the `init=` callback | Injected into the device handler and all of its commands |
| Per-device config type | Per-device config object | From dict-name expansion; the concrete type is added to providers |

### Lifespan-yielded types

| Annotation | Value injected | Notes |
|---|---|---|
| Type yielded by `lifespan=` context manager | The yielded value | Single value per app; concrete runtime type matched; not available in `on_configure` hooks |

### Stream archetype only

| Annotation | Value injected | Notes |
|---|---|---|
| `Stream[T]` | Async stream iterator | Available only in `@app.stream` handlers for push-to-pull bridging |

### Request-scoped (Annotated markers)

These require `Annotated[T, marker]` syntax and are only available in
archetypes that receive an MQTT message (commands, triggered telemetry).
**Reactors (`@app.react`) have no inbound message context — `Topic()`, `Payload()`,
and `Message` raise `TypeError` in reactor handlers.**

| Annotation | Value injected | Notes |
|---|---|---|
| `Annotated[T, Payload()]` | Parsed payload (T via Pydantic TypeAdapter) | JSON-decoded and validated; use `Payload(raw=True)` for raw string |
| `Annotated[str, Topic()]` | Full inbound MQTT topic string | Inner type must be `str`; raises TypeError otherwise |
| `Annotated[T, Depends(fn)]` | Return value of synchronous callable `fn` | `fn` can declare its own injectable parameters |
| `Annotated[T \| None, Optional()]` | Provider T if registered; otherwise param default or `None` | Fallback to `None` requires `= None` default or no default |
| `Message` (bare type, no marker) | `Message(topic=..., payload=...)` dataclass | Both fields are raw strings; not available outside command context |

## Per-Archetype Resolution Frequency

The framework builds a providers map once per device lifetime. What varies is
**when `resolve_request_kwargs` is called** — i.e. when handler kwargs are
built from that map.

| Archetype | Resolved | Source location |
|---|---|---|
| `@app.command` | **Per message** — once per incoming MQTT message | `_runners/_command_runner.py`, `prepare_command_kwargs` (called per dispatch) |
| `@app.react` | **Per message** — once per command that triggers the reactor | `_wiring/_reactors.py`, `_dispatch_single_reactor_with_events` |
| `@app.telemetry` | **Once per device lifetime** — before the publish loop | `_runners/_telemetry_runner.py`, `_TelemetryRunner.run` |
| `@app.device` (periodic) | **Once per device lifetime** — before the `while True` loop | `_runners/_periodic.py`, resolved once then loop repeats |
| `@app.stream` | **Once per device lifetime** — before the async generator is iterated | `_runners/_stream_runner.py`, `_build_handler_kwargs` called once |

For telemetry, periodic, and stream handlers, the same resolved kwargs dict is
reused on every cycle. Injected objects — `DeviceContext`, `Settings`, adapter
instances — are stable references for the entire lifetime of the device.

For commands, kwargs are rebuilt on every MQTT message so that request-scoped
values (`Topic()`, `Payload()`, `Message`) reflect the current message. The
providers map itself (adapter instances, settings, etc.) is shared and not
rebuilt per message. Reactors rebuild kwargs per triggering command but have no
request-scoped context — `Topic()`, `Payload()`, and `Message` are not available.

## Marker Syntax Quick Reference

```python
from typing import Annotated
from cosalette import DeviceContext
from cosalette.di import Depends, Optional
from cosalette.mqtt import Message, Payload, Topic

# Framework type — exact or issubclass match
async def handler(ctx: DeviceContext) -> ...: ...

# Adapter port — registered via app.adapter(SensorPort, ...)
async def handler(sensor: SensorPort) -> ...: ...

# Parsed payload
async def handler(cmd: Annotated[SetCmd, Payload()]) -> ...: ...

# Raw payload string
async def handler(raw: Annotated[str, Payload(raw=True)]) -> ...: ...

# Topic string
async def handler(topic: Annotated[str, Topic()]) -> ...: ...

# Full message object (bare type, no marker)
async def handler(msg: Message) -> ...: ...

# Dependency callable
def get_device_id(topic: Annotated[str, Topic()]) -> str:
    return topic.split("/", 3)[2]

async def handler(device_id: Annotated[str, Depends(get_device_id)]) -> ...: ...

# Optional provider (falls back to None if not registered)
async def handler(store: Annotated[DeviceStore | None, Optional()] = None) -> ...: ...
```
