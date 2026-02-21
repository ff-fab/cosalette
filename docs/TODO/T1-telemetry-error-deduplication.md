# T1: Telemetry Error Deduplication

**Status:** Open
**Phase trigger:** Before first PyPI release (gate task on `workspace-ch2`)
**Related:** ADR-011 (Error Handling), ADR-012 (Health & Availability), ADR-010 (Device Archetypes)

## Problem

The `@app.telemetry` polling loop publishes an error on *every* failed cycle
(see `_run_telemetry` in `_app.py`):

```python
while not ctx.shutdown_requested:
    try:
        result = await reg.func(**kwargs)
        await ctx.publish_state(result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Telemetry '%s' error: %s", reg.name, exc)
        await error_publisher.publish(exc, device=reg.name, is_root=reg.is_root)
    await ctx.sleep(reg.interval)
```

If a sensor is permanently broken, a device polled every 5 seconds floods MQTT with
12 identical error messages per minute — indefinitely. For transient glitches this
is fine ("self-healing"), but for persistent hardware failures it creates noise that
buries actionable signals.

## Decision Drivers

1. **Signal-to-noise ratio** — operators should see *one* alert when a sensor breaks,
   not thousands.
2. **Simplicity** — `@app.telemetry` is the "easy path"; complexity belongs in
   `@app.device` with manual loops.
3. **Observability** — errors must not be silently swallowed; operators need to know
   a device is still failing.
4. **ADR-011 compatibility** — errors are events (not retained), QoS 1, fire-and-forget.
5. **ADR-012 integration** — health/availability reporting already tracks per-device
   status; error deduplication should complement, not duplicate, that system.
6. **MQTT broker load** — repeated identical publishes waste bandwidth and storage in
   listeners/databases.

## Options

### Option A: Keep Current Behaviour (Naive)

**What it does:** Publish every failure, no deduplication.

**Implementation:** No change.

**Advantages:**

- Zero added complexity — nothing to get wrong.
- Every error is individually observable; no missed events.
- Simple mental model: "exception → MQTT message, always."
- Works well for transient errors (the common case during development).

**Disadvantages:**

- Error flood on persistent failure (12/min at 5s interval, 720/hour).
- Buries other errors in log/MQTT noise.
- MQTT broker, database, and alerting systems waste resources on duplicates.

---

### Option B: State-Transition Publishing (Recommended)

**What it does:** Track a per-device error state. Publish only on transitions:

- **healthy → error:** Publish the error (first failure).
- **error → error (same type):** Suppress — the device is still broken.
- **error → error (different type):** Publish — a *new* failure mode appeared.
- **error → healthy:** Log recovery (INFO level); optionally publish a recovery
  event or update device availability.

**Implementation sketch:**

```python
async def _run_telemetry(self, reg, ctx, error_publisher):
    last_error_type: type[Exception] | None = None

    while not ctx.shutdown_requested:
        try:
            result = await reg.func(**kwargs)
            await ctx.publish_state(result)
            if last_error_type is not None:
                logger.info("Telemetry '%s' recovered", reg.name)
                # Optionally: publish availability "online"
            last_error_type = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if type(exc) is not last_error_type:
                logger.error("Telemetry '%s' error: %s", reg.name, exc)
                await error_publisher.publish(
                    exc, device=reg.name, is_root=reg.is_root,
                )
            last_error_type = type(exc)
        await ctx.sleep(reg.interval)
```

**Advantages:**

- Eliminates error floods — one message per failure mode.
- Low complexity — ~6 lines of state tracking.
- Recovery is observable via log (and optionally via availability status).
- Different failure modes still surface immediately.
- Compatible with ADR-011 (errors remain events, not retained).
- Natural integration with ADR-012 health reporting (error→offline, recovery→online).

**Disadvantages:**

- If the first error message is lost (network blip), the failure is invisible
  until recovery or a different error — mitigated by QoS 1 and by health/availability
  reporting showing the device as degraded.
- Matching on `type(exc)` alone may group distinct errors of the same exception class
  (e.g., two different `OSError` messages). Could be refined to include `str(exc)` or
  a tuple key — adds minor complexity.
- Users accustomed to "every error = a message" may be surprised by suppression.

---

### Option C: Exponential Backoff on Error Publication

**What it does:** On repeated errors, increase the interval between error publications
(e.g., 1st immediately, 2nd after 30s, 3rd after 60s, capped at 5 min).

**Implementation:** Track consecutive error count, only publish when the backoff
timer expires.

**Advantages:**

- Reduces flood gradually rather than cutting off abruptly.
- Every error is *eventually* published — nothing completely suppressed.
- Does not require distinguishing error types.

**Disadvantages:**

- Still publishes repeatedly, just slower — MQTT noise is reduced, not eliminated.
- More complex state management (counter + timer + cap).
- Backoff affects *publication* only, not polling — but operators may confuse
  "fewer error messages" with "errors becoming less frequent."
- Doesn't integrate naturally with health reporting.

---

### Option D: First-Error + Periodic Summary

**What it does:** Publish the first error immediately. After N consecutive failures
(or a time window), publish a summary: `"Telemetry 'sensor' has failed 50 times
in the last 5 minutes (last error: OSError: I2C timeout)"`.

**Advantages:**

- Best signal-to-noise ratio for monitoring dashboards.
- Preserves urgency of first failure.
- Summary provides quantitative failure data.

**Disadvantages:**

- Significantly more complex — requires counters, timers, summary payload format.
- New payload schema needed for summary messages.
- Harder to test.
- Arguably over-engineered for `@app.telemetry` — this is `@app.device` territory.

---

### Option E: Circuit Breaker

**What it does:** After N consecutive failures, stop polling entirely. Resume after
a cooldown period or manual reset.

**Advantages:**

- Prevents pointless I/O to dead hardware.
- Clear failure state ("circuit open").

**Disadvantages:**

- Changes the fundamental `@app.telemetry` contract (always polls).
- Recovery detection is non-trivial (timer? manual reset? health check probe?).
- Risk of permanently disabling a sensor that could have recovered.
- Likely too opinionated for the framework layer.

## Recommendation

**Option B (State-Transition Publishing)** — it hits the sweet spot of simplicity
and effectiveness:

- Minimal added complexity (~6 lines of state in `_run_telemetry`).
- Eliminates the error flood completely for the persistent-failure case.
- Recovery is observable.
- Integrates naturally with ADR-012 health/availability.
- Keeps `@app.telemetry` as the "simple path" — users who need backoff/circuit-breaker
  logic already have `@app.device` with manual loops.

Consider refining the deduplication key to `(type(exc), str(exc))` to distinguish
different messages within the same exception class, at the cost of one extra comparison.

## Open Questions

1. Should recovery publish a structured event (new payload type) or just log?
   Health reporting (ADR-012) may already cover this via availability transitions.
2. Should the error-type matching key be `type(exc)` alone or `(type(exc), str(exc))`?
   The latter is more precise but may over-publish if messages contain variable data
   (e.g., timestamps, addresses).
3. Should this be opt-in (`deduplicate_errors=True`) or the default? Changing the
   default is a minor breaking change for consumers who rely on every-cycle error
   messages.
4. Does this warrant an ADR amendment to ADR-011, or is it an implementation detail
   within the existing decision?

## Next Steps

- Decide on Option B (or alternative) during pre-release review.
- If Option B: implement, test, update docs (telemetry-device.md, error-handling.md,
  device-archetypes.md).
- Consider whether this is a patch (behavioural fix) or minor (new feature).

*2026-02-21*
