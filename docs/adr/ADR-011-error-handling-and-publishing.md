# ADR-011: Error Handling and Publishing

## Status

Accepted **Date:** 2026-02-14

## Context

cosalette applications run as unattended daemons. When errors
occur (invalid commands, hardware failures, out-of-range values), there is no user
present to observe them. Errors must be reported to a remote monitoring system via MQTT
so that operators can detect and diagnose problems without SSH-ing into individual
devices.

The velux2mqtt reference implementation includes a 251-line `ErrorPublisher` that
converts domain exceptions into structured JSON payloads and publishes them to MQTT
error topics. This pattern needs to be generalised: velux2mqtt maps specific domain
error classes (`InvalidCommandError`, `PositionOutOfRangeError`, etc.) to machine-
readable `error_type` strings — the framework must make this mapping pluggable while
providing the publication machinery.

Key design requirements from the reference implementation:

- Errors are published as structured JSON (not plain text)
- Publication is fire-and-forget — a failed error publication must not crash the daemon
- Both global and per-device error topics are used
- Wall-clock timestamps (not monotonic) for operator correlation with real time
- Errors are logged locally AND published to MQTT (dual observability)

## Decision

Use **structured `ErrorPayload` → JSON → MQTT** with **pluggable error type mapping**,
**fire-and-forget publishing**, and **per-device + global error topics** because
unattended daemon operation requires observable, machine-parseable error reporting that
never crashes the main control loop.

### Error payload schema

```json
{
  "error_type": "invalid_command",
  "message": "Invalid command: 'hello' (not a recognised command)",
  "device": "blind",
  "timestamp": "2026-02-14T12:34:56+00:00",
  "details": {"payload": "hello"}
}
```

### Topic layout

```text
{app}/error              ← all errors (global, always published)
{app}/{device}/error     ← per-device errors (when device name is known)
```

### Publication behaviour

- **Not retained** — errors are events, not last-known state
- **QoS 1** — at-least-once delivery; errors should survive brief network hiccups
- **Fire-and-forget** — publication failures are logged but never propagated
- **Dual output** — errors are both logged locally and published to MQTT

### Pluggable error types

The framework provides a base `ErrorPublisher` with `build_error_payload()`.
Projects register their own domain error → `error_type` string mappings:

```python
_ERROR_TYPE_MAP: dict[type[DomainError], str] = {
    InvalidCommandError: "invalid_command",
    PositionOutOfRangeError: "position_out_of_range",
}
```

## Decision Drivers

- Unattended daemon operation — no local user to observe errors
- Machine-parseable error format for monitoring dashboards
- Fire-and-forget — error reporting must never crash the main application
- Per-device granularity for targeted alerting
- Pluggable error types — each project has its own domain error hierarchy
- Wall-clock timestamps for operator correlation with real-world events

## Considered Options

### Option 1: Logging only

Report errors through the logging system exclusively (JSON log lines).

- *Advantages:* Simple, no additional infrastructure. Log aggregators can capture
  errors from the log stream.
- *Disadvantages:* Requires a log aggregation system to be deployed and configured
  (not yet available). Does not enable MQTT-based monitoring dashboards. Cannot
  trigger HA automations on errors. Mixes error signals with operational logs.

### Option 2: Exception propagation

Let exceptions propagate to a global handler that logs and optionally publishes.

- *Advantages:* Standard Python error handling. Less infrastructure code.
- *Disadvantages:* Global handlers lose per-device context. Unhandled exceptions
  can crash the daemon. Does not support the fire-and-forget requirement.

### Option 3: Dead letter queue

Publish failed messages to a dead letter topic for later analysis.

- *Advantages:* No message loss, supports replay and forensic analysis.
- *Disadvantages:* Over-engineered for the scope. Requires infrastructure for
  queue management. The devices are simple IoT bridges — error events are
  informational, not transactional.

### Option 4: Structured ErrorPayload → MQTT (chosen)

Convert domain errors to structured JSON payloads and publish to MQTT error topics
with fire-and-forget semantics.

- *Advantages:* Machine-parseable errors for monitoring. Fire-and-forget ensures
  the daemon never crashes due to error reporting. Per-device + global topics
  enable both targeted and aggregate monitoring. Pluggable error type mapping
  supports project-specific domain errors. Clock injection enables deterministic
  test assertions.
- *Disadvantages:* Adds MQTT publishing overhead for every error (mitigated by
  QoS 1, small payloads). Error schema becomes a contract that must be maintained.

## Decision Matrix

| Criterion                    | Logging Only | Exception Propagation | Dead Letter Queue | Structured MQTT |
| ---------------------------- | ------------ | --------------------- | ----------------- | --------------- |
| Remote observability         | 2            | 2                     | 4                 | 5               |
| Resilience (fire-and-forget) | 4            | 1                     | 3                 | 5               |
| Per-device granularity       | 2            | 1                     | 3                 | 5               |
| Machine parseability         | 3            | 2                     | 4                 | 5               |
| Implementation complexity    | 5            | 4                     | 2                 | 3               |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Operators can monitor all deployed applications by subscribing to `+/error`
- Machine-parseable JSON enables dashboards, alerting, and Home Assistant automations
- Fire-and-forget publication ensures errors never cascade into application crashes
- Per-device error topics allow targeted monitoring of specific hardware
- Pluggable error type mapping lets each project define its own domain error vocabulary
- Dual output (log + MQTT) provides both local and remote observability

### Negative

- Error schema (`error_type`, `message`, `actuator`, `timestamp`, `details`) becomes
  a contract — changes require coordinated updates to monitoring consumers
- Fire-and-forget means error publication failures are silently logged — errors about
  errors could be missed
- Per-device + global topic publishing doubles MQTT messages for device-specific errors

_2026-02-14_
