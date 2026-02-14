# ADR-012: Health and Availability Reporting

## Status

Accepted **Date:** 2026-02-14

## Context

cosalette applications run as unattended daemons on multiple Raspberry Pi devices.
Operators need to know whether each application is running, which devices are available,
and when crashes or disconnects occur. MQTT's Last Will and Testament (LWT) feature
provides automatic crash detection — the broker publishes a pre-configured message when
a client disconnects unexpectedly.

Home Assistant requires device availability topics for MQTT-connected entities. Without
per-device availability reporting, HA cannot distinguish between "device is offline" and
"device has no data yet."

The framework needs two levels of health reporting:

1. **App-level:** Is the application process running? (LWT for crash detection)
2. **Device-level:** Is each individual device available? (per-device availability
   topics)

A key constraint: LWT messages are published by the **broker**, not the application,
when an unexpected disconnect occurs. LWT payloads must be simple static strings because
they are configured at connection time, before the application has runtime state.

## Decision

Use **per-device availability topics** and **app-level status with LWT**, augmented by
a **structured JSON heartbeat** for rich health data, because this provides both
automatic crash detection (LWT) and detailed fleet monitoring (structured health).

### App-level status (`{app}/status`)

Two publishing modes on the same topic:

**LWT (broker-published on crash/disconnect):**

```text
{app}/status = "offline"    # simple string, retained
```

**App-published (periodic heartbeat):**

```json
{
  "status": "online",
  "uptime_s": 3600,
  "version": "0.1.0",
  "devices": {
    "blind": {"status": "ok"},
    "window": {"status": "ok"}
  }
}
```

On connect, the app publishes the structured JSON heartbeat — overwriting the LWT
"offline" string. The JSON includes version for fleet management visibility and
per-device status for aggregate health monitoring.

### Per-device availability (`{app}/{device}/availability`)

```text
{app}/{device}/availability = "online"    # retained, updated by the framework
```

Published when a device starts and set to "offline" during graceful shutdown or when
a device encounters an unrecoverable error. Aligns with Home Assistant's MQTT device
availability model.

### Monitoring pattern

A central monitor can subscribe to `+/status` to aggregate health across all deployed
applications. The structured JSON heartbeat provides version, uptime, and per-device
status for fleet dashboards.

## Decision Drivers

- MQTT LWT for automatic crash detection without polling
- Home Assistant device availability model compatibility
- Fleet monitoring across 8+ deployed applications on multiple Raspberry Pis
- Version visibility for fleet management (which app version is deployed where)
- Distinguishing app-level health from individual device availability

## Considered Options

### Option 1: Simple online/offline only

Publish only "online"/"offline" strings on a single status topic per app.

- *Advantages:* Simple to implement. LWT-compatible. Sufficient for basic monitoring.
- *Disadvantages:* No version information for fleet management. No per-device
  granularity. Cannot determine uptime or device-level health without additional
  infrastructure.

### Option 2: HTTP health check endpoint

Expose an HTTP endpoint (e.g., `/health`) for liveness/readiness probes.

- *Advantages:* Standard in cloud-native environments. Compatible with Kubernetes
  probes and load balancers.
- *Disadvantages:* Requires an HTTP server in what is otherwise a pure MQTT application.
  Adds network port management. Does not leverage MQTT's built-in LWT. The deployment
  target is Raspberry Pi with Docker/systemd, not Kubernetes.

### Option 3: Structured JSON + LWT hybrid (chosen)

LWT publishes a simple "offline" string for crash detection. The app publishes
structured JSON heartbeats with rich health data during normal operation.

- *Advantages:* LWT provides automatic crash detection by the broker — no polling
  needed. Structured JSON heartbeat includes version, uptime, and per-device status.
  Per-device availability topics integrate with Home Assistant. Central `+/status`
  subscription enables fleet monitoring. The LWT "offline" string is overwritten by
  the JSON heartbeat on connect — simple and structured coexist on the same topic.
- *Disadvantages:* The status topic carries two different payload formats (string
  and JSON) depending on whether the app or the broker published. Heartbeat
  publishing adds periodic MQTT traffic.

## Consequences

### Positive

- Crashes are detected automatically via MQTT LWT — no polling or external probes
- Fleet monitoring via `+/status` provides aggregate health across all 8+ applications
- Version field in heartbeat enables fleet management dashboards (which version is
  deployed where)
- Per-device availability integrates with Home Assistant's MQTT device model
- Structured heartbeat includes per-device status without requiring individual device
  subscriptions for aggregate views

### Negative

- The `{app}/status` topic carries two payload formats — simple string (LWT) vs.
  structured JSON (heartbeat). Consumers must handle both.
- Periodic heartbeat publishing adds MQTT traffic (typically every 30-60 seconds per
  app — negligible for the broker)
- Per-device availability topics increase the total number of MQTT retained messages
  (one per device per application)

_2026-02-14_
