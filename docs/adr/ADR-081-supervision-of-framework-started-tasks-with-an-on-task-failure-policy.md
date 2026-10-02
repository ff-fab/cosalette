---
status: Proposed
date: 2026-10-02
impact: high
tags: [lifecycle, health, error-handling, telemetry, devices]
---

# ADR-081: Supervision of framework-started tasks with an on_task_failure policy

## Status

Proposed **Date:** 2026-10-02

## Context

On current main the framework starts one asyncio task per `@app.device`, ungrouped `@app.telemetry` entity, coalescing group (`group:<name>`), `@app.periodic` handler and `@app.stream` handler (`_wiring/_tasks.py`, `_wiring/_task_lifecycle.py`), plus its own heartbeat, freshness (ADR-080) and health-check (ADR-028) loops. None of these tasks has a done-callback. The runners guard the handler call with `except Exception`, but anything that escapes those guards - an exception raised in a `finally` block, in `ErrorPublisher.publish`, in reactor dispatch outside the per-cycle guard, a `BaseException` that is not `CancelledError`, or a bug in the runner itself - ends the task silently. The exception is only observed at shutdown, when `cancel_tasks()` gathers the results and logs `Task error during shutdown`. Until then the entity keeps its last retained availability (usually `online`) and its heartbeat status (usually `ok`).

The downstream adopter `airthings2mqtt` (cosalette-apps proposal, item P-4; epic cos-4mv5) hit exactly this: one of three production outages of 11-38 h began with a polling task that had died while the process, the heartbeat and the MQTT connection stayed healthy. ADR-080's freshness watchdog now bounds how long a dead *telemetry* task can go unnoticed (`stale_after`, by default about two periods), but it does not cover devices, periodic handlers or streams, it detects the symptom up to `stale_after` seconds late, and it never brings the task back. ADR-029 already restarts tasks, but only as a side effect of an adapter health check failing; a task that dies while its adapter is healthy is never recreated.

The maintainer decided proposal open question 1 on 2026-10-02: the default reaction to an unexpected task death is to **restart with a budget** (exponential backoff), and to escalate to **exit** once the budget is used up, so a container orchestrator can take over. This ADR records that decision and the mechanism; the numbers and several interaction details are left open for review (see Open questions under Consequences).

## Decision

Use a framework-owned task supervisor that attaches a done-callback to every framework-started task and applies an `on_task_failure` policy - default `"restart"` with a bounded, exponentially backed-off restart budget that escalates to `"exit"` when exhausted - because an entity task that dies outside every guard must be reported within seconds, recovered without operator action when the fault is transient, and handed to the orchestrator when it is not.

**1. What counts as a task failure.** A supervised task fails when it finishes while the app is not shutting down and the framework did not cancel it itself: it raised an exception (including a non-`CancelledError` `BaseException`), or it was cancelled by something other than the framework. Cancellation from phase-4 teardown (`shutdown_event` set) and from an ADR-029 adapter restart (`cancel_tasks_for_adapter`) is expected and never reaches the policy. The supervisor tracks expected cancellations explicitly instead of inferring them from `task.cancelled()` alone.

**2. Immediate reaction (every policy).** The done-callback is synchronous, so it only records the failure and schedules an async handler on the loop. The handler: logs one CRITICAL line with the task name, the mapped entities and the full traceback; sets every entity mapped to the task in `DeviceTaskMap` (all members, for a coalescing group) to heartbeat status `error`; and publishes retained `offline` for each through a new availability source `"supervisor"` via `publish_device_unavailable(..., source="supervisor")`. Under ADR-077's multi-source rule this source is cleared independently, so it never declares an entity online while `telemetry`, `freshness` or the adapter health check still holds it offline. Periodic and stream tasks have no availability topic; for them the reaction is the log line and the policy.

**3. `on_task_failure="restart"` (default).** The supervisor recreates the failed task through the same factories that start it today (`start_device_tasks_for_names` for devices, telemetry and whole coalescing groups, keeping trigger slots per ADR-064/065/067; the periodic and stream starters for those archetypes), after a backoff delay that starts at 1 s and doubles per consecutive restart of the same task, capped at 60 s, without jitter (as in ADR-080, so tests can pin it). Each task identity has a restart budget of N restarts within a rolling window W; a restart that is followed by W seconds without another failure resets the counter, mirroring ADR-029's `sustained_health_reset`. The `supervisor` availability source is cleared at the replacement's first healthy boundary (a fresh telemetry cycle, a device yield), not at restart time, so a task that crash-loops never flaps `online`.

**4. Budget exhausted -> `"exit"`.** When a task fails with no budget left, the supervisor logs CRITICAL (`task <name> exceeded its restart budget; exiting`), sets `shutdown_event` so the normal phase-4 teardown runs (tasks cancelled, lifespan exited, `{prefix}/status` set `offline`), and `App.run()` / `App.cli()` then end the process with a non-zero exit status. Exiting is preferred over leaving a permanently dead entity because the process is otherwise healthy and no liveness check would ever restart it.

**5. Other policies.** `"exit"` skips the restart step and escalates on the first failure. `"ignore"` performs only the immediate reaction of point 2 and leaves the entity offline in status `error` until the process restarts; ADR-080 freshness and a P-5 liveness probe remain the backstops.

**6. Configuration surface.** `App(on_task_failure=..., task_max_restarts=..., task_restart_window=...)` sets the app-wide policy. Values are validated in `App.__init__` like the ADR-029 parameters. Whether registrations may override the policy per task is left open.

**7. Scope.** Entity tasks (device, telemetry, group, periodic, stream) are supervised under the policy. How the framework's own loops (heartbeat, freshness watchdog, health-check runner) are treated is left open; the proposed default is to supervise them as well but to escalate straight to exit, because a dead heartbeat is a framework bug, not a handler fault.

This ADR changes nothing in ADR-029: adapter-driven restarts keep their own threshold, budget and cooldown, and a task cancelled for an adapter restart is never counted against the task restart budget.

```python
import cosalette

app = cosalette.App(
    name="airthings2mqtt",
    version="1.4.0",
    on_task_failure="restart",   # default; "ignore" | "restart" | "exit"
    task_max_restarts=3,          # proposed default, open question
    task_restart_window=600.0,    # seconds, proposed default, open question
)

# A telemetry task that dies outside every guard now:
#   CRITICAL Task 'telemetry:radon' died: <traceback>
#   airthings2mqtt/radon/availability -> "offline" (source="supervisor")
#   heartbeat: {"radon": {"status": "error", ...}}
#   restarted after 1 s, 2 s, 4 s ... capped at 60 s
#   4th failure within the window -> graceful shutdown, non-zero exit
```

## Decision Drivers

- A dead entity task must be reported within seconds, not at shutdown and not only after a telemetry stale_after window (ADR-080)
- Transient faults (a one-off adapter exception that escaped a guard, a race in a reactor) should heal without operator action in unattended deployments
- A persistent fault must not leave a healthy-looking process with a permanently dead entity; the container orchestrator is the right owner of a full restart
- Reuse existing mechanisms: DeviceTaskMap, start_device_tasks_for_names, ADR-077 multi-source availability, ADR-029 budget and sustained-reset semantics
- Expected cancellations (shutdown, ADR-029 adapter restart) must never trigger the policy
- The maintainer decided on 2026-10-02 that the default is restart with a budget, escalating to exit (proposal cos-4mv5 open question 1)

## Considered Options

### Option 1: Supervisor with restart-with-budget default escalating to exit (chosen)

Attach a done-callback to every framework-started task. On an unexpected end, log CRITICAL, mark mapped entities error and offline (source "supervisor"), and apply on_task_failure: restart with exponential backoff within a per-task budget, and exit the process when the budget is exhausted. ignore and exit are available as alternatives.

- *Advantages:* Detects a dead task immediately and for every archetype, not only telemetry; Recovers transient faults automatically, as ADR-029 does for adapters; Bounded: a crash loop ends in a clean, observable process exit the orchestrator can act on; Reuses the task factories and availability sources that already exist
- *Disadvantages:* Most moving parts of the options: backoff timers, per-task budgets, expected-cancel tracking; Restarting a @app.device task loses its in-memory loop state (as with ADR-029); Changes behaviour for existing apps: a task that silently died before now restarts or ends the process

### Option 2: Supervisor that only reports (ignore by default)

Attach the done-callback, log CRITICAL and mark mapped entities error and offline, but never restart or exit unless the app opts in. Recovery relies on an operator, on ADR-080 freshness, or on a P-5 liveness probe.

- *Advantages:* Smallest change in behaviour; no risk of restart storms; Simplest implementation and test surface
- *Disadvantages:* A transient fault still needs a manual container restart, which is exactly the outage pattern the proposal reports; Depends on every adopter wiring a liveness probe to get recovery

### Option 3: Fail fast: exit on the first task failure

Treat any unexpected task end as fatal: log CRITICAL, run graceful teardown and exit non-zero, leaving all recovery to the container orchestrator (Docker restart policy, Kubernetes).

- *Advantages:* Simple and predictable; one code path; Fresh process state on every recovery; no partially restarted app
- *Disadvantages:* One flaky entity takes every other healthy entity of the app offline for the restart duration; Deployments without a restart policy stay down; Repeated full restarts re-run lifespan, adapter initialisation and discovery on each fault

### Option 4: Structured concurrency with asyncio.TaskGroup

Run all entity tasks inside one TaskGroup so an exception in any task cancels the group and propagates out of the run loop.

- *Advantages:* Idiomatic modern asyncio; no hand-written callbacks; Exceptions can never be silently lost
- *Disadvantages:* Semantically equivalent to fail-fast for the whole app, with no per-task restart; Conflicts with ADR-029, which cancels and recreates individual tasks at runtime; Large refactor of the wiring layer for no gain over an explicit supervisor

## Decision Matrix

| Criterion | Supervisor with restart-with-budget default escalating to exit | Supervisor that only reports (ignore by default) | Fail fast: exit on the first task failure | Structured concurrency with asyncio.TaskGroup |
| --- | --- | --- | --- | --- |
| Time to detect a dead entity task | 5 | 5 | 5 | 5 |
| Automatic recovery from transient faults | 5 | 1 | 3 | 3 |
| Isolation: healthy entities stay online | 4 | 5 | 1 | 1 |
| Bounded behaviour under persistent faults | 5 | 3 | 4 | 4 |
| Fit with ADR-029 and ADR-077 mechanisms | 5 | 5 | 3 | 1 |
| Implementation and test complexity | 2 | 5 | 4 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- An entity task that dies outside every guard is visible within one event-loop turn: CRITICAL log, heartbeat status error and retained offline, instead of a silent outage that surfaces only at shutdown
- Transient faults heal without operator action; persistent faults end in a graceful shutdown with a non-zero exit status that Docker or Kubernetes restart policies act on
- Covers devices, periodic handlers and streams, which ADR-080 freshness does not
- Reuses DeviceTaskMap, the existing task factories, ADR-077 multi-source availability and ADR-029 budget semantics, so no new availability model is introduced
- The 'Task error during shutdown' path becomes a last-resort safety net rather than the only place a task failure is seen

### Negative

- Behaviour change for existing apps: a task that used to die silently now restarts and may end the process; release notes and the version-migration guide must call this out, and it may warrant a minor-version bump
- A restarted @app.device task loses its in-memory loop state, as with ADR-029; adopters must persist state that has to survive via DeviceStore
- A process exit takes every entity of the app offline until the orchestrator restarts it; deployments without a restart policy stay down
- New public API surface (on_task_failure, task_max_restarts, task_restart_window) and a new availability source name (supervisor) to document and keep stable
- OPEN QUESTION 1 - budget size and window: proposed task_max_restarts=3 within task_restart_window=600 s, with a counter reset after 600 s without failure (ADR-029 uses 3 restarts and a 300 s sustained reset). Confirm or change the defaults.
- OPEN QUESTION 2 - backoff: proposed 1 s initial delay, doubling, capped at 60 s, no jitter. Should the cap or the base scale with the entity's poll interval for slow pollers (25 min)?
- OPEN QUESTION 3 - after the budget is exhausted: proposed graceful shutdown and a non-zero exit status. Which exit code (1, or 70/EX_SOFTWARE), and should App.run() raise a dedicated exception (e.g. TaskSupervisionError) that embedders can catch instead of exiting?
- OPEN QUESTION 4 - per-task override: should @app.telemetry, @app.device, @app.periodic and @app.stream (and Router equivalents) accept on_task_failure= to override the app-wide policy, or is the app-wide setting enough for v1?
- OPEN QUESTION 5 - availability and health publishing: is a new 'supervisor' availability source right, and should it clear at the replacement's first healthy boundary (proposed) or as soon as the task is recreated? Should the heartbeat expose a restart count or last-failure field per entity, and how does this interact with P-3's planned last_error?
- OPEN QUESTION 6 - device crash path: TelemetryRunner.run_device currently catches a crashing @app.device handler, logs 'Device ... crashed' and returns normally, so the task ends without an exception. Should a device task that returns while the app is running count as a failure and go through the policy (the most common real-world device death), or stay out of scope?
- OPEN QUESTION 7 - framework loops: should the heartbeat, freshness watchdog and health-check loops be supervised, and if so with the app policy or with an unconditional escalate-to-exit (proposed)?
- OPEN QUESTION 8 - interaction with ADR-029: proposed rule is that a task whose adapter is mid-restart is left to the adapter restart and not counted against the task budget. Confirm, and decide whether a task failure should feed the adapter's consecutive-failure count.

_2026-10-02_
