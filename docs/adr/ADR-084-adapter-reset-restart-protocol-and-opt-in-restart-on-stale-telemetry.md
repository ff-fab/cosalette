---
status: Accepted
date: 2026-10-02
impact: moderate
tags: [lifecycle, health, telemetry]
---

# ADR-084: Adapter reset() restart protocol and opt-in restart on stale telemetry

## Status

Accepted **Date:** 2026-10-02 | Amended **Date:** 2026-10-08

## Context

ADR-029 restarts an unhealthy adapter by exiting and re-entering its async context manager, and only adapters that are both `HealthCheckable` and context managers are eligible. Two gaps showed up in epic cos-4mv5 (proposal item P-6):

- Many adapters hold only software state (a client object, a session, a parser) and are not context managers. They cannot be restarted at all today, although recreating that state is often all a recovery needs.
- A health check can pass while the adapter delivers no data. In the airthings2mqtt outages the BLE adapter answered its health check while every telemetry entity went stale (ADR-080) for hours, so the ADR-029 threshold never triggered.

While reviewing the restart path, a drift from ADR-029 Decision 5 also showed up: the health check runner calls the restart callback for every health-checkable adapter, including adapters with `restartable = False` and adapters that are not context managers. The restart then calls `__aexit__`/`__aenter__` on an opted-out adapter, or fails on an adapter without them and leaves its device tasks cancelled.

## Decision

Use an async `reset()` method as a second restart protocol, and an opt-in `App(restart_on_stale=False)` that sends a stale entity through the existing ADR-029 restart path, because both reuse ADR-029's budget, cooldown and task re-creation instead of adding a second recovery mechanism.

- **Eligibility.** An adapter is restartable when it is `HealthCheckable`, has not set `restartable = False`, and either is an async context manager or has a callable `reset()`. A context manager keeps precedence: when an adapter has both, the restart exits and re-enters it and never calls `reset()`. Adapters with neither keep ADR-029's startup WARNING.
- **Reset restart.** For a reset-only adapter the restart cancels the dependent device tasks, waits `restart_cooldown`, awaits `reset()`, runs the post-restart health check and re-creates the tasks, exactly like ADR-029 Decision 2 without the exit and enter steps. A `reset()` that raises is a failed restart and counts against `max_restarts`. The framework never enters or exits a reset-only adapter. `reset()` must only recreate software state; resetting hardware is out of scope.
- **Only eligible adapters restart.** The health check runner calls the restart path only for restartable adapters. When a non-restartable adapter reaches the threshold it logs one WARNING per unhealthy episode and stays offline, as ADR-029 Decision 5 already states.
- **Restart on stale.** With `restart_on_stale=True`, the transition of a telemetry entity to `stale` (ADR-080) requests a restart of every restartable adapter that entity depends on. The request skips the `restart_after_failures` threshold but counts against `max_restarts`, honours `restart_exhausted`, uses the cooldown, and is serialised with the health check loop so the two paths never restart the same adapter at once. It fires once per stale episode: the entity must produce fresh data and go stale again before it requests another restart. Adapters are deduplicated within one freshness check.
- **Scope.** Restart on stale needs the health check runner, so it applies only when `health_check_interval` is set and the adapter is restartable under the rules above. The default stays `False`.

```python
class AirthingsClient:
    async def health_check(self) -> bool: ...

    async def reset(self) -> None:
        """Drop the BLE client and parser; the next poll reconnects."""
        self._client = None


app = App("airthings2mqtt", restart_on_stale=True)
```

## Decision Drivers

- Software-only adapters need a recovery path without becoming context managers
- An adapter whose health check passes while it delivers no data must be recoverable
- Every restart, whatever triggers it, must share ADR-029's budget, cooldown and task re-creation
- Opted-out and non-lifecycle adapters must never be restarted (ADR-029 Decision 5)
- No behaviour change for existing apps unless they opt in

## Considered Options

### Option 1: reset() protocol plus opt-in restart on stale (chosen)

Accept reset() as a second restart protocol and let a stale transition request an ADR-029 restart behind restart_on_stale.

- *Advantages:* One restart path with one budget; Works for software-only adapters; Off by default
- *Disadvantages:* Two restart protocols to document; Restart on stale needs a health-checkable adapter

### Option 2: Require context managers

Keep ADR-029 as is; software-only adapters must implement __aenter__/__aexit__.

- *Advantages:* No new protocol
- *Disadvantages:* Forces lifecycle ceremony on adapters that only hold software state; Does not address stale telemetry with a passing health check

### Option 3: Separate stale recovery mechanism

A dedicated on_stale hook that apps implement themselves.

- *Advantages:* Maximum flexibility for the app
- *Disadvantages:* Each app re-implements budget, cooldown and task handling; Two recovery paths can race on the same adapter

## Decision Matrix

| Criterion | reset() protocol plus opt-in restart on stale | Require context managers | Separate stale recovery mechanism |
| --- | --- | --- | --- |
| Reuse of the ADR-029 restart path | 5 | 5 | 1 |
| Coverage of the stale-with-passing-health-check case | 4 | 1 | 4 |
| Ceremony for adapter authors | 4 | 2 | 2 |
| Safety of existing apps | 5 | 5 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Software-only adapters become restartable by adding one method
- A stale entity can recover without waiting for an operator, within the ADR-029 budget
- Opted-out and non-lifecycle adapters are no longer exited, re-entered or left without device tasks by a restart attempt

### Negative

- Adapters with neither protocol that were silently "restarted" before (always failing) now log one WARNING and stay offline instead
- Restart on stale cannot help an adapter without a health check, because the restart path lives in the health check runner
- A slow entity whose stale_after is too tight can spend the restart budget on false alarms

## Amendment (2026-10-08) — Additive

**Rationale:** ADR-084 limited restart on stale to telemetry because only telemetry was in the stale-restart map. ADR-081 (2026-10-03 amendment) gave streams an opt-in stale_after= but kept them out of restart_on_stale, giving as the only reason that streams were not in the ADR-029 adapter map. cos-kg37 has since added streams to that map, so a stream bound to a StreamablePort[T] is health-checked and restarted like any other dependent, but a stale stream still could not request that restart and its name was dropped silently by the restart callback (cos-02wc). A radio or BLE stream whose port passes its health check while delivering nothing is the same failure ADR-084 was written for.

### Additional Sub-Decision: Restart on stale covers streams that declare stale_after=

With `restart_on_stale=True`, a stream that declares `stale_after=` and goes stale requests a restart of every restartable adapter it depends on (the `StreamablePort[T]` behind its `Stream[T]` parameter, or any other injected adapter), with the telemetry semantics unchanged: the request skips `restart_after_failures`, counts against `max_restarts`, honours `restart_exhausted` and `restart_cooldown`, is serialised with the health check loop, fires once per stale episode, and needs `health_check_interval` and a health-checkable adapter (otherwise the existing startup WARNING is logged). Root streams with `stale_after=` are included: their freshness is heartbeat-only, but their adapter can still be restarted. A stream without `stale_after=` never goes stale and so never requests a restart. The stale-restart map is built from the telemetry entities and streams that have a freshness bound, not from the full adapter map, so a command, device or periodic task that shares a name with a stale entity never pulls its own adapter into the restart. The restart reason names the archetype (`stale stream 'radio'` or `stale telemetry 'sensor'`). A stale entity that depends on no adapter is logged at DEBUG instead of being ignored silently. No other archetype gains a stale notion: commands, buttons, devices, periodic tasks, discovery, state and attributes are event-driven or have no success signal, so they cannot go stale.

### Additional Positive Consequences

- A stream whose port passes its health check while delivering nothing can recover in place, within the ADR-029 budget, the same as telemetry

### Additional Negative Consequences

- An app that already sets restart_on_stale=True and has a stream with stale_after= now restarts that stream's adapter when the stream goes stale; a quiet but healthy stream with a tight stale_after can spend the restart budget on false alarms
