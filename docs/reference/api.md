---
icon: material/api
---

# API Reference

Complete reference for all public classes, functions, and protocols exported by `cosalette`.

## Application

::: cosalette.App

::: cosalette.AppContext

::: cosalette.DeviceContext

::: cosalette.SubEntityContext

::: cosalette.Command

::: cosalette.CronSchedule

## Shared-State Factories

`@app.state` registers a factory that runs once at bootstrap, after settings are
resolved and before lifecycle adapters are entered.  Its return value is registered
in the DI container by the return type and injected into any handler declaring that
type.

Four factory forms are supported, detected from the return annotation at registration
time:

| Form | Teardown |
|------|----------|
| `def f(...) -> T` | None |
| `def f(...) -> ContextManager[T]` | `__exit__` on shutdown |
| `async def f(...) -> AsyncIterator[T]` | generator finalized on shutdown |
| `async def f(...) -> AsyncContextManager[T]` | `__aexit__` on shutdown |

Teardown runs in **reverse registration order** (LIFO).

The factory may optionally declare one parameter annotated with `Settings` or a
subclass — the framework passes the resolved settings instance narrowed to that type.
Zero-parameter factories are also valid.

**Registration-time validation:**

- Missing return annotation → `TypeError`
- Unsupported return annotation form → `TypeError`
- First parameter annotated with a non-`Settings` type → `TypeError`
- Two factories returning the same type → `ValueError`

See [Share State Between Handlers](../guides/shared-state.md#app-state-factory) for
usage examples and [ADR-039](../adr/ADR-039-app-state-factory.md) for design rationale.

## Domain-Event Reactors

`@app.react` registers a reactor function that the framework calls automatically
at execution boundaries when a state object has pending domain events.

```python
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

**`state_type`** — the `@app.state`-registered type to watch. Must be registered
before `@app.react` is called; otherwise `ValueError` is raised at decoration time.

**`drain=`** — optional callable `(state_instance) -> Iterable | None`. When
`None`, the framework calls `state_instance.drain_events()` structurally. If no
drain method exists, `AttributeError` is raised at runtime.

**`events` parameter** — reserved name. If the reactor function declares a
parameter named `events`, the framework injects the drained event list directly.
The `events` parameter is **not** resolved through type-based DI.

**Reaction boundaries:**

| Handler | When reactors fire |
|---|---|
| `@app.device` | After each `yield` and once at normal completion |
| `@app.stream` | After each item processed and once at handler exit |
| `@app.telemetry` | After each successful handler return |
| `@app.command` | After each successful handler return |

Reactors do not fire on cancellation or unhandled exceptions.

**Registration-time validation:**

- `state_type` not registered via `@app.state` → `ValueError`
- Reactor function is not `async def` → `TypeError`

See [Share State Between Handlers](../guides/shared-state.md#app-react-domain-event-reactors)
for usage examples and [ADR-043](../adr/ADR-043-domain-event-reactors-for-state-objects.md)
for design rationale.

## Periodic Background Tasks

`@app.periodic` registers a coroutine as a background task that runs on a fixed
interval with no MQTT output. It is the right primitive for side-effect work that runs
alongside devices: flushing write buffers, sending watchdog pings, synchronising LED
state, or warming caches.

```python
import datetime
import cosalette
from cosalette import SettingRef


class AppSettings(cosalette.Settings):
    watchdog_enabled: bool = True
    led_interval: float = 5.0


app = cosalette.App(name="bridge", version="1.0.0")


@app.periodic("flush-buffer", interval=30.0)  # (1)!
async def flush_buffer(cache: BufferCache) -> None:
    await cache.flush()


@app.periodic(
    "watchdog",
    interval=datetime.timedelta(minutes=1),  # (2)!
    enabled=lambda s: s.watchdog_enabled,    # (3)!
)
async def watchdog_ping(settings: AppSettings) -> None:
    await ping_watchdog(settings.watchdog_url)


@app.periodic("led-sync", interval=SettingRef("led_interval"))  # (4)!
async def led_sync(led: LedPort) -> None:
    await led.sync_state()


@app.periodic(  # (5)!
    "poll-sensor",
    interval=lambda s: s.sensor_poll_interval,
)
async def poll_sensor(settings: AppSettings) -> None:
    await read_sensor(settings.sensor_url)
```

1. `interval` as a plain `float` — simplest form; positive number of seconds between invocations.
2. `interval` as `datetime.timedelta` — converted to seconds at registration time.
3. `enabled` as a callable — evaluated at bootstrap with the resolved `Settings` instance;
   `False` silently skips registration entirely (ADR-038 deferred-enabled pattern).
4. `SettingRef("led_interval")` — deferred resolution: the value of `AppSettings.led_interval`
   is read from settings at bootstrap, not at import time.
5. `interval` as a `Callable[[Settings], float]` — called once at bootstrap with the resolved
   settings; use when the interval depends on a computed expression or multiple settings fields.

**DI injection:** handlers may declare `Settings` subclasses, adapter ports registered
via `app.adapter()`, `ClockPort`, and objects registered by `@app.state` factories.
`DeviceContext` is **not** available (periodic tasks have no MQTT lifecycle).

**Exception behaviour:** `asyncio.CancelledError` propagates (clean shutdown). All
other exceptions are caught, logged at `ERROR` level, and the loop continues.

**Lifecycle:** periodic tasks are spawned as `asyncio.Task`s during Phase 3 (Run) and
cancelled during Phase 4 (Teardown) with a 5-second grace period.

### `App.add_periodic(name, func, *, interval, enabled, init, summary, behavior)`

Imperative equivalent of `@app.periodic`. Accepts `enabled: bool` only (not a
callable) — use inside `@app.on_configure` where settings are already resolved.

### `App.periodic_registrations`

`Sequence[_PeriodicRegistration]` — read-only view of all registered periodic tasks.
Each entry exposes `name`, `interval`, `func`, and the injection plan.

### `AppHarness.tick_periodic(name)`

Invoke one cycle of a named periodic handler synchronously, bypassing the interval
sleep. This is the recommended way to test periodic handlers:

```python
async def test_flush_writes_pending_data() -> None:
    mock_buf = MockBufferPort()
    harness = AppHarness.create()
    harness.app.adapter(BufferPort, lambda: mock_buf)

    await harness.tick_periodic("flush-buffer")

    assert mock_buf.flush_called
```

The handler runs exactly once. No task is spawned; no sleep occurs.

### `AppHarness.create(..., run_periodic=False)`

The `run_periodic` parameter on `AppHarness.create()` controls whether periodic tasks
are spawned during `harness.run()`:

| Value | Effect |
|-------|--------|
| `False` (default) | Periodic tasks are not spawned — existing tests are unaffected |
| `True` | Periodic tasks are spawned as `asyncio.Task`s for integration-level coverage |

Prefer `tick_periodic()` for unit-level testing of handler logic. Use
`run_periodic=True` only when you need to verify that a task actually fires during
the full application lifecycle.

See the [Periodic Tasks guide](../guides/periodic-tasks.md) for full usage examples
and [ADR-041](../adr/ADR-041-periodic-background-tasks.md) for design rationale.

### `AppHarness.create(..., run_streams=False)`

The `run_streams` parameter controls whether `@app.stream` handlers run their real
lifecycle during `harness.run()`:

| Value | Effect |
|-------|--------|
| `False` (default) | Streams are suppressed — use [`inject_stream()`](../guides/streaming.md#step-4-test-with-inject_stream) for handler-logic tests |
| `True` | The framework opens each registered `StreamablePort`, scans, and runs the handler, so a stream can arm a concurrently running device |

`run_streams=True` mirrors `run_periodic=True`: prefer `inject_stream()` for
handler logic, and reach for `run_streams=True` only for integration-shape tests
(port open/scan ordering, a stream arming another entity through the shared
`EntityNotifier`). It **fails fast** with `RuntimeError` when a stream has no
matching `StreamablePort` adapter, rather than hanging.

!!! warning "`run_streams=True` opens the app's real ports"
    The framework opens whatever `StreamablePort` the app registers — real
    hardware included. Register a fake port on `harness.app`, or pass
    `AppHarness.create(dry_run=True)` to bind the dry-run adapter variant.

See the [Streaming guide](../guides/streaming.md#step-4-test-with-inject_stream)
for usage examples and [ADR-045](../adr/ADR-045-stateful-stream-receiver-semantics.md)
for design rationale.

## MQTT

::: cosalette.MqttPort

::: cosalette.MqttClient

::: cosalette.MqttLifecycle

::: cosalette.MqttMessageHandler

::: cosalette.MockMqttClient

::: cosalette.NullMqttClient

::: cosalette.WillConfig

::: cosalette.MessageCallback

## Error Handling

::: cosalette.ErrorPayload

::: cosalette.ErrorPublisher

::: cosalette.build_error_payload

## Health and Availability

::: cosalette.DeviceStatus

::: cosalette.HeartbeatPayload

::: cosalette.HealthReporter

::: cosalette.build_will_config

::: cosalette.HealthCheckable

::: cosalette.AdapterHealthStatus

## Clock

::: cosalette.ClockPort

::: cosalette.SystemClock

## Logging

::: cosalette.JsonFormatter

::: cosalette.configure_logging

## Settings

::: cosalette.Settings

::: cosalette.MqttSettings

::: cosalette.LoggingSettings

## Adapter Lifecycle

Adapters registered via `app.adapter()` that implement the async context manager
protocol (`__aenter__`/`__aexit__`) are automatically managed by the framework:

- **Entered** during startup, before the `lifespan=` hook runs
- **Exited** during shutdown, after the `lifespan=` hook exits
- Managed via `contextlib.AsyncExitStack` for LIFO ordering and exception safety
- Adapters without `__aenter__`/`__aexit__` pass through unchanged

The detection is duck-typed — any object with both `__aenter__` and `__aexit__`
attributes qualifies. No base class or registration is needed.

See [ADR-016](../adr/ADR-016-adapter-lifecycle-protocol.md) for the design rationale
and [Adapter Lifecycle Management](../guides/hardware-adapters.md#adapter-lifecycle-management)
for usage examples.

## Streaming

`StreamablePort[T_co]` and `Stream[T]` are
the push-to-pull bridge for hardware devices that deliver data via callbacks
rather than polling. All lifecycle methods (`open`, `close`, `start_scan`, `stop_scan`)
are coroutines awaited by the stream runner.
See [Streaming](../concepts/streaming.md) for a full explanation and
[ADR-042](../adr/ADR-042-streaming-protocol-streamableport-and-stream-t.md) for design rationale.

::: cosalette.StreamablePort

::: cosalette.Stream

## Publish Strategies

::: cosalette.PublishStrategy

::: cosalette.Every

::: cosalette.OnChange

## Composite Strategies

::: cosalette.AllStrategy

::: cosalette.AnyStrategy

## Introspection

::: cosalette.build_registry_snapshot

::: cosalette.format_registry_json

::: cosalette.format_registry_table

## Retry / Backoff

::: cosalette.BackoffStrategy

::: cosalette.ExponentialBackoff

::: cosalette.LinearBackoff

::: cosalette.FixedBackoff

::: cosalette.CircuitBreaker

## Triggerable Telemetry

::: cosalette.TriggerPayload

::: cosalette.EntityNotifier

::: cosalette.DeviceTrigger

## Filters

::: cosalette.Filter

::: cosalette.Pt1Filter

::: cosalette.MedianFilter

::: cosalette.OneEuroFilter

## Persistence

::: cosalette.PersistPolicy

::: cosalette.SaveOnPublish

::: cosalette.SaveOnChange

::: cosalette.SaveOnShutdown

::: cosalette.AllSavePolicy

::: cosalette.AnySavePolicy

## Stores

::: cosalette.Store

::: cosalette.DeviceStore

::: cosalette.NullStore

::: cosalette.MemoryStore

::: cosalette.JsonFileStore

::: cosalette.SqliteStore
