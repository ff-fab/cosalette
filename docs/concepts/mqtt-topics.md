---
icon: material/message-outline
---

# MQTT Topics

Cosalette uses a **flat, Home Assistant-aligned topic hierarchy** where the
application name serves as the prefix and device names form the second segment.
Every topic follows the pattern `{app}/{device}/{channel}` or `{app}/{channel}`.

For the complete topic map, per-topic details, and prefix configuration, see
[MQTT Topic Reference](../reference/mqtt-topics.md).

## Retained vs Not-Retained Rationale

| Topic type    | Retained? | Rationale                                        |
|---------------|-----------|--------------------------------------------------|
| State         | Yes       | Consumers need last-known value on subscribe     |
| Availability  | Yes       | Consumers need last-known online/offline status  |
| Status        | Yes       | Crash detection requires retained LWT            |
| Error         | **No**    | Errors are events — stale errors mislead operators |
| Set (command) | —         | Inbound — retention is the publisher's choice    |

!!! tip "Why error topics are not retained"
    Consider a scenario: a device publishes an error, then recovers. If the
    error message were retained, a new subscriber would see it and incorrectly
    believe the error is still active. Non-retained errors are ephemeral — they
    are delivered to current subscribers only.

## Wildcard Monitoring

MQTT wildcards enable fleet-level monitoring without knowing device names
in advance. Since cosalette uses a flat topic hierarchy with a consistent
`{app}/{device}/{channel}` structure, a single wildcard subscription covers
all devices in an app — or all apps in a fleet when the `+` is in the app
position:

| Pattern                     | Use case                            |
|-----------------------------|-------------------------------------|
| `+/status`                  | Monitor all apps in a fleet         |
| `velux2mqtt/+/state`        | All device states in one app        |
| `+/error`                   | Global errors across all apps       |
| `velux2mqtt/+/error`        | Per-device errors in one app        |
| `velux2mqtt/+/availability` | Per-device availability in one app  |

The framework itself uses **explicit per-device subscriptions**, not wildcards —
wildcards are a consumer-side convenience for monitoring tools, dashboards, and
Home Assistant discovery.

```bash
# Subscribe to all errors across all bridges
mosquitto_sub -t '+/error' -v

# Subscribe to all state updates from a single bridge
mosquitto_sub -t 'velux2mqtt/+/state' -v
```

## See Also

- [MQTT Topic Reference](../reference/mqtt-topics.md) — complete topic map and per-topic details
- [Device Archetypes](device-archetypes.md) — which devices use which topics
- [Error Handling](error-handling.md) — error payload structure and semantics
- [Health & Availability](health-reporting.md) — heartbeat and LWT details
- [Configuration](configuration.md) — `topic_prefix` setting
- [ADR-002 — MQTT Topic Conventions](../adr/ADR-002-mqtt-topic-conventions.md)
- [ADR-025 — Command Channel and Sub-Topic Routing](../adr/ADR-025-command-channel-and-subtopic-routing.md)
- [ADR-031 — Sub-Entity Context Manager](../adr/ADR-031-sub-entity-context-manager.md)
