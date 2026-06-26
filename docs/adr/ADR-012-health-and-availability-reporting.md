---
status: Accepted
date: 2026-02-14
impact: moderate
tags: [health, mqtt]
---

# ADR-012: Health and Availability Reporting

## Status

Accepted **Date:** 2026-02-14 | Amended **Date:** 2026-06-26

## Context

cosalette applications run as unattended daemons across multiple hosts.
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
- Fleet monitoring across 8+ deployed applications on multiple hosts
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
  targets use Docker or systemd, not Kubernetes.

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

## Decision Matrix

| Criterion                 | Simple Online/Offline | HTTP Health Endpoint | JSON + LWT Hybrid |
| ------------------------- | --------------------- | -------------------- | ------------------ |
| Crash detection           | 4                     | 2                    | 5                  |
| Fleet monitoring          | 2                     | 3                    | 5                  |
| HA compatibility          | 4                     | 2                    | 5                  |
| Implementation complexity | 5                     | 2                    | 3                  |
| Rich health data          | 1                     | 4                    | 5                  |

_Scale: 1 (poor) to 5 (excellent)_

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

## Amendment (2026-06-26) — Additive

**Rationale:** The original ADR assumed health-state publishes (per-device availability, registry snapshot, heartbeat) happened synchronously at startup. In practice, MqttClient.start() is non-blocking — the broker connection is established ~2 s later in a background loop. Startup publishes fired before the broker was connected raised RuntimeError('MqttClient is not connected'), were swallowed by ADR-011 fire-and-forget semantics, and were never retried. The periodic heartbeat self-healed {app}/status, but per-device availability and the AsyncAPI registry had no re-emit path, so retained availability stayed at the broker LWT value 'offline' while the service was healthy. This additive amendment documents the connection-aware re-announce mechanism that fixes the root cause.

### Additional Sub-Decision: Re-announce Health State on Every MQTT (Re)connect

The framework gates all retained startup publishes on a successful MQTT connection and re-asserts health state on every subsequent reconnect.

**Root cause fixed:** `MqttClient.start()` is non-blocking — the broker connects asynchronously ~2 s later. The original implementation published retained per-device availability (`{app}/{device}/availability`), the AsyncAPI registry snapshot (`{app}/_meta/registry`), and the initial heartbeat (`{app}/status`) immediately after `start()` — i.e. before the broker was connected. Those publishes raised `RuntimeError("MqttClient is not connected")`, were silently swallowed by ADR-011's fire-and-forget error handling, and were never retried. The periodic heartbeat eventually self-healed `{app}/status`, but per-device `availability` and the registry had no re-emit path, leaving retained availability stuck at the broker LWT value `"offline"` while the service was actually running.

**Mechanism — `MqttConnectAware` protocol:**

A narrow capability protocol `MqttConnectAware` (method `add_connect_callback`) is introduced. The production `MqttClient` implements this protocol and invokes registered callbacks immediately after each successful connection (including reconnects). Adapters that do not implement `MqttConnectAware` (e.g. mock / null test doubles) retain the original eager inline startup publishes, preserving existing test and offline behaviour.

**Behaviour on first connect (optimistic announce):**

- Publish `"online"` availability for all registered devices
- Re-publish the AsyncAPI registry snapshot (`{app}/_meta/registry`)
- Publish an initial JSON heartbeat (`{app}/status`)

All three publishes are fire-and-forget per ADR-011; individual failures are logged but not propagated.

**Behaviour on every reconnect (selective re-announce via `HealthReporter.reannounce()`):**

- Re-assert `"online"` availability only for devices that are currently tracked as available. Devices that transitioned to `"offline"` after the initial connect keep their last retained `"offline"` payload — their availability is not re-broadcast.
- Re-publish the registry snapshot and a fresh heartbeat.

This ensures that a broker restart or reconnect does not inadvertently resurface `"online"` for devices that went offline after initial startup.

**Lifecycle alignment:** This approach aligns the implementation with ADR-016's documented lifecycle order (MQTT Connect → enter adapters → …). Startup retained publishes are now gated on broker connection and no longer fire before connect, eliminating spurious `"not connected"` error logs.

**Interaction with ADR-011:** Per-callback failures are logged (ADR-011 fire-and-forget) and never propagated, preserving the existing resilience contract. Graceful shutdown still publishes `"offline"` for all devices and the LWT remains registered at connect time — no change to shutdown semantics.

**Note on availability publish timing:** Availability is re-published on MQTT connect, which may precede full adapter-entry completion (adapters start in parallel after connect). For most deployments this is acceptable — availability reflects broker connectivity rather than adapter readiness.

### Additional Positive Consequences

- Retained availability now reflects reality after a reconnect or broker restart — no longer stuck at the LWT 'offline' value while the service is healthy
- Late subscribers (or subscribers that reconnect after the app has started) receive the correct 'online' availability immediately via MQTT retain
- Spurious startup 'MqttClient is not connected' error logs are eliminated — startup publishes are gated on broker connection
- Selective reannounce on reconnect correctly preserves 'offline' for devices that went offline after startup — reconnect does not ghost-revive unavailable devices
- The AsyncAPI registry snapshot and heartbeat are also re-published on reconnect, ensuring broker-side state is consistent after a broker restart

### Additional Negative Consequences

- One idempotent re-announce per reconnect adds a small burst of retained-publish traffic (one message per registered device plus registry and heartbeat); negligible for typical deployments
- Availability is published on MQTT connect, which may precede full adapter-entry completion — availability signals broker connectivity, not adapter readiness
- Adapters without `MqttConnectAware` (mock/null test doubles) retain eager inline startup publishes; this is intentional but creates two code paths that must both be maintained
