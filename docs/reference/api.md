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

## Periodic Background Tasks

`@app.periodic` registers a coroutine as a background task that runs on a fixed
interval with no MQTT output. It is the right primitive for side-effect work that runs
alongside devices: flushing write buffers, sending watchdog pings, synchronising LED
state, or warming caches.

### `App.periodic(name, *, interval, enabled, init, summary, behavior)`

Decorator form. Registers the decorated coroutine as a periodic background task.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `str \| None` | No | Task name (defaults to `func.__name__`). Must be unique across all registrations. |
| `interval` | `float \| timedelta \| Callable[..., float] \| SettingRef` | **Yes** | Seconds between invocations. Accepts a raw float, a `datetime.timedelta`, a `Callable[[Settings], float]`, or a `SettingRef`. Resolved at bootstrap alongside `@app.telemetry` intervals. Must be positive. |
| `enabled` | `bool \| Callable[..., bool]` | No | Whether to register the task (default `True`). A callable receives the resolved `Settings` instance and returns `bool`; evaluated at bootstrap (ADR-038 pattern). Literal `False` silently skips registration. |
| `init` | `Callable[..., Any] \| None` | No | One-shot setup factory. Called once at startup before the first sleep. Receives the same injected parameters as the handler (same DI rules as `@app.device`). |
| `summary` | `str \| None` | No | Short description for introspection. |
| `behavior` | `list[str] \| None` | No | Behavioural contract annotations for introspection. |

```python
import datetime
import cosalette
from cosalette import SettingRef


class AppSettings(cosalette.Settings):
    watchdog_enabled: bool = True
    led_interval: float = 5.0


app = cosalette.App(name="bridge", version="1.0.0")


@app.periodic("flush-buffer", interval=30.0)
async def flush_buffer(cache: BufferCache) -> None:
    await cache.flush()


@app.periodic(
    "watchdog",
    interval=datetime.timedelta(minutes=1),
    enabled=lambda s: s.watchdog_enabled,
)
async def watchdog_ping(settings: AppSettings) -> None:
    await ping_watchdog(settings.watchdog_url)


@app.periodic("led-sync", interval=SettingRef("led_interval"))
async def led_sync(led: LedPort) -> None:
    await led.sync_state()
```

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
and [Adapter Lifecycle Management](../guides/adapters.md#adapter-lifecycle-management)
for usage examples.

## Streaming

`StreamablePort[T_co]` and `Stream[T]` are the push-to-pull bridge for
hardware devices that deliver data via callbacks rather than polling.
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
