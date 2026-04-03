# ADR-029: Adapter Auto-Restart Strategy

## Status

Accepted **Date:** 2026-04-03

## Context

ADR-028 introduced the adapter health check protocol — periodic probing of
`HealthCheckable` adapters with availability reporting. When an adapter becomes
unhealthy, the framework sets affected devices to `"offline"` and logs warnings, but
takes no corrective action. Operators must still manually restart the container to
recover from a wedged adapter (BlueZ crash, serial port glitch, hardware reset).

Auto-restart closes the loop: detect → restart → recover, without human intervention.
This is critical for unattended deployments where adapters may experience transient
failures hours or days apart.

### Existing infrastructure

- `HealthCheckRunner` tracks `consecutive_failures` per adapter via
  `AdapterHealthStatus` (ADR-028).
- Adapter lifecycle uses duck-typed `__aenter__`/`__aexit__` managed by an
  `AsyncExitStack` (ADR-016).
- `build_adapter_device_map()` maps adapter types to dependent device names
  via DI introspection (ADR-028).
- Device tasks are created by `start_device_tasks()` and cancelled by
  `cancel_tasks()` during shutdown.
- Commands are received via MQTT subscriptions and routed to device handlers
  by the command router (ADR-025).

## Decision

### 1. Restart trigger: consecutive failure threshold

When an adapter's `consecutive_failures` reaches a configurable threshold, the
framework initiates a restart. The threshold is set via `App.__init__`:

```python
App(
    restart_after_failures=5,  # default: 5; 0 disables auto-restart
)
```

Five consecutive failures at the default 30-second health check interval gives
the adapter 2.5 minutes to recover naturally before the framework intervenes.
This avoids restarting on transient blips while still responding within a
reasonable window.

### 2. Restart mechanism: cancel + recreate

On restart, the framework:

1. Cancels all device tasks that depend on the failing adapter.
2. Calls `adapter.__aexit__()` to tear down the adapter.
3. Waits for the cooldown period (Decision 4).
4. Calls `adapter.__aenter__()` to re-initialise the adapter.
5. Runs a startup health check to verify the adapter recovered.
6. Recreates cancelled device tasks from their original registrations.
7. Resets `consecutive_failures` to 0.

Device tasks are cancelled and recreated from scratch rather than suspended.
This ensures a clean state with no stale references to the old adapter instance.
`@app.telemetry` handlers are stateless by design, so recreation is seamless.
`@app.device` handlers lose in-flight state (local variables, closures), but
`DeviceStore` data survives because it is persisted (ADR-015).

This trade-off is acceptable: an adapter restart implies the underlying hardware
experienced a failure, so any in-memory state derived from hardware interaction
is likely stale anyway. Adopter projects should persist critical device state
via `DeviceStore` if it must survive restarts.

### 3. Max restarts with sustained health reset

A configurable maximum prevents restart loops:

```python
App(
    max_restarts=3,  # default: 3; per adapter, lifetime limit
    sustained_health_reset=300.0,  # seconds (5 min); resets restart counter
)
```

When `max_restarts` is exhausted, the adapter stays offline permanently until
the container is restarted. A CRITICAL-level log message is emitted.

To handle adapters with rare, widely-spaced failures (e.g., a hardware glitch
once every few days), the restart counter resets to 0 after
`sustained_health_reset` seconds of consecutive healthy checks. The default of
5 minutes provides confidence that the adapter has genuinely recovered, not
merely passed a single check before failing again.

### 4. Restart cooldown

A configurable delay between `__aexit__` and `__aenter__` gives the underlying
hardware and OS resources time to release:

```python
App(
    restart_cooldown=5.0,  # seconds, default: 5
)
```

Five seconds accommodates typical resource release times for Bluetooth daemons,
serial port drivers, and GPIO subsystems used by current adopter projects.
The cooldown uses shutdown-aware sleep so it can be interrupted if the
application is shutting down.

### 5. Restart opt-out

By default, all adapters that satisfy both `HealthCheckable` and the lifecycle
protocol (`__aenter__`/`__aexit__`) are eligible for auto-restart. Adapters
that implement `health_check()` but lack lifecycle methods cannot be restarted
(a WARNING is logged at startup).

Some adapters may not survive an exit + re-enter cycle — for example, hardware
that requires a one-time initialisation sequence not repeatable at runtime. These
adapters can opt out by setting a class attribute:

```python
class MyAdapter:
    restartable: ClassVar[bool] = False  # opt out of auto-restart

    async def __aenter__(self): ...
    async def __aexit__(self, *exc): ...
    async def health_check(self) -> bool: ...
```

The framework checks `getattr(adapter, 'restartable', True)` — adapters are
restartable by default unless they explicitly set `restartable = False`. This
follows the principle of safe defaults: most adapters that implement lifecycle
methods can tolerate exit + re-enter.

When an opted-out adapter's health check fails beyond the threshold, the framework
logs a WARNING indicating the adapter is unhealthy but not restartable, and
continues reporting `"offline"` availability without attempting restart.

### 6. In-flight command handling during restart

Commands that arrive while an adapter is restarting are queued and delivered
after device tasks are recreated:

- The command router holds incoming commands in a bounded queue during the
  restart window.
- After device tasks are recreated, queued commands are dispatched in order.
- If the queue overflows (unlikely given the short restart window), the oldest
  commands are dropped with a WARNING log.

Commands are asynchronous by nature — a few seconds delay during restart is
acceptable. Dropping commands silently (Option A) risks lost user actions, and
rejecting with an error (Option C) pushes retry logic onto the caller
unnecessarily.

## Decision Drivers

- Unattended deployments need automatic recovery from transient adapter failures
- Restart must be safe: no stale references, no leaked resources, no partial state
- Framework conventions: duck-typed protocols, `AsyncExitStack` lifecycle, DI-based
  wiring
- Simplicity: global thresholds over per-adapter configuration for v1
- Current adopter projects use adapters (BLE, serial, GPIO) that tolerate exit + re-enter

## Considered Options

### Device task handling during restart

| Option | Behaviour | Pros | Cons | Chosen |
| ------ | --------- | ---- | ---- | ------ |
| **A: Cancel + recreate** | Cancel tasks, restart adapter, recreate tasks | Clean state, no stale refs | Loses `@app.device` loop state | **Yes** |
| B: Suspend + resume | Pause flag, device checks it | Preserves loop state | Complex, device must cooperate | No |
| C: Replace adapter ref | Swap DI reference, devices keep running | Minimal disruption | Thread safety, stale handles | No |

### Max restarts reset

| Option | Behaviour | Pros | Cons | Chosen |
| ------ | --------- | ---- | ---- | ------ |
| A: Never reset | Hard lifetime limit | Simple | Penalises rare failures days apart | No |
| **B: Reset after sustained health** | 5 min healthy → counter resets | Handles intermittent failures | Slightly more complex | **Yes** |

### Restart opt-in vs. opt-out

| Option | Behaviour | Pros | Cons | Chosen |
| ------ | --------- | ---- | ---- | ------ |
| A: Implicit (no control) | All eligible adapters restart | Zero config | No escape hatch for fragile adapters | No |
| **B: Default-on with opt-out** | `restartable = False` to disable | Safe default, escape hatch | Class attribute convention | **Yes** |
| C: Explicit opt-in | Must implement `Restartable` protocol | Maximum control | Extra ceremony for common case | No |

### In-flight commands

| Option | Behaviour | Pros | Cons | Chosen |
| ------ | --------- | ---- | ---- | ------ |
| A: Drop | Commands lost during restart | Simple | Silent data loss | No |
| **B: Queue** | Buffer and deliver after restart | No lost commands | Bounded queue, slight delay | **Yes** |
| C: Reject | Publish error to error topic | Explicit failure | Pushes retry to caller | No |

## Consequences

### Positive

- Transient adapter failures are recovered automatically without operator
  intervention
- Clean restart via cancel + recreate avoids stale state and reference leaks
- Sustained health reset prevents permanent offline state from rare, widely-spaced
  failures
- Opt-out mechanism accommodates adapters that cannot tolerate re-initialisation
- Command queuing preserves user actions during the brief restart window
- Builds directly on ADR-028 infrastructure (`consecutive_failures`,
  adapter→device mapping, `HealthCheckable` protocol)

### Negative

- `@app.device` handlers lose in-flight state on restart — adopter projects
  must persist critical state via `DeviceStore` (mitigated by the fact that
  adapter failure likely invalidates in-memory hardware state anyway)
- Restart introduces a brief period (~5s cooldown + re-init) where affected
  devices are unresponsive
- Command queue introduces a bounded buffer that could overflow under extreme
  command rates during restart (mitigated by bounded queue with overflow logging)
- `restartable = False` is a class-level attribute, not configurable at runtime
  (sufficient for the known use case of hardware that fundamentally cannot
  re-initialise)

_2026-04-03_
