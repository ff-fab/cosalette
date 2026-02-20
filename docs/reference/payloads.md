# Payload Schemas

All cosalette MQTT payloads are either **JSON objects** or **plain strings**.
This page documents the exact schema for each payload type. For topic
routing and QoS details, see [MQTT Topic Reference](mqtt-topics.md).

## Device State

State payloads are **user-defined JSON dicts**. The framework imposes no
schema — whatever dict you pass to `ctx.publish_state()` is serialised to
JSON and published to `{prefix}/{device}/state`.

```python
# In your device function:
await ctx.publish_state({"position": 75, "tilt": 45})
```

Produces:

```json
{"position": 75, "tilt": 45}
```

The payload argument must be a `dict[str, object]`. The framework calls
`json.dumps()` internally.

## Error Payload

Published by the `ErrorPublisher` to `{prefix}/error` and (when a device
name is known) to `{prefix}/{device}/error`. Defined in
`cosalette._errors.ErrorPayload`.

### Example

```json
{
    "error_type": "invalid_command",
    "message": "Human-readable error description",
    "device": "blind",
    "timestamp": "2026-02-14T12:34:56+00:00",
    "details": {}
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `error_type` | `str` | Machine-readable error category. Determined by the `error_type_map` — unmapped exceptions fall back to `"error"`. |
| `message` | `str` | Human-readable description (`str(exception)`). |
| `device` | `str \| null` | Device name if the error is device-scoped, otherwise `null`. |
| `timestamp` | `str` | ISO 8601 timestamp with timezone (e.g. `"2026-02-14T12:34:56+00:00"`). |
| `details` | `object` | Optional dict of additional context. Defaults to `{}` when not provided. |

### Error Type Mapping

The `error_type` field is resolved from the `error_type_map` dict passed
to the `ErrorPublisher`. The map keys are **exact exception classes** (no
subclass matching). Unmapped exceptions produce `"error"` as the type.

```python
error_type_map = {
    ValueError: "invalid_command",
    TimeoutError: "timeout",
}
```

See [Error Handling (concept)](../concepts/error-handling.md) for the full
error pipeline.

## Heartbeat Payload

Published by `HealthReporter.publish_heartbeat()` to `{prefix}/status`.
Defined in `cosalette._health.HeartbeatPayload`.

The framework publishes an initial heartbeat on connect, then repeats
at the `heartbeat_interval` (default 60 s).  Set
`App(heartbeat_interval=None)` to disable periodic heartbeats.

### Example

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

### Fields

| Field | Type | Description |
|---|---|---|
| `status` | `str` | Always `"online"` for heartbeats. |
| `uptime_s` | `float` | Seconds since the `HealthReporter` was initialised (monotonic clock). |
| `version` | `str` | Application version string passed to the `HealthReporter`. |
| `devices` | `object` | Map of device name → `DeviceStatus`. Only includes devices currently tracked. |

### DeviceStatus Fields

Each entry in the `devices` map is a `DeviceStatus` object:

| Field | Type | Description |
|---|---|---|
| `status` | `str` | Free-form status string. Defaults to `"ok"` when a device is registered as available. |

Devices are added to tracking when `publish_device_available()` is called
and removed when `publish_device_unavailable()` is called.

## Availability Messages

Published on `{prefix}/{device}/availability` and `{prefix}/status` (LWT).
These are **plain strings**, not JSON.

| Payload | Meaning | When published |
|---|---|---|
| `"online"` | Device or app is available | `publish_device_available()`, heartbeat |
| `"offline"` | Device or app is unavailable | `publish_device_unavailable()`, `shutdown()`, LWT (broker-published on crash) |

!!! tip "Distinguishing heartbeat from LWT on `{prefix}/status`"

    The heartbeat payload is a JSON object; the LWT/shutdown payload is
    the plain string `"offline"`. Consumers can distinguish them by
    attempting JSON parse.

## Command Payloads

Inbound messages on `{prefix}/{device}/set` topics are **plain strings**.
The framework passes the raw payload to the command handler. The
recommended approach is `@app.command()` — handlers only declare the
parameters they need:

```python
@app.command("valve")
async def handle_valve(payload: str) -> dict[str, object]:
    return {"valve_state": payload}
```

If the handler also needs the full MQTT topic:

```python
@app.command("blind")
async def handle_blind(topic: str, payload: str) -> dict[str, object]:
    position = int(payload)
    return {"position": position}
```

Alternatively, inside an `@app.device()` function you can use
`@ctx.on_command`:

```python
@ctx.on_command
async def handle(topic: str, payload: str) -> None:
    position = int(payload)  # User-defined decoding
    await set_blind_position(position)
```

The framework performs no parsing, validation, or transformation on
command payloads — decoding is entirely the responsibility of the
command handler.

## See Also

- [MQTT Topic Reference](mqtt-topics.md) — topic patterns, QoS, and retain
  settings
- [Error Handling (concept)](../concepts/error-handling.md) — error pipeline
  and `error_type_map`
- [Health & Availability (concept)](../concepts/health-reporting.md) —
  heartbeat scheduling and LWT integration
- [ADR-011 — Error Handling](../adr/ADR-011-error-handling-and-publishing.md)
- [ADR-012 — Health Reporting](../adr/ADR-012-health-and-availability-reporting.md)
