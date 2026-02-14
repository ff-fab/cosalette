# ADR-002: MQTT Topic Conventions

## Status

Accepted **Date:** 2026-02-14

## Context

All 8 IoT-to-MQTT bridge projects need a standardised MQTT topic layout. The velux2mqtt
legacy project uses `{prefix}/{actuator}/actual` for state — a non-standard convention
that does not align with Home Assistant's MQTT integration. Home Assistant expects
`state` for device state topics and `set` for command topics. Additionally, the
framework needs to support device-level availability tracking and app-level health/crash
detection via MQTT Last Will and Testament (LWT).

A consistent topic convention across all projects enables:

- Home Assistant auto-discovery compatibility
- Uniform monitoring and debugging (subscribe to `+/status` to see all apps)
- Clear separation between state, commands, availability, errors, and health

## Decision

Use a **flat, Home Assistant-aligned topic layout** with the following structure:

```text
{app_name}/{device_name}/state          → device state (JSON, retained)
{app_name}/{device_name}/set            → command input (subscribed)
{app_name}/{device_name}/availability   → online/offline (retained, LWT-compatible)
{app_name}/error                        → structured error events (not retained)
{app_name}/status                       → app-level health (retained, LWT)
```

Design choices:

| Choice                     | Rationale                                                    |
| -------------------------- | ------------------------------------------------------------ |
| `state` not `actual`       | Aligns with Home Assistant MQTT convention                   |
| `set` for commands         | Home Assistant convention, clear intent                      |
| `availability` per device  | Allows individual device health monitoring                   |
| `status` at app level      | Last Will & Testament for crash detection                    |
| Flat structure             | Avoids deep nesting; each device is a first-class citizen    |
| JSON payloads              | Machine-parseable, extensible, universal                     |
| Retained state             | Clients get current state on connect without polling         |
| Errors not retained        | Errors are events, not last-known state                      |

Telemetry-only devices simply never subscribe to a `set` topic — the framework skips
command subscription when no command handler is registered.

## Decision Drivers

- Home Assistant MQTT integration compatibility
- LWT support for crash detection of unattended daemons
- Per-device availability for fine-grained health monitoring
- Consistent topic naming across all 8+ projects
- Machine-parseable payloads for monitoring and aggregation

## Considered Options

### Option 1: `actual` topic name (velux2mqtt legacy)

Keep the existing `{prefix}/{device}/actual` convention from velux2mqtt.

- *Advantages:* No migration needed for velux2mqtt.
- *Disadvantages:* Non-standard — does not align with Home Assistant conventions. Would
  need custom configuration in HA for every entity. Inconsistent with the broader MQTT
  ecosystem.

### Option 2: Flat topics without hierarchy

Use flat topics like `velux2mqtt-blind-state` with dashes instead of path separators.

- *Advantages:* Simple subscription, no hierarchy to navigate.
- *Disadvantages:* Cannot use MQTT wildcards (`+`, `#`) for monitoring. Loses the
  natural grouping that topic hierarchies provide. Non-standard.

### Option 3: Deep nested hierarchy

Use deeply nested topics like `{app}/{device}/sensors/{sensor_name}/state`.

- *Advantages:* Very granular, allows sub-device organisation.
- *Disadvantages:* Over-engineered for the project scope. Makes subscriptions complex.
  Does not match Home Assistant's expected topic structure.

### Option 4: Home Assistant-aligned flat hierarchy (chosen)

`{app}/{device}/state`, `/set`, `/availability` with app-level `status` and `error`.

- *Advantages:* Direct Home Assistant compatibility. Clean wildcard subscriptions
  (`velux2mqtt/+/state`). Industry-standard patterns. Clear separation of concerns
  across topic suffixes.
- *Disadvantages:* Requires migration from `actual` to `state` in velux2mqtt.

## Decision Matrix

| Criterion           | `actual` Legacy | Flat (No Hierarchy) | Deep Nested | HA-Aligned Flat |
| ------------------- | --------------- | ------------------- | ----------- | --------------- |
| HA compatibility    | 1               | 2                   | 2           | 5               |
| Wildcard monitoring | 3               | 1                   | 3           | 5               |
| Migration effort    | 5               | 2                   | 2           | 3               |
| Extensibility       | 2               | 2                   | 4           | 4               |
| Standard compliance | 1               | 1                   | 3           | 5               |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- All projects work with Home Assistant MQTT integration out of the box
- Wildcard subscriptions enable fleet monitoring (`+/status`, `+/error`)
- Clear, predictable topic paths make debugging straightforward
- Per-device availability allows fine-grained health dashboards
- LWT on `{app}/status` provides automatic crash detection

### Negative

- velux2mqtt must migrate from `actual` to `state` (breaking change for existing HA
  automations)
- Flat structure may feel limiting if a future project needs sub-device sensors (can be
  handled via JSON payload nesting)
- App-level `error` topic aggregates all device errors — per-device error topics are
  published additionally when the device name is known (see ADR-011)

_2026-02-14_
