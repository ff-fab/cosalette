---
status: Accepted
date: 2026-08-25
impact: moderate
tags: [security, commands, periodic, resource-exhaustion]
---

# ADR-060: Bounded Handler Execution Defaults

## Status

Accepted **Date:** 2026-08-25

## Context

Security-audit finding F-DP5 (CWE-400, threat-model scenario S7, risk 12): command handlers default to ``timeout=None`` (unbounded), so one hung handler stalls its entity's FIFO dispatch worker indefinitely — no log line, no error publish, no availability change. The device-context command path (``ctx.on_command`` / sub-entity handlers) has no timeout mechanism at all, and the ``@app.periodic`` loop likewise invokes its handler unbounded, silently freezing the background task. This is the same permanent-wedge class as the production incident that motivated the ADR-024 amendment, which already added a per-invocation timeout backstop for ``@app.telemetry`` (auto-default ``interval × 1.0``, ``None`` disables).

The asymmetry is now a liability: telemetry invocations are bounded by default while command and periodic invocations are not, even though commands occupy the entity worker that also serializes state updates, and a wedged periodic loop produces a silent zombie task. A hung MQTT-facing handler additionally keeps the broker connection's command path busy, which an attacker can compound by repeatedly issuing commands once they discover a slow handler (amplified by the fact that command payloads are attacker-chosen within the inbound size cap).

Commands have no natural period to derive a bound from (unlike telemetry's interval), so the design must pick an explicit constant, and because a bounded default changes existing behavior (legitimately slow handlers will now be cancelled), the change needs a documented opt-out that matches the mental model users already learned from the ADR-024 amendment.

## Decision

Extend the ADR-024 three-state timeout backstop to all remaining handler surfaces. Commands get an explicit bounded default of 30 seconds; periodic tasks inherit the telemetry rule (interval × 1.0); device-context command handlers get the same bounded default with a per-handler override. All three surfaces share one semantics table: omitted → bounded default, explicit float → used as-is, ``None`` → explicitly unbounded (opt-out), callable spec → resolved against Settings at bootstrap.

```python
# Command: bounded by default (30 s), override or disable explicitly
@app.command("reboot", timeout=120.0)   # slow bus operation
async def reboot(cmd: Command) -> dict[str, object]: ...

@app.command("fast-toggle")             # omitted -> 30 s backstop
async def fast_toggle(cmd: Command) -> dict[str, object]: ...

@app.command("legacy-longrun", timeout=None)   # explicit opt-out
async def legacy_longrun(cmd: Command) -> dict[str, object]: ...

# Periodic: auto-default interval x 1.0 (telemetry rule, ADR-024)
@app.periodic("cache-refresh", interval=60)
async def refresh(cache: CachePort) -> None: ...          # bounded at 60 s

# Device/sub-entity context handlers: same 30 s default
@ctx.on_command("calibrate", timeout=90.0)
async def calibrate(sub_topic: str | None, payload: str) -> None: ...
```

## Decision Drivers

- F-DP5/S7 (risk 12): one hung handler must not stall an entity FIFO worker or freeze a periodic task without any observable signal
- Consistency with the ADR-024 amendment: one semantics table (unset/explicit/None/callable) across telemetry, command, periodic, and device-context surfaces
- TimeoutError is already mapped to error_type 'timeout' in the error taxonomy and flows through publish_error_safely, so cancellation needs no new error machinery
- Behavior change must be reversible per registration (timeout=None) because the framework cannot know which handlers are legitimately slow
- No new global Settings namespace: the post-F-TP1 posture is to avoid growing reserved environment names

## Considered Options

### Option 1: Status quo (explicit opt-in only)

Keep timeout=None defaults everywhere and only document that app authors should pass timeout= themselves.

- *Advantages:* Zero migration risk for existing apps; No new constants or resolution code
- *Disadvantages:* Leaves the risk-12 finding untreated; silent freezes remain the default experience; Every app must remember to configure each of the four surfaces independently; Audit finding stays open indefinitely

### Option 2: Bounded defaults, three-state spec everywhere (chosen) (chosen)

Commands default to a 30-second backstop; periodic tasks auto-default to interval x 1.0 exactly like telemetry; device-context handlers get the 30-second default with a per-handler override. Omitted/explicit/None/callable semantics identical across all surfaces, resolved at bootstrap like intervals (ADR-020 pattern).

- *Advantages:* Closes F-DP5 across every execution surface, not just @app.command; One semantics table to learn; reuses ADR-020 bootstrap resolution and the ADR-011/012 error-publishing and health machinery unchanged; Per-registration opt-out preserves legitimate long-running handlers; TimeoutError cancellation publishes structured error_type 'timeout' events — observable, not silent
- *Disadvantages:* Behavior change: previously-unbounded handlers now get cancelled at 30 s (or one interval) unless migrated; Adds resolution code paths and tests for three additional surfaces; The 30 s constant is arbitrary and may need tuning per ecosystem

### Option 3: Global settings namespace knob

Add an execution (or similar) section to Settings, e.g. EXECUTION__COMMAND_TIMEOUT, read as the global default for all handler timeouts, with decorator arguments overriding it.

- *Advantages:* Single deployment-level control point for operators; No repeated per-registration boilerplate for homogeneous apps
- *Disadvantages:* Grows the reserved environment namespace right after F-TP1 documented how collision-prone those are; Two-layer precedence (env then decorator) complicates reasoning about effective values and introspection; Precedent: retry/backoff and the telemetry timeout were both scoped per-registration; a global knob would be a second, competing configuration philosophy

## Decision Matrix

| Criterion | Status quo (explicit opt-in only) | Bounded defaults, three-state spec everywhere (chosen) | Global settings namespace knob |
| --- | --- | --- | --- |
| Closes F-DP5 across surfaces | 1 | 5 | 3 |
| Consistency with ADR-024/ADR-020 patterns | 3 | 5 | 2 |
| Migration risk contained | 5 | 4 | 3 |
| Environment/config surface discipline | 5 | 5 | 2 |
| Observability of hangs | 1 | 5 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- A hung command handler can no longer stall its entity's FIFO worker beyond 30 seconds; the worker logs, publishes a structured timeout error, and continues with queued commands
- Periodic loops self-heal: a wedged cycle is cancelled at one interval and the loop continues with the next tick instead of becoming a silent zombie task
- Device-context and sub-entity handlers get the same protection as app-level commands, closing the last unguarded execution path from F-DP5
- Cancellation is observable end-to-end: TimeoutError maps to error_type 'timeout' (existing taxonomy), flows through ADR-011 redaction/deduplication and ADR-012 health reporting
- Users migrate with a single keyword per registration (timeout=None) — the same escape hatch the ADR-024 amendment established

### Negative

- Breaking-ish behavior change (0.x minor): apps with handlers legitimately exceeding 30 s (or one periodic interval) will see cancellations until they set timeout= explicitly
- asyncio.timeout cancels inside the coroutine and awaits cleanup, so actual elapsed time may slightly exceed the nominal bound (standard asyncio semantics)
- Three more surfaces carry resolution logic and tests, increasing maintenance area alongside telemetry's existing timeout machinery
- Reactor dispatch after a successful command remains outside the timeout window by design; a hung reactor is still only guarded by its own registration rules

_2026-08-25_
