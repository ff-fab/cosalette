# API Reference

Complete reference for all public classes, functions, and protocols exported by `cosalette`.

## Application

::: cosalette.App

::: cosalette.AppContext

::: cosalette.DeviceContext

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

## Introspection

::: cosalette.build_registry_snapshot

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

## Stores

::: cosalette.Store

::: cosalette.DeviceStore

::: cosalette.NullStore

::: cosalette.MemoryStore

::: cosalette.JsonFileStore

::: cosalette.SqliteStore
