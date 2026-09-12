---
status: Accepted
date: 2026-09-12
impact: high
tags: [health, telemetry, devices, mqtt, error-handling]
---

# ADR-077: Automatic Transport Availability for the Telemetry and Device Archetypes

## Status

Accepted **Date:** 2026-09-12

## Context

Downstream feedback from an early adopter of `airthings2mqtt` (2026-09-12, fleet upgrade 0.1.5 -> 0.2.2) reports that sustained device read failures never reach the per-device retained `availability` topic. The app keeps publishing a retained `online` and a stale last reading while every poll fails, so a Home Assistant entity fed by a failing radon sensor still presents its last value as current.

ADR-047 introduced transport availability signaling for `@app.command` in two forms: the declarative `unavailable_on=(ExcType, ...)` decorator parameter, and the dynamic `ctx.mark_unavailable()`. It scoped auto-recovery to `@app.command` only, on the reasoning that telemetry devices were already covered by `HealthCheckRunner`'s periodic probes. That reasoning holds for *adapter* health probes (ADR-028) but not for a read failure raised inside the telemetry handler itself, which is the reporter's case: the adapter is healthy, the BLE read is not.

`ctx.mark_unavailable()` / `ctx.mark_available()` shipped in 0.5.10 and gives apps the primitive, but the asymmetry is in the *declarative* layer: `unavailable_on` exists only on `_CommandRegistration` (`packages/src/cosalette/_registration/_model.py`); `_TelemetryRegistration` and `_DeviceRegistration` have no such field. Evidence that the opt-in primitive does not reach app authors: it has been available for four minor releases and the reporting app has not adopted it.

What `_telemetry_runner` does today on failure is status-blob only. `_handle_telemetry_error()` calls `health_reporter.set_device_status(reg.name, "error")`, `_clear_telemetry_error()` calls `set_device_status(name, "ok")`, and `_circuit_breaker_skip()` calls `set_device_status(reg.name, "circuit_open")`. All three land in the periodic `{prefix}/status` JSON heartbeat (ADR-012) and never touch `{prefix}/{device}/availability`.

Two constraints shape the decision, both established by reading the code rather than by preference:

**A framework-provided default exception tuple is not constructible.** cosalette has no dependency on `bleak`, `paramiko`, or `pyserial`, so it cannot name `BleakError`, `SSHException`, or `serial.SerialException` in a default. Any built-in tuple is restricted to stdlib types such as `(OSError, TimeoutError)`, and whether those cover the library exceptions is not something the framework can verify. A typed default would therefore silently fail to fire for the BLE and SSH adapters ADR-047 was written for, including the reporter's own — a default that looks functional and is not.

**`HealthReporter._devices` encodes unavailability as absence, and that collides with automatic signaling.** The dict serves three roles at once: the heartbeat roster (`publish_heartbeat` sends `devices=dict(self._devices)`), the "believed online" set (`reannounce()` republishes `online` for every key), and the shutdown roster. `publish_device_unavailable()` calls `remove_device()`, which pops the key; `set_device_status(name, "error")` re-adds it. Today these run in different code paths (command runner vs telemetry runner) and never collide. Publishing availability from `_handle_telemetry_error`, which already calls `set_device_status(..., "error")`, puts both in one path: the device is popped, immediately re-added with an `error` status, and the next MQTT reconnect's `reannounce()` republishes `online` for a device that is still failing. Retained availability would then lie, and self-heal on every reconnect, making the symptom intermittent and easy to misattribute to the broker.

The project is pre-1.0 with a small, known downstream set, and its convention is to ship breaking defaults at a minor boundary rather than defer them: ADR-045 places breaking changes at the next `0.x.0`, and ADR-060 and ADR-062 each shipped a breaking default that way with a one-line documented opt-out. Backwards compatibility is therefore not the deciding criterion here; which default is *correct* is.

## Decision

Publish per-device transport availability automatically from the telemetry and device archetypes: on retry exhaustion publish retained `offline`, and on the next successful poll publish retained `online`. The trigger defaults to any exception, because the framework cannot name the downstream transport exception types a typed default would need. `unavailable_on=(ExcType, ...)` narrows the trigger to specific types for apps that want a transport failure to mark a device offline while a handler bug does not, and `unavailable_on=None` disables it.

`None` keeps meaning "disabled" in all three archetypes, exactly as it does for `@app.command` today; only the *default* differs by archetype. Telemetry and device handlers default to automatic because they make a continuous freshness claim about a device. `@app.command` keeps its existing `None` default and its existing behaviour unchanged, because a command runs on demand and a failed command says nothing about whether the device is reachable.

Root entities (`is_root=True`) are excluded from the automatic default and require an explicit `unavailable_on` to participate. A root handler publishes to the flat `{prefix}/availability`, so one failed read would declare the entire app unavailable and can take down every entity the app owns in a consumer; a named device risks one entity.

Availability stays a two-word vocabulary (`online` / `offline`) and does not become the diagnostic channel. The distinction between a transport failure and a handler bug stays where it already lives — the ADR-012 status blob (`error` vs `circuit_open`) and the error topic (ADR-011/ADR-061) — so an operator who sees `offline` can still tell the two apart without availability carrying any error text.

The `circuit_open` skip path does not publish. Retry exhaustion already published `offline` before the circuit opened, so publishing again would only republish an unchanged retained value.

As a prerequisite, `HealthReporter` must stop encoding unavailability as absence from `_devices`: the device stays in the dict so the heartbeat roster and shutdown list remain complete, and an explicit marker records unavailability so `reannounce()` republishes `online` only for devices not marked unavailable — which is what its docstring already claims. This narrows ADR-047's statement that auto-recovery is scoped to `@app.command` only; the rest of ADR-047, including all command-side behaviour and both existing forms, stands unchanged and is not superseded.

```python
# Automatic by default -- no parameter needed. After the configured retries are
# exhausted, publishes retained "offline" to {prefix}/radon/availability;
# republishes "online" on the next successful poll.
@app.telemetry("radon", interval=300, retry=2)
async def read_radon(ctx: DeviceContext) -> dict[str, float]:
    return await ctx.adapter(SensorPort).read()   # BleakError -> offline


# Narrowed -- only a transport failure marks the device offline. A KeyError
# from a malformed payload is still reported on the error topic and in the
# status blob, but does not claim the device is unreachable.
@app.telemetry("radon", interval=300, unavailable_on=(BleakError, TimeoutError))
async def read_radon(ctx: DeviceContext) -> dict[str, float]:
    payload = await ctx.adapter(SensorPort).read()
    return {"radon": payload["radon_bq_m3"]}


# Disabled -- same spelling as @app.command's default today.
@app.telemetry("noisy", interval=60, unavailable_on=None)
async def read_noisy(ctx: DeviceContext) -> dict[str, float]: ...


# Root entities are NOT automatic: {prefix}/availability covers the whole app,
# so participating is opt-in.
@app.telemetry(interval=300, unavailable_on=(BleakError,))
async def read_root(ctx: DeviceContext) -> dict[str, float]: ...

```

## Decision Drivers

- A failing device that keeps a retained 'online' and a stale last reading is a false negative that looks authoritative — the same 'make bad states loud' principle ADR-062 applied to insecure transport configuration
- Opt-in does not reach app authors: ctx.mark_unavailable() shipped in 0.5.10 and the reporting app has not adopted it four minor releases later
- A framework-provided default exception tuple is not constructible, because cosalette has no dependency on bleak, paramiko, or pyserial and so cannot name their exception types
- Pre-1.0 with a small known downstream set, and the project's own convention (ADR-045, and the ADR-060/ADR-062 precedent) is to ship breaking defaults at a minor boundary with a documented one-line opt-out rather than defer them
- Availability must not become the diagnostic channel: ADR-012's status blob and the ADR-011/ADR-061 error topic already distinguish a transport failure from a handler bug, and availability carries no error text
- Flapping availability causes operator alarm fatigue, so the sustained-failure gate should reuse the runner's existing retry-exhaustion point rather than introduce new hysteresis machinery

## Considered Options

### Option 1: Opt-in only via ctx.mark_unavailable() (status quo)

Leave the runners publishing status-blob updates only, and require every telemetry and device handler to wrap its own body in try/except and call ctx.mark_unavailable() / ctx.mark_available() by hand.

- *Advantages:* No behaviour change for any existing app, and no migration note needed; Keeps ADR-047's auto-recovery scoping exactly as written, with no statement to revise; The app author decides precisely which failures count, with no framework guesswork
- *Disadvantages:* Demonstrably does not reach app authors: the primitive shipped in 0.5.10 and the reporting app has not adopted it four minor releases later; Leaves the reported defect open — a failing device keeps a retained 'online' and a stale reading indefinitely, which is a false negative that looks authoritative; Reinstates exactly the 15-25 lines of per-handler boilerplate ADR-047 set out to eliminate, just in the telemetry archetype instead of the command one; Keeps the declarative layer asymmetric: unavailable_on works on @app.command and silently does not exist on @app.telemetry

### Option 2: Automatic on any exception after retry exhaustion, unavailable_on narrows, root excluded (chosen) (chosen)

Publish retained 'offline' from the telemetry and device runners when a handler's retries are exhausted, and 'online' on the next successful poll. Default to any exception; accept unavailable_on=(ExcType, ...) to narrow and unavailable_on=None to disable. Exclude root entities from the automatic default.

- *Advantages:* Fires for the adapters that motivated ADR-047 — BLE, SSH, serial — without the framework needing to name their exception types; Zero-config for the common case, which is the only form that demonstrably reaches app authors given the 0.5.10 adoption evidence; Reuses the runner's existing retry-exhaustion point as the definition of a sustained failure, inventing no new threshold or hysteresis machinery; Keeps one parameter name and one meaning for None across all three archetypes; only the default differs, and it differs for a stated reason; Root exclusion bounds the blast radius to a single entity per failure instead of the whole app; Payload vocabulary stays online/offline, so nothing about internals is disclosed and ADR-061's concern does not apply
- *Disadvantages:* Breaking change: existing telemetry apps begin publishing retained 'offline' on sustained read failures, which will flip consumer entities to unavailable and needs a migration note; A handler bug (for example a KeyError on a malformed payload) publishes the same 'offline' as a genuine transport failure, so the two are distinguishable only via the status blob and the error topic; Requires the HealthReporter._devices prerequisite before it can be implemented correctly, widening the change beyond the runners; Root entities behave differently from named ones, which is an asymmetry app authors must learn

### Option 3: Automatic with a framework-provided default exception tuple

Publish availability automatically, but restrict the default trigger to a built-in tuple of transport-shaped exception types such as (OSError, TimeoutError), so that handler bugs never publish 'offline'.

- *Advantages:* Preserves ADR-047's 'transport availability' framing precisely — only transport-shaped failures claim a device is unreachable; A handler bug never misdirects incident response toward the network or the physical device; Still zero-config for apps whose adapters raise stdlib transport errors
- *Disadvantages:* Not constructible: cosalette does not depend on bleak, paramiko, or pyserial, so the default cannot name BleakError, SSHException, or serial.SerialException; Restricted to stdlib types, it would silently fail to fire for the BLE and SSH adapters ADR-047 was written for, including the reporter's own — a default that looks functional and is not; The silent-miss failure mode is worse than the status quo, because an author who sees the parameter documented reasonably assumes it is working; The framework cannot verify the hierarchies it would be betting on, since it cannot import the libraries in question

### Option 4: Automatic for every entity, root entities included

Apply the automatic default uniformly to named and root entities alike, with no is_root carve-out, so a failing root telemetry handler publishes 'offline' to the flat {prefix}/availability.

- *Advantages:* One rule with no archetype-dependent exception for an author to learn; A single-entity app registered at root gets automatic availability with no parameter at all; Simplest possible implementation: no is_root branch in the publish path
- *Disadvantages:* One failed read declares the whole app unavailable, which in Home Assistant can take down every entity the app owns; Blast radius is wildly asymmetric with the named-device case for what is the same class of failure; An operator cannot distinguish 'the app is down' from 'one root poll failed' on the availability topic alone; Most damaging on exactly the smallest deployments, where a root registration is most likely

## Decision Matrix

| Criterion | Opt-in only via ctx.mark_unavailable() (status quo) | Automatic on any exception after retry exhaustion, unavailable_on narrows, root excluded (chosen) | Automatic with a framework-provided default exception tuple | Automatic for every entity, root entities included |
| --- | --- | --- | --- | --- |
| Fires for the BLE/SSH/serial adapters that motivated ADR-047 | 1 | 5 | 2 | 5 |
| Reaches app authors without per-handler opt-in | 1 | 5 | 5 | 5 |
| Blast radius contained to one entity per failure | 5 | 5 | 5 | 1 |
| Preserves the transport-failure vs handler-bug distinction | 5 | 3 | 5 | 3 |
| Implementation and maintenance simplicity | 5 | 3 | 2 | 4 |
| Migration cost for existing apps | 5 | 2 | 2 | 1 |
| Closes the reported defect (stale reading under retained 'online') | 1 | 5 | 2 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- A sustained telemetry read failure now reaches the retained per-device availability topic with no app-side code, so a consumer stops presenting a stale reading as current
- The declarative layer becomes symmetric: unavailable_on is accepted by @app.telemetry, @app.device and @app.command, with one meaning for None in all three
- Removes the per-handler try/except boilerplate ADR-047 set out to eliminate, in the archetype where it was still required
- Sustained failure is defined by the runner's existing retry-exhaustion point, so no new threshold, hysteresis, or configuration surface is introduced
- Root exclusion keeps the blast radius of a single failed read at one entity rather than an entire app
- The HealthReporter prerequisite fixes a latent defect in its own right: reannounce() currently relies on absence-from-tracking and its docstring already describes the behaviour the explicit marker will provide

### Negative

- Breaking change: existing telemetry and device apps begin publishing retained 'offline' on sustained read failures, flipping consumer entities to unavailable where they previously stayed online with a stale value — needs a migration note written symptom-first, since that is what an operator will search for
- A handler bug publishes the same 'offline' as a transport failure, so diagnosis requires the status blob or the error topic; the availability topic alone cannot tell an operator which it was
- Root entities behave differently from named ones under the same failure, an asymmetry app authors must learn even though it is bounded and deliberate
- Narrowing via unavailable_on requires the author to name exception types from their own adapter libraries, which is precisely the knowledge the framework cannot supply on their behalf
- Revises ADR-047's statement that auto-recovery is scoped to @app.command only, so that ADR must be read together with this one
- The AI guidance in _ai_content/ and assets/guidance/ currently states these archetypes do not auto-recover; that text becomes actively wrong on merge and must be updated as part of the change, not as follow-up

_2026-09-12_
