---
status: Accepted
date: 2026-02-14
impact: moderate
tags: [error-handling, mqtt]
---

# ADR-011: Error Handling and Publishing

## Status

Accepted **Date:** 2026-02-14 | Amended **Date:** 2026-07-24

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

## Amendment (2026-07-24) — Additive

**Rationale:** The 0.5.6 LEAK-01 hardening made build_error_payload default-deny (an exception whose type is not in the ErrorPublisher's error_type_map has its message redacted to the class name) but left no consumer-facing way to register app-level exception types into that map: create_services built the publisher with the framework command-exception map alone, and App.__init__/run/decorators took no error_type_map parameter. This silently contradicted this ADR's original 'pluggable error type mapping' decision, redacting domain exceptions that carry intentionally safe messages and degrading MQTT error diagnostics. This amendment documents the restored consumer opt-in hook and the merge precedence that keeps LEAK-01's default-deny intact.

### Additional Sub-Decision: LEAK-01 default-deny message redaction

`build_error_payload` publishes the raw `str(error)` only for exceptions whose exact type is present in the ErrorPublisher's `error_type_map`. For any unlisted (downstream/unexpected) exception it publishes only the class name, because such exception text can carry secrets (e.g. CalDav URLs with embedded credentials, broker passwords, tokens) and the error topics are broker-visible. The full message and traceback are always logged locally under a correlation `id`. The global `MqttSettings.error_publish_verbose` flag (env `MQTT__ERROR_PUBLISH_VERBOSE`) un-redacts every exception in the process and is retained as a blunt operator escape hatch.

### Additional Sub-Decision: Consumer error_type_map opt-in hook

Apps register their domain exception → `error_type` string map via the `App(error_type_map=...)` constructor parameter — the single configuration surface, evaluated at construction where wiring needs it (no duplicate `run()`/decorator surface to keep in sync). Keys must be exception classes and values `error_type` strings; both are validated at construction and a wrong key/value raises `TypeError` rather than silently never matching. Registering a type opts it back into full-message publishing under LEAK-01; unlisted types stay redacted. This restores this ADR's original pluggable-mapping intent that the 0.5.6 hardening inadvertently closed, without loosening the default.

```python
app = cosalette.App(
    name="caldates2mqtt",
    error_type_map={
        CalDavConnectionError: "caldav_connection_error",
        CalDavNotFoundError: "caldav_not_found",
    },
)
```

### Additional Sub-Decision: Merge precedence — framework entries authoritative

`create_services` builds the ErrorPublisher's map as `{**app_map, **_FRAMEWORK_ERROR_TYPE_MAP}` — the app map extended by the framework map, so **framework command exceptions win on conflict**. An app cannot override or shadow framework error handling; app entries only extend the map for app-owned types. Framework exception types are private (`cosalette._runners._command_runner`), so a genuine conflict is practically unreachable; on the rare intersection the framework mapping silently prevails.

### Additional Positive Consequences

- Apps opt specific domain exception types back into full-message publishing without the global verbose flag, so unrelated exception messages stay redacted (LEAK-01 preserved).
- Existing app-side error_type_maps (jeelink2mqtt, vito2mqtt) become live again, closing latent redaction of their domain messages.

### Additional Negative Consequences

- The app-registered map is a security-relevant surface: an app that registers an exception type whose message can carry secrets re-opens that specific leak for that type, so registration is an explicit per-type decision the app owns.
- error_type_map keys are matched by exact type (no subclass matching), so a subclass of a registered domain exception is still redacted unless separately registered.
