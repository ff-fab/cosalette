# MQTT Topic Reference

Complete topic schema reference for cosalette MQTT topics. For conceptual
background — rationale, routing internals, wildcard monitoring — see
[MQTT Topics (concept)](../concepts/mqtt-topics.md).

## Topic Schema

Every cosalette application uses a **flat, Home Assistant-aligned topic
hierarchy**. The `{prefix}` is the application name, and `{device}` is
the device name registered with `@app.device()`, `@app.command()`, or
`@app.telemetry()`. When the device name is omitted, the device publishes
to **root-level topics** without a `{device}` segment.

| Topic Pattern | Direction | QoS | Retain | Description |
|---|---|---|---|---|
| `{prefix}/{device}/state` | Outbound | 1 | Yes | Device state (JSON) |
| `{prefix}/{device}/set` | Inbound | — | — | Command input (subscribed) |
| `{prefix}/{device}/availability` | Outbound | 1 | Yes | Per-device online/offline (string) |
| `{prefix}/{device}/error` | Outbound | 1 | No | Per-device error event (JSON) |
| `{prefix}/error` | Outbound | 1 | No | Global error event (JSON) |
| `{prefix}/status` | Outbound | 1 | Yes | App heartbeat (JSON) and LWT (`"offline"`) |

### Root Device Topics

When `name` is omitted from a decorator, the device publishes at the
root level:

| Topic Pattern | Direction | QoS | Retain | Description |
|---|---|---|---|---|
| `{prefix}/state` | Outbound | 1 | Yes | Root device state (JSON) |
| `{prefix}/set` | Inbound | — | — | Root device command input |
| `{prefix}/availability` | Outbound | 1 | Yes | Root device online/offline |

Root device errors appear only on the global `{prefix}/error` topic —
there is no per-device error topic since it would be identical.

At most one root device per app. See
[Device Archetypes](../concepts/device-archetypes.md#root-devices-unnamed)
for naming rules.

!!! note "QoS and retain defaults"

    QoS values are hard-coded by the framework and cannot be overridden.
    Retain defaults match the table above, but `publish_state()` accepts
    an optional `retain` keyword argument to override per-call. The error
    and health services do not expose retain overrides.

## Topic Prefix

The `{prefix}` placeholder resolves at runtime from two sources,
checked in order:

1. **`Settings.mqtt.topic_prefix`** — when set (e.g. via
   `MQTT__TOPIC_PREFIX=staging-velux`), this value is used as `{prefix}`.
2. **`App(name="velux2mqtt")`** — when `topic_prefix` is empty (the
   default), the `name` argument is used.

This lets you deploy the same binary with different topic namespaces
(e.g. staging vs production) by setting an environment variable.

For example, `App(name="velux2mqtt")` with a device `"blind"` produces:

```text
velux2mqtt/blind/state
velux2mqtt/blind/set
velux2mqtt/blind/availability
velux2mqtt/error
velux2mqtt/status
```

See [Settings Reference](settings.md) for all MQTT configuration options.

## State Topics

Published by `ctx.publish_state()` in device functions.

- **Topic:** `{prefix}/{device}/state`
- **QoS:** 1 — at-least-once delivery
- **Retain:** Yes — subscribers receive the last-known state immediately
- **Payload:** JSON dict (user-defined structure)

```text
velux2mqtt/blind/state → {"position": 75, "tilt": 45}
```

## Command Topics

Inbound topics the framework subscribes to for command & control devices.

- **Topic:** `{prefix}/{device}/set`
- **Direction:** Inbound — the broker delivers messages to the framework
- **Payload:** Plain string, decoded by the user's command handler

The `TopicRouter` subscribes to `{prefix}/{device}/set` **individually**
for each device that has a registered command handler (via `@app.command()`
or `@ctx.on_command` inside an `@app.device()` function).
Telemetry-only devices are not subscribed. The framework does **not** use
MQTT wildcards for command subscription.

```text
velux2mqtt/blind/set ← "50"
```

### Routing behaviour

The router extracts the device name by simple string parsing — no regex,
no wildcards:

- Topics that do not match `{prefix}/{device}/set` (or `{prefix}/set` for root devices) are silently ignored.
- Messages for a device with no handler produce a WARNING log entry.

## Availability Topics

Published automatically by the `HealthReporter` at device startup and
during graceful shutdown.

- **Topic:** `{prefix}/{device}/availability`
- **QoS:** 1
- **Retain:** Yes
- **Payload:** `"online"` or `"offline"` (plain string, not JSON)

```text
velux2mqtt/blind/availability → "online"
velux2mqtt/blind/availability → "offline"
```

Compatible with Home Assistant's
[MQTT availability](https://www.home-assistant.io/integrations/mqtt/#availability)
schema.

## Error Topics

Published by the `ErrorPublisher` service. Every error is published to
the global topic; when a device name is known, a copy goes to the
per-device topic as well.

- **Global topic:** `{prefix}/error`
- **Per-device topic:** `{prefix}/{device}/error`
- **QoS:** 1
- **Retain:** No — errors are events, not state
- **Payload:** JSON (`ErrorPayload` schema)

```text
velux2mqtt/error       → {"error_type": "error", "message": "...", ...}
velux2mqtt/blind/error → {"error_type": "invalid_command", "message": "...", ...}
```

See [Payload Schemas](payloads.md) for the full `ErrorPayload` structure.

## App Status and LWT

The `{prefix}/status` topic serves two roles on the same topic:

### Last Will and Testament (LWT)

When the MQTT client connects, the framework registers a **Last Will and
Testament** via `build_will_config()`:

| Property | Value |
|---|---|
| **Topic** | `{prefix}/status` |
| **Payload** | `"offline"` |
| **QoS** | 1 |
| **Retain** | Yes |

If the client disconnects unexpectedly (crash, network loss), the **broker**
publishes `"offline"` to `{prefix}/status` on the application's behalf.
During graceful shutdown, the `HealthReporter` publishes `"offline"`
explicitly.

### Heartbeat

The `HealthReporter` publishes a structured JSON heartbeat to the
same `{prefix}/status` topic.  An initial heartbeat is published
immediately on connect (overwriting the LWT `"offline"`), then
periodically at the configured `heartbeat_interval` (default 60 s,
set to `None` to disable):

```json
{
    "status": "online",
    "uptime_s": 3600.0,
    "version": "0.3.0",
    "devices": {
        "blind": {"status": "ok"},
        "temp": {"status": "ok"}
    }
}
```

- **QoS:** 1
- **Retain:** Yes

Consumers can distinguish LWT from heartbeat by attempting JSON parse:
the LWT payload is a plain string `"offline"`, while heartbeats are
valid JSON objects.

See [Payload Schemas](payloads.md) for the full `HeartbeatPayload` and
`DeviceStatus` structures.

## Wildcards

The framework uses **explicit per-device subscriptions**, not MQTT
wildcards. However, external consumers (monitoring tools, Home Assistant)
can use wildcards for fleet-level monitoring:

| Pattern | Use case |
|---|---|
| `+/status` | Monitor all apps in a fleet |
| `velux2mqtt/+/state` | All device states in one app |
| `+/error` | Global errors across all apps |
| `velux2mqtt/+/availability` | Per-device availability in one app |

## See Also

- [MQTT Topics (concept)](../concepts/mqtt-topics.md) — rationale, routing
  internals, retained vs not-retained reasoning
- [Payload Schemas](payloads.md) — JSON payload structures
- [Error Handling (concept)](../concepts/error-handling.md) — error semantics
- [Health & Availability (concept)](../concepts/health-reporting.md) — heartbeat
  and LWT details
- [Settings Reference](settings.md) — `MQTT__TOPIC_PREFIX` and other MQTT
  settings
- [ADR-002 — MQTT Topic Conventions](../adr/ADR-002-mqtt-topic-conventions.md)
