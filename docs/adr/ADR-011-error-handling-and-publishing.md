---
status: Accepted
date: 2026-02-14
impact: moderate
tags: [error-handling, mqtt]
---

# ADR-011: Error Handling and Publishing

## Status

Accepted **Date:** 2026-02-14 | Amended **Date:** 2026-07-24 | Amended **Date:** 2026-10-01 | Amended **Date:** 2026-10-12

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

## Amendment (2026-10-01) — Additive

**Rationale:** A publish that fails because the broker is not connected is not a handler failure, yet it was reported through the full ADR-011 error path: an ERROR log, an error publish that itself failed and logged a traceback, and an 'error' health status. The error report cannot reach the broker anyway, so the path only produced log noise and a misleading health state.

### Additional Sub-Decision: Not-Connected Is a Transport Condition

`MqttClient.publish()` raises the typed `MqttNotConnectedError(RuntimeError)` when no broker connection exists; subclassing `RuntimeError` keeps existing `except RuntimeError` code working. The telemetry runner treats it as a transport condition: it logs at `DEBUG`, publishes nothing to the error topics, leaves the entity's health status, availability and error de-duplication state untouched, and retries on the next interval. `ErrorPublisher` logs a not-connected failure of its own fire-and-forget publish at `DEBUG` without a traceback; every other publish failure keeps the existing `ERROR`-with-traceback logging. Device generators are unchanged: an exception escaping a generator still ends it.

### Additional Positive Consequences

- Broker outages no longer produce error-topic attempts, tracebacks or 'error' health states for healthy telemetry handlers.
- Applications can catch MqttNotConnectedError precisely instead of matching a RuntimeError message.

### Additional Negative Consequences

- A telemetry handler that keeps hitting a disconnected broker is only visible at DEBUG level; the heartbeat and LWT remain the outage signal.

## Amendment (2026-10-01) — Additive

**Rationale:** The earlier 2026-10-01 amendment left device generators unchanged, so an @app.device or @app.stream handler that called ctx.publish_state() during a broker outage received MqttNotConnectedError, which escaped the generator. run_device then reported a crash, published an error that could not reach the broker, and never restarted the handler: one outage permanently ended the device. This amendment extends the transport-condition treatment to device and stream handler publishes.

### Additional Sub-Decision: Device and Stream Handler Publishes Tolerate Not-Connected

`DeviceContext` takes a `tolerate_not_connected` flag. The framework sets it on contexts built for `@app.device` and `@app.stream` handlers, and `AppHarness` matches that for streams. On those contexts `publish_state()`, `publish()`, `SubEntityContext.publish_state()` and the `sub_entity()` availability/cleanup publishes log `MqttNotConnectedError` at `DEBUG` and return instead of raising, so the generator keeps running and its next publish reaches the broker after reconnect. Every other exception still propagates. Telemetry and command contexts keep raising, because the telemetry runner relies on the exception to leave `last_published` unchanged: a swallowed publish would make an `OnChange` strategy believe the value was delivered and skip republishing it after reconnect. Device names never share a context with telemetry or commands, so the flag is unambiguous per context. This replaces the earlier statement that device generators are unchanged for not-connected publishes.

### Additional Considered Options

**Supervised device restart**

Let MqttNotConnectedError escape the generator and have run_device wait for reconnect, then re-invoke the handler and its init.

- *Advantages:* No publish is silently dropped from the handler's point of view; Handlers see the outage as an exception they could react to
- *Disadvantages:* Re-runs init and discards all in-generator state; Needs reconnect signalling and restart machinery in run_device

**Document-only handler responsibility**

Keep raising and document that device handlers must catch MqttNotConnectedError themselves.

- *Advantages:* No framework change
- *Disadvantages:* Every app must know about the footgun; one missed call site ends the device on the first outage

### Additional Positive Consequences

- A broker outage no longer permanently ends @app.device or @app.stream handlers.

### Additional Negative Consequences

- A device or stream that publishes only on change loses the update made during the outage until it next publishes; retained sub-entity availability is not re-asserted until the sub-entity is re-entered.
- Code that constructs DeviceContext directly (custom harnesses, tests) gets the strict raising behaviour unless it passes tolerate_not_connected=True.

## Amendment (2026-10-12) — Additive

**Rationale:** Rejected typed-command input (a payload that fails JSON decoding or schema validation and raises the public `PayloadValidationError`) had no framework error type, so it was published as `error_type: "error"` with the class name as message, the same shape as a crash in handler code. The framework owns both this exception and its sanitised summary, so it also owns the label. Putting a public exception type into the framework map makes the merge-precedence rule of the 2026-07-24 amendment reachable in practice, so a shadowed app entry needs a visible signal. Tracked as cos-ked4.3 (epic cos-ked4).

### Additional Sub-Decision: Framework label for rejected typed-command input: invalid_payload

The framework error map contains the public exception type `PayloadValidationError` with the label `invalid_payload`, next to `InvalidJsonError` → `invalid_json`, `MissingSubKeyError` → `missing_sub_key`, `UnknownSubCommandError` → `unknown_sub_command` and the builtin `TimeoutError` → `timeout`. Both schema failures and JSON decode failures on typed `@app.command` payloads raise `PayloadValidationError` and are therefore published as `invalid_payload`. Map membership has the usual disclosure effect: under the legacy rule (no `disclose_messages_for`) the sanitised validation summary from `_safe_error_summary` is published; under an explicit `disclose_messages_for` set the message is disclosed only if the set contains `PayloadValidationError` (see ADR-061). The summary never contains pydantic `msg` text or input values (ADR-045/046).

### Additional Sub-Decision: ReturnValidationError stays unlabelled

`ReturnValidationError` (a handler returned a value that does not match its declared return contract) is not added to the framework map. It signals an application fault and keeps the generic `error` label and class-name message, like any other unmapped exception.

### Additional Sub-Decision: Shadowed app entries produce a startup warning

Merge precedence is unchanged: `create_services` builds `{**app_map, **_FRAMEWORK_ERROR_TYPE_MAP}` and framework entries win. Before merging, it logs one `WARNING` per app `error_type_map` key that is also a framework key with a **different** label, naming the exception type, the app label and the framework label (for example `error_type_map entry PayloadValidationError -> 'validation' is overridden by the framework label 'invalid_payload'`). An app entry with the same label as the framework entry is harmless and produces no warning. The warning is emitted once at startup, not per published error.

!!! note "Editorial note (2026-10-12)"
    Correction to the 2026-07-24 amendment, sub-decision 'Merge precedence — framework entries authoritative': the framework command exception types and `_FRAMEWORK_ERROR_TYPE_MAP` live in `cosalette._runners._command_errors` (kept apart from the command runner so the error publisher can map them without loading it, cos-8jxg.5), not in `cosalette._runners._command_runner`. With `PayloadValidationError` in the framework map, a conflict between an app entry and a framework entry is reachable; the statements that such a conflict is 'practically unreachable' and that the framework mapping 'silently prevails' are replaced by the sub-decisions of this amendment. Framework entries remain authoritative.

!!! note "Editorial note (2026-10-12)"
    Breaking change (0.12.0): every app's error-topic payload for rejected typed-command input changes from `error_type: "error"` / `message: "PayloadValidationError"` to `error_type: "invalid_payload"`, with the message governed by the disclosure rules of ADR-061. Consumers that filter on `error_type == "error"` no longer see these events.

### Additional Positive Consequences

- Consumers can tell rejected command input (`invalid_payload`) apart from handler crashes (`error`) without app-specific configuration, and typed commands and sub-command groups both get a framework label for bad input.
- Apps that mapped `PayloadValidationError` themselves get an explicit startup signal that their entry is overridden and can be removed.

### Additional Negative Consequences

- Error-topic payloads for rejected typed-command input change for every app, which requires a breaking-change release (0.12.0) and may require consumer filter updates.
- Under the legacy disclosure rule the sanitised validation summary of rejected input becomes broker-visible for every app, not only for apps that registered `PayloadValidationError` themselves.

## Amendment (2026-10-12) — Additive

**Rationale:** The shared PayloadValidationError is also used for typed telemetry trigger input, and validation locations can contain caller-controlled mapping keys or extra-field names. The public disclosure rules need to record both the telemetry impact and the stronger sanitization boundary.

### Additional Sub-Decision: Validation summaries redact string location components

`_safe_error_summary` replaces every string component of a Pydantic location with `[field]` while retaining integer sequence indexes and framework-owned error type codes. A string location may be a declared model field, but it may also be an untrusted mapping key or extra-field name, and the validator does not have enough schema context to distinguish these safely. This preserves actionable error categories without echoing input-controlled names to broker-visible topics. The `invalid_payload` label and disclosure behavior also apply to typed `Annotated[T, Payload()]` parameters on triggerable telemetry handlers, because those handlers use the same `PayloadValidationError` and `ErrorPublisher` path.

### Additional Positive Consequences

- Broker-visible typed validation summaries no longer expose caller-controlled object keys through Pydantic locations.

### Additional Negative Consequences

- Field names are no longer included in validation summaries; every string location component appears as `[field]`, which reduces diagnostic specificity.

## Amendment (2026-10-12) — Additive

**Rationale:** A command payload that fails JSON decoding or schema validation is expected bad input that the framework handles correctly, yet it was logged like a handler fault: an ERROR line from the command runner plus the ErrorPublisher WARNING carrying the full chained traceback. The ADR records the log severity of handler faults but not of input the framework rejects itself. Tracked as cos-ked4.6 (epic cos-ked4).

### Additional Sub-Decision: Severity policy for framework-rejected command input

Command input that the framework rejects before any handler code runs is logged once, at `WARNING`, without a traceback. This covers a `PayloadValidationError` raised while binding a typed `@app.command` payload to the handler parameters (JSON decode or schema failure) and the sub-command proxy errors `InvalidJsonError`, `MissingSubKeyError` and `UnknownSubCommandError`. The command runner writes no `ERROR` line for this input and publishes with `ErrorPublisher.publish(..., log_traceback=False)` (the parameter from ADR-081, forwarded by `publish_error_safely`). For binding failures the command runner logs the chained exception at `DEBUG` with its traceback, so the pydantic detail stays available through a per-logger level override. `UnknownSubCommandError` keeps its local `WARNING` with the raw sub-command value, because the published message only carries a fingerprint.

### Additional Sub-Decision: Handler faults keep ERROR plus traceback

Only the framework's input-binding step and the sub-command routing checks are downgraded. Any exception raised by handler code, including a `PayloadValidationError` the handler raises itself, keeps the existing behaviour: one `ERROR` line from the command runner and the publish `WARNING` with the traceback. Device command proxies (`_dispatch_handler`) never parse payloads in the framework and are unchanged. The error-topic payload is identical for all paths; the policy affects local logs only.

### Additional Positive Consequences

- One rejected payload produces one log line instead of an ERROR plus a traceback of about 50 lines, and alert rules on `level=ERROR` no longer fire on bad input.
- The chained pydantic error, which contains the raw input value, is not written to the local log at the default level.

### Additional Negative Consequences

- Operators who alerted on the ERROR line for bad command input see WARNING instead and must adjust such rules.
- Diagnosing a rejected payload in detail requires enabling DEBUG for `cosalette._runners._command_runner`, and the DEBUG record is not tagged with the publish correlation id.

## Amendment (2026-10-12) — Additive

**Rationale:** The unknown sub-command warning originally retained the raw value for local diagnostics. That value may contain sensitive input, so local logging should follow the same minimization policy as the published error.

### Additional Sub-Decision: Privacy of unknown sub-command warnings

The local WARNING for an unknown sub-command logs only the value's type, length and non-reversible fingerprint. It does not include the raw value, which may contain sensitive input.
