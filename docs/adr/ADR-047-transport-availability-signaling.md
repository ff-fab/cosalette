---
status: Accepted
date: 2026-06-23
impact: moderate
tags: [health, mqtt, lifecycle, devices, error-handling]
---

# ADR-047: Transport Availability Signaling

## Status

Accepted **Date:** 2026-06-23

## Context

Many adapters wrap fallible transports — SSH, BLE, serial — where a transport-layer failure makes a device temporarily unavailable. Without a framework convention, each adapter catches exceptions independently and emits an `"offline"` payload to an availability topic. This pattern was independently reinvented by at least three downstream adapters: `wallpanel-control` (SSH), `airthings2mqtt` (BLE), and `vito2mqtt` (serial), resulting in duplicated error-handling boilerplate across the ecosystem.

The framework already provides first-class availability support for *telemetry devices* via `HealthCheckRunner` and `HealthReporter`, which execute periodic health probes on a schedule and publish `"online"` / `"offline"` to `{prefix}/{device}/availability`. Command handlers — the primary integration surface for actuator adapters — have no equivalent mechanism. When a command handler raises an exception from a transport call, the framework propagates it to the MQTT dispatch loop but never publishes an availability update.

Home Assistant's MQTT integration relies on a retained `availability` topic to gate entity state — without a framework-level mechanism, adapters must manually handle the three-way interaction of exception catching, availability publishing, and eventual recovery, adding roughly 15–25 lines of boilerplate per device.

## Decision

Introduce first-class transport-level availability signaling for command handlers via two complementary forms: a static `unavailable_on` parameter on `@app.command` and a dynamic `ctx.mark_unavailable()` method on `DeviceContext`. Reuse the existing `HealthReporter` infrastructure by injecting it into `DeviceContext` as an optional `_health_reporter` field. Auto-recovery publishes `"online"` after any successful handler invocation when `ctx._is_unavailable` is `True`, eliminating the need for explicit recovery logic in adapters.

```python
from cosalette import App
from cosalette.devices import DeviceContext
from paramiko import SSHException

app = App("wallpanel")

# Static form — unavailable_on on the decorator
# On SSHException or TimeoutError:
#   1. publishes "offline" to {prefix}/panel/availability
#   2. logs to error topic
#   3. sets ctx._is_unavailable = True
# On next *successful* invocation, auto-publishes "online".
@app.command("panel", unavailable_on=(SSHException, TimeoutError))
async def run_command(ctx: DeviceContext, cmd: str) -> str:
    result = await ctx.ssh.run(cmd)
    return result


# Dynamic form — ctx.mark_unavailable() called explicitly
# Useful when the failure condition is detected by application logic
# rather than by an exception type.
@app.command("ble_sensor")
async def poll_sensor(ctx: DeviceContext) -> dict:
    readings = await ctx.ble.scan(timeout=5)
    if not readings:
        await ctx.mark_unavailable()   # publishes "offline"
        return {}
    return readings
```

## Decision Drivers

- Eliminate 15–25 lines of per-adapter boilerplate for the unavailability pattern, which has been reinvented independently by three downstream adapters
- Enable Home Assistant MQTT integration to consume a standard retained availability topic without adapter-specific configuration
- Auto-recovery after successful handler invocation removes the need for separate 'come back online' logic in each adapter
- Reuse the existing HealthReporter infrastructure (publish_device_available / publish_device_unavailable) to avoid a parallel availability mechanism
- Preserve handler simplicity — adapters that do not need availability signaling are completely unaffected

## Considered Options

### Option 1: Framework-level transport signaling (chosen) (chosen)

Add `unavailable_on` parameter to `@app.command` for static exception-based signaling and `ctx.mark_unavailable()` to `DeviceContext` for dynamic signaling. The framework wraps handlers in a `try/except tuple(unavailable_on)` block, calls `health_reporter.publish_device_unavailable()` on match, logs to the error topic, and sets `ctx._is_unavailable = True`. After any successful invocation, the framework checks `ctx._is_unavailable` and auto-publishes `"online"` via `health_reporter.publish_device_available()`. `HealthReporter` is injected into `DeviceContext` as `_health_reporter: HealthReporter | None` during device context construction.

- *Advantages:* Eliminates per-adapter boilerplate — adapters declare their failure exceptions once on the decorator; Auto-recovery is transparent: no adapter code needed to publish 'online' after reconnect; Reuses HealthReporter, which already manages the availability topic contract (retained, QoS 1); Both static and dynamic forms compose — an adapter can use unavailable_on for transport exceptions and ctx.mark_unavailable() for application-level conditions; Device-scoped state means all command handlers on a device share one availability flag, matching HA's entity model
- *Disadvantages:* DeviceContext gains a new optional dependency on HealthReporter, breaking the current separation between the command dispatch layer and the health layer; Only applies to @app.command handlers; @app.device (ctx.commands() loop pattern) must use ctx.mark_unavailable() manually; The implicit try/except wrapping may obscure exception flow for adapters that need to perform cleanup on the same exception types

### Option 2: Per-adapter manual availability publishing

Continue the current practice where each adapter is responsible for catching transport exceptions, publishing to the availability topic via `app.mqtt.publish()`, and handling recovery. Document the pattern in the framework guide without encoding it in the framework itself.

- *Advantages:* Zero framework changes — no new coupling between DeviceContext and HealthReporter; Full adapter control over availability semantics (e.g. retry before marking unavailable, custom back-off); Exception flow is explicit and visible in adapter code
- *Disadvantages:* 15–25 lines of boilerplate per device, reinvented independently across multiple adapters — the status quo that motivates this ADR; Inconsistent topic naming and retention settings across adapters — no framework contract; No auto-recovery mechanism: every adapter must also implement the 'come back online' path; Documentation-only guidance is harder to discover and easier to skip

### Option 3: Health probe extension

Extend `HealthCheckRunner` to accept an optional `on_failure` exception tuple. When a health probe raises a matching exception, the runner marks the device unavailable. Command handler failures do not themselves trigger availability updates; instead, a frequent health probe (e.g. every 5 seconds) detects the transport failure.

- *Advantages:* No changes to DeviceContext or @app.command decorator API; HealthCheckRunner already manages the schedule-based availability contract; Health probe frequency is independently tunable without touching handler code
- *Disadvantages:* Availability update lags behind the actual failure by up to one health probe interval — unacceptable for HA entity state; Requires defining a separate health probe function that duplicates transport connectivity logic already present in handlers; Does not help adapters that have no natural health probe entry point (e.g. BLE scanners that only produce data on command); Auto-recovery still requires explicit probe success to publish 'online'

## Decision Matrix

| Criterion | Framework-level transport signaling (chosen) | Per-adapter manual availability publishing | Health probe extension |
| --- | --- | --- | --- |
| Eliminates per-adapter boilerplate | 5 | 1 | 2 |
| Availability update latency (lower is better; 5 = immediate) | 5 | 3 | 2 |
| Auto-recovery without adapter code | 5 | 1 | 3 |
| HealthReporter infrastructure reuse | 5 | 1 | 5 |
| Minimal impact on DeviceContext API | 3 | 5 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Adapter authors declare transport failure exceptions once on @app.command (unavailable_on parameter) and get automatic availability publishing, error logging, and recovery for free.
- Home Assistant MQTT integration can subscribe to the standard {prefix}/{device}/availability retained topic without adapter-specific configuration.
- Auto-recovery is transparent: the framework publishes "online" after the first successful command invocation following an unavailable state, removing a common source of 'device stuck offline' bugs in adapters.
- HealthReporter.publish_device_available() and publish_device_unavailable() are reused unchanged — the new mechanism is an additional call site, not a new availability contract.

### Negative

- DeviceContext gains a new optional _health_reporter: HealthReporter | None field, introducing a dependency from the command dispatch layer into the health layer. This breaks the current separation and must be documented as an intentional architectural trade-off.
- Implicit try/except wrapping via unavailable_on may obscure exception flow for adapters that need cleanup on the same exception types; such adapters should use ctx.mark_unavailable() in an explicit except block instead.
- @app.device handlers using the ctx.commands() coroutine loop pattern must call ctx.mark_unavailable() explicitly; the unavailable_on decorator parameter does not apply to that pattern.

_2026-06-23_
