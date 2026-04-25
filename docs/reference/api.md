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
