---
icon: material/message-outline
---

# MQTT Topics

Cosalette uses a **flat, Home Assistant-aligned topic hierarchy** where the
application name serves as the prefix and device names form the second segment.
Every topic follows the pattern `{app}/{device}/{channel}` or `{app}/{channel}`.

## Complete Topic Map

| Topic                            | Direction   | Retained | QoS | Purpose                          |
|----------------------------------|-------------|----------|-----|----------------------------------|
| `{app}/{device}/state`           | Outbound    | Yes      | 1   | Device state (JSON)              |
| `{app}/{device}/set`             | Inbound     | —        | —   | Command input (subscribed, routed) |
| `{app}/{device}/{sub}/state`     | Outbound    | Yes      | 1   | Sub-entity state (JSON)            |
| `{app}/{device}/{sub}/set`       | Inbound     | —        | —   | Sub-entity command input (ADR-025 + ADR-031) |
| `{app}/{device}/{sub}/availability` | Outbound | Yes      | 1   | Sub-entity online/offline (ADR-031) |
| `{app}/{device}/availability`    | Outbound    | Yes      | 1   | Per-device online/offline        |
| `{app}/{device}/error`           | Outbound    | **No**   | 1   | Per-device error events          |
| `{app}/error`                    | Outbound    | **No**   | 1   | Global error events              |
| `{app}/status`                   | Outbound    | Yes      | 1   | App heartbeat / LWT              |

!!! info "Topic prefix = app name"
    The `{app}` prefix is the `name` argument to `App(name="velux2mqtt")`.
    For example, a device `"blind"` in a `"velux2mqtt"` app publishes state to
    `velux2mqtt/blind/state`.

!!! info "QoS is hard-coded at 1"
    All framework-managed publishes use **QoS 1** (at-least-once). This is
    not configurable — it matches Home Assistant expectations and is the
    right trade-off for idempotent state payloads. If you need QoS 0 for a
    high-frequency custom channel, use the escape hatch:

    ```python
    await ctx.publish("fast_sensor", payload, qos=0, retain=False)
    ```

## State Topics

```
velux2mqtt/blind/state → {"position": 75, "tilt": 45}
```

- **JSON payload** — always a serialised dict
- **Retained** — Home Assistant and other consumers receive the last-known
  state immediately upon subscribing
- **QoS 1** — at-least-once delivery for reliability
- Published by `ctx.publish_state()` in device code

## Command Topics

```
velux2mqtt/blind/set ← "50"
```

- **Inbound** — the framework subscribes to `{app}/{device}/set` and
  `{app}/{device}/+/set` for every command & control device
- The `TopicRouter` parses the topic, extracts the device name and optional
  sub-topic segment, and dispatches to the appropriate handler or command queue
- Standard telemetry devices do not subscribe to `/set`. Triggerable telemetry devices (`triggerable=True`) subscribe to `{app}/{device}/set` — incoming messages fire the handler immediately alongside the normal polling interval. See [Triggerable Telemetry](../guides/telemetry-device.md#triggerable-telemetry).

### Sub-Topic Commands

Devices that handle multiple command types use sub-topic routing. Each
sub-topic gets its own MQTT topic:

```text
velux2mqtt/cover/set             ← "50"         (root command)
velux2mqtt/cover/calibrate/set   ← "HIGH"       (sub-topic: calibrate)
```

The sub-topic appears as a segment between the device name and `/set`.
Register sub-topic handlers via `@ctx.on_command("calibrate")` inside an
`@app.device` function. See [ADR-025](../adr/ADR-025-command-channel-and-subtopic-routing.md)
for the design rationale.

### Topic Routing Internals

The `TopicRouter` extracts the device name and optional sub-topic from the
MQTT topic string:

```python
# Root command
"velux2mqtt/blind/set"           → device="blind", sub_topic=None

# Sub-topic command
"velux2mqtt/blind/calibrate/set" → device="blind", sub_topic="calibrate"
```

The router silently ignores topics that do not match the expected pattern. If a
message arrives for a device with no registered handler, a warning is logged
but no error is raised.

## Availability Topics

```
velux2mqtt/blind/availability → "online"
velux2mqtt/blind/availability → "offline"
```

- **String payload** — `"online"` or `"offline"` (not JSON)
- **Retained** — subscribers always know the last-known status
- **Home Assistant compatible** — matches the
  [MQTT availability](https://www.home-assistant.io/integrations/mqtt/#availability)
  schema directly
- Published automatically by the `HealthReporter` at device startup and
  during graceful shutdown

### Sub-Entity Availability

Sub-entities follow the same pattern one level deeper:

```
velux2mqtt/cover/calibrate/availability → "online"
velux2mqtt/cover/calibrate/availability → "offline"
```

These are managed automatically by `ctx.sub_entity()` — `"online"` on enter,
`"offline"` on exit, with retained state cleared on teardown. See
[ADR-031](../adr/ADR-031-sub-entity-context-manager.md).

## Error Topics

```
velux2mqtt/error        → {"error_type": "error", "message": "...", ...}
velux2mqtt/blind/error  → {"error_type": "invalid_command", "message": "...", ...}
```

- **Not retained** — errors are *events*, not state. A retained error would
  mislead operators into thinking the error is ongoing after a restart.
- **QoS 1** — reliable delivery so monitoring tools receive the event
- **Dual publication** — every error goes to `{app}/error` (global), and if a
  device name is known, also to `{app}/{device}/error`
- See [Error Handling](error-handling.md) for payload structure

!!! tip "Why not retained?"
    Consider a scenario: a device publishes an error, then recovers. If the
    error message were retained, a new subscriber would see it and incorrectly
    believe the error is still active. Non-retained errors are ephemeral — they
    are delivered to current subscribers only.

## App Status Topic

The `{app}/status` topic serves double duty:

=== "LWT (broker-published)"

    When the MQTT client connects, it registers a **Last Will and Testament**:
    if the client disconnects unexpectedly (crash, network loss), the broker
    publishes `"offline"` to `{app}/status` on the client's behalf.

    ```
    velux2mqtt/status → "offline"    (broker publishes on crash)
    ```

=== "Heartbeat (app-published)"

    The application publishes a structured JSON heartbeat:

    ```json
    {
        "status": "online",
        "uptime_s": 3600,
        "version": "0.3.0",
        "devices": {
            "blind": {"status": "ok"},
            "temp": {"status": "ok"}
        }
    }
    ```

The two formats coexist on the same topic — the LWT payload is a plain string
`"offline"`, while the heartbeat is JSON. Consumers can distinguish them by
attempting JSON parse. See [Health & Availability](health-reporting.md) for
details.

## Retained vs Not-Retained Rationale

| Topic type    | Retained? | Rationale                                        |
|---------------|-----------|--------------------------------------------------|
| State         | Yes       | Consumers need last-known value on subscribe     |
| Availability  | Yes       | Consumers need last-known online/offline status  |
| Status        | Yes       | Crash detection requires retained LWT            |
| Error         | **No**    | Errors are events — stale errors mislead operators |
| Set (command) | —         | Inbound — retention is the publisher's choice    |

## Wildcard Monitoring

MQTT wildcards enable fleet-level monitoring without knowing device names
in advance:

| Pattern                     | Use case                            |
|-----------------------------|-------------------------------------|
| `+/status`                  | Monitor all apps in a fleet         |
| `velux2mqtt/+/state`        | All device states in one app        |
| `+/error`                   | Global errors across all apps       |
| `velux2mqtt/+/error`        | Per-device errors in one app        |
| `velux2mqtt/+/availability` | Per-device availability in one app  |

```bash
# Subscribe to all errors across all bridges
mosquitto_sub -t '+/error' -v

# Subscribe to all state updates from a single bridge
mosquitto_sub -t 'velux2mqtt/+/state' -v
```

---

## See Also

- [Device Archetypes](device-archetypes.md) — which devices use which topics
- [Error Handling](error-handling.md) — error payload structure and semantics
- [Health & Availability](health-reporting.md) — heartbeat and LWT details
- [Configuration](configuration.md) — `topic_prefix` setting
- [ADR-002 — MQTT Topic Conventions](../adr/ADR-002-mqtt-topic-conventions.md)
- [ADR-025 — Command Channel and Sub-Topic Routing](../adr/ADR-025-command-channel-and-subtopic-routing.md)
- [ADR-031 — Sub-Entity Context Manager](../adr/ADR-031-sub-entity-context-manager.md)
