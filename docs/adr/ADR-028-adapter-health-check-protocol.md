# ADR-028: Adapter Health Check Protocol

## Status

Accepted **Date:** 2026-04-02

## Context

cosalette adapters (BLE, serial, GPIO) can enter a wedged state — a BlueZ daemon crash,
a hardware reset, or a disconnected serial cable — where all operations fail
indefinitely. Today, the framework publishes `"online"` availability as long as the
process is running, regardless of whether the adapter is actually functional. Operators
must notice repeated errors in logs and manually restart the container.

A health check protocol lets adapters report their own readiness. The framework
periodically probes adapters that implement the protocol, and sets per-device availability
to `"offline"` when the adapter is unhealthy. This is *detection and reporting only* —
automatic restart belongs to Epic 6.

### Existing infrastructure

- `HealthReporter` ([_health.py](../reference/api.md)) manages per-device availability
  topics (`{prefix}/{device}/availability`) with retained `"online"`/`"offline"` messages.
- Adapter lifecycle uses duck-typed `__aenter__`/`__aexit__` (ADR-016).
- DI injection plans (`build_injection_plan()`) record which types each device handler
  requests — this is introspectable at wiring time.
- `TelemetryRunner` already calls `health_reporter.set_device_status("error")` on
  telemetry failures, but this only affects the heartbeat payload, not the availability
  topic.

## Decision

### 1. HealthCheckable Protocol

Add a `@runtime_checkable` structural protocol:

```python
@runtime_checkable
class HealthCheckable(Protocol):
    async def health_check(self) -> bool: ...
```

The framework detects adapters implementing this protocol via `isinstance()` after
adapter lifecycle entry, consistent with the duck-typing detection pattern used for
`__aenter__`/`__aexit__` (ADR-016).

### 2. Global health check interval

A single `health_check_interval` parameter on `App.__init__` controls the check
frequency for all adapters.

```python
App(
    health_check_interval=30.0,  # seconds, default 30; None disables
)
```

Per-adapter overrides are deferred — 30 seconds is sufficient for all current adapter
types (BLE, serial, GPIO). The interval controls detection latency, not precision;
the difference between 30s and 60s is cosmetic for availability reporting.

### 3. Failure behaviour: availability only

When `health_check()` returns `False`:

- Set availability to `"offline"` for all devices that depend on the adapter.
- Log a WARNING with the adapter type and failure count.

When `health_check()` returns `True` after a failure:

- Restore availability to `"online"` for affected devices.
- Log an INFO recovery message.

Telemetry polling **continues** during unhealthy periods. This keeps the health check
informational — pausing telemetry is a form of "acting on health" that belongs in
Epic 6 (auto-restart). The existing error deduplication suppresses consecutive
identical telemetry errors, limiting log noise.

### 4. HealthCheckable is independent of adapter lifecycle

An adapter can implement `health_check()` without implementing `__aenter__`/`__aexit__`.
The framework calls `health_check()` on any adapter that satisfies the
`HealthCheckable` protocol, regardless of lifecycle support.

This matters for Epic 6: auto-restart only works for adapters with lifecycle methods.
A stateless adapter can report unhealthy but cannot be restarted.

### 5. Adapter→device mapping via DI introspection

The framework builds the adapter→device mapping automatically by scanning each device
handler's `injection_plan` at wiring time:

```python
# For each device registration:
adapter_deps = {
    t for _, t in reg.injection_plan
    if t not in KNOWN_INJECTABLE_TYPES and t in resolved_adapters
}
# adapter_deps → set of adapter port types this device depends on
```

This produces a `dict[type, list[str]]` mapping each adapter port type to its dependent
device names. No user configuration required — the mapping is derived from the same
type annotations already used for DI.

### 6. Startup health check

The framework runs one initial health check for each `HealthCheckable` adapter **before
starting device tasks**. If the check fails:

- Affected devices start with availability `"offline"` (instead of the default
  `"online"`).
- A WARNING is logged.
- Device tasks still start — the failure is non-blocking.

This ensures availability is accurate from the first heartbeat, rather than being
optimistically `"online"` until the first periodic check runs.

## Decision Drivers

- Operators need automated detection of wedged adapters without log-spelunking
- Health check is informational (detection + reporting) — restart logic is separate
- Framework conventions: `@runtime_checkable` protocols, duck-typed detection,
  type-based DI
- Simplicity for v1 — defer per-adapter intervals and telemetry pausing

## Considered Options

### Health check interval

| Option | API | Pros | Cons | Chosen |
| ------ | --- | ---- | ---- | ------ |
| **A: Global only** | `App(health_check_interval=30)` | Simple, one setting | BLE may want different interval | **Yes** |
| B: Per-adapter override | Global + per-adapter | Flexible | Extra API surface, configuration complexity | No |
| C: Protocol method | `health_check_interval() -> float` | Adapter knows best | Framework can't control; hard to override in settings | No |

### Failure behaviour

| Option | Behaviour | Pros | Cons | Chosen |
| ------ | --------- | ---- | ---- | ------ |
| **A: Availability only** | Set offline, telemetry continues | Simple, informational | Log noise from expected failures | **Yes** |
| B: Availability + pause telemetry | Set offline, skip telemetry | Clean logs | More complex, belongs in Epic 6 | No |

### Adapter→device mapping

| Option | Mechanism | Pros | Cons | Chosen |
| ------ | --------- | ---- | ---- | ------ |
| **A: DI introspection** | Scan `injection_plan` | Automatic, zero config | Introspection at wiring time | **Yes** |
| B: Explicit mapping | `App(adapter_devices={...})` | Clear | Manual, error-prone, duplicates DI | No |

## Consequences

### Positive

- Wedged adapters are detected and reported automatically via availability topics
- Operators and home automation systems (Home Assistant) see accurate device
  availability without manual intervention
- Zero-config adapter→device mapping via DI introspection — no user burden
- Consistent with existing protocol conventions (`@runtime_checkable`, duck-typing)
- Non-breaking — adapters that don't implement `HealthCheckable` are unaffected
- Consecutive failure count is tracked, providing the foundation for Epic 6
  (auto-restart)

### Negative

- Telemetry continues during unhealthy periods, producing expected errors
  (mitigated by error deduplication)
- Global interval may not suit all adapter types (mitigated by the fact
  that detection latency ≤30s is acceptable for availability reporting)
- DI introspection for adapter→device mapping adds build-time complexity
  (one-time scan, not a runtime cost)

_2026-04-02_
