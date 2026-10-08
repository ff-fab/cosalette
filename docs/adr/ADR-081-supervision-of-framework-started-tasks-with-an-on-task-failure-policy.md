---
status: Accepted
date: 2026-10-02
impact: high
tags: [lifecycle, health, error-handling, telemetry, devices]
---

# ADR-081: Supervision of framework-started tasks with an on_task_failure policy

## Status

Accepted **Date:** 2026-10-02 | Amended **Date:** 2026-10-02 | Amended **Date:** 2026-10-03 | Amended **Date:** 2026-10-08

## Context

On current main the framework starts one asyncio task per `@app.device`, ungrouped `@app.telemetry` entity, coalescing group (`group:<name>`), `@app.periodic` handler and `@app.stream` handler (`_wiring/_tasks.py`, `_wiring/_task_lifecycle.py`), plus its own internal loops (heartbeat, freshness watchdog, adapter health checker, MQTT connection and refresh loops). None of these tasks has a done-callback, and two runners end their task silently on failure:

- `TelemetryRunner.run_device` (`_runners/_telemetry_runner.py:158-163`) catches any handler exception, logs `Device '<name>' crashed` at ERROR, publishes an error payload and **returns normally**. A device whose generator body raised is marked offline through the `device` source (ADR-077); a setup failure (DI, `init=`, a non-generator handler) is not. Nothing restarts it. `docs/guides/command-device.md` documents this as "The device task ends, but other devices continue."
- `run_stream` (`_runners/_stream_runner.py:233-236`) logs the exception and returns; no error payload, no status, no availability change.
- Anything that escapes the remaining guards - an exception in a `finally` block, in `ErrorPublisher.publish`, a non-`CancelledError` `BaseException`, a runner bug, a failing periodic `init=` - ends the task and is only observed at shutdown, when `cancel_tasks()` logs `Task error during shutdown`.

Until shutdown the entity keeps its last retained availability (usually `online`) and heartbeat status (usually `ok`). The downstream adopter `airthings2mqtt` (cosalette-apps proposal, item P-4; epic cos-4mv5) had one of three 11-38 h outages start with exactly this: a dead polling task inside a healthy process. ADR-080's freshness watchdog bounds the detection delay for telemetry only and never recovers the task. ADR-029 recreates tasks only as a side effect of an adapter health check failing.

The maintainer decided the proposal's open question 1 and all follow-up questions of this ADR's first draft on 2026-10-02; the decisions are recorded below. Defaults deliberately mirror ADR-029 (`max_restarts=3`, `sustained_health_reset=300`, no jitter) so adapter-level and task-level recovery behave alike.

## Decision

Use a framework-owned task supervisor that attaches a done-callback to every framework-started task and applies an app-wide `on_task_failure` policy - default `"restart"` with a 3-restart budget that resets after 300 s without failure, escalating to a graceful shutdown that raises `TaskSupervisionError` - because a task that dies must be reported within seconds, recovered without operator action when the fault is transient, and handed to the orchestrator when it is not.

### 1. What counts as a failure

A supervised task fails when it raises (including a non-`CancelledError` `BaseException`) or is cancelled by something other than the framework, while the app is not shutting down. A normal return is **never** a failure: it is logged at INFO (`device 'x' handler completed`) and the entities keep their state. Cancellation by phase-4 teardown and by an ADR-029 adapter restart is recorded as expected before `cancel()` is called and never reaches the policy.

**Runners re-raise.** `run_device` keeps marking generator-body errors `offline` through the `device` source, but no longer publishes its own error payload for an exception that ends the task; it re-raises instead of returning, and the supervisor publishes the single payload for the crash (section 2). This includes setup failures (DI resolution, `init=`, the non-generator `TypeError`); deterministic ones simply use up the budget. `run_stream` re-raises the same way, so streams gain error/offline reporting through the supervisor. The runners drop their own ERROR `crashed`/`error` lines.

### 2. Immediate reaction (every policy)

The done-callback is synchronous and only records the failure and schedules an async handler; it holds no awaitable state of its own and so cannot die independently. The handler:

- logs exactly **one CRITICAL** line with the task name, mapped entities and full traceback;
- publishes **exactly one error payload per crash** through the normal error path, `ErrorPublisher`, to `{prefix}/error` and, for a task mapped to a single non-root entity, `{prefix}/{device}/error`. A coalescing-group task maps to several entities; its one payload goes to `{prefix}/error` only and lists the members in `details.entities`. A periodic task has no entity and publishes to `{prefix}/error` only. The payload keeps the original exception's `error_type` and `message` (ADR-011 mapping and ADR-061 disclosure rules unchanged), so existing subscribers keep working, and carries the marker `details.task_failure = true` plus `details.task` (the task name). Runners do not publish their own payload for an exception that ends the task; per-cycle errors that the runners handle and survive (telemetry poll errors, periodic and stream per-item errors that are caught) keep their current payloads;
- sets every entity mapped to the task in `DeviceTaskMap` (all members, for a coalescing group) to heartbeat status `error`, and publishes retained `offline` through a new availability source `"supervisor"` (ADR-077 multi-source rule: it never declares an entity online while another source holds it offline). Periodic tasks have no entities; the log and the error payload are their whole reaction.

**Log deduplication.** `ErrorPublisher.publish` logs every published payload at WARNING with `exc_info`. That WARNING stays, because it ties the correlation id to the broker payload (ADR-011, ADR-061), but for the supervisor's terminal-failure payload it is emitted **without** the traceback. The CRITICAL line is the only line per crash that carries a traceback.

### 3. `on_task_failure="restart"` (default)

**Budget.** Each registration may be restarted **3** times. The count resets to 0 only after a full **300 s** without a failure of that registration; it is not a rolling-window rate. This is ADR-029's `max_restarts=3` / `sustained_health_reset=300` applied per task. For a coalescing group the budget, backoff and counters are **per group**, not per member: the group runs as one task (`group:<name>`), so a failure of the group task consumes one restart of the group's single budget whichever member's handler raised.

| Failure pattern | Outcome |
| --- | --- |
| Fails on every run (deterministic bug, bad config) | Restarts after 1 s, 2 s, 4 s; the 4th failure exits - about 10 s after start |
| Fails repeatedly, gaps shorter than 300 s | Budget never resets; exits on the 4th failure |
| Fails at most once per 300 s | Each failure restarts after 1 s; tolerated indefinitely |

**Backoff.** 1 s before the first restart, doubling per consecutive restart, capped at 60 s, no jitter (ADR-029 has none either; tests can pin it). The backoff is not scaled by the poll interval; instead a supervisor restart **skips the immediate first cycle**:

- interval telemetry waits one interval before its first poll;
- cron telemetry waits for its next fire time;
- coalescing-group members start with due time = their interval instead of 0;
- periodic handlers already sleep first (`_runners/_periodic.py:85-89`).

Guarantee: a supervisor restart never polls a device more often than its configured interval. This is a runner flag set only by the supervisor; the initial start and ADR-029 restarts keep today's immediate first cycle. Trigger arms pending on a slot (ADR-064/066) keep their own semantics and `min_interval` throttle.

**Re-creation.** The task is rebuilt through the factories that start it today: `start_device_tasks_for_names` for devices, telemetry and whole coalescing groups, passing the wiring-owned `trigger_slots` mapping so the persistent `_TriggerSlot` objects that `EntityNotifier`/`DeviceTrigger` are bound to (pending arm, ADR-066 throttle window, group wake event) are reused; the periodic and stream starters for those archetypes. Task-local state is lost: the dedup baseline `last_published`, `last_error_type` and `retry_count`. The first value after a restart is therefore always published; this is accepted. State held on the registration (`circuit_breaker`, `publish_strategy`) and in `HealthReporter` (freshness) survives.

**Recovery.** The `supervisor` source and the `error` status clear at the re-created task's first successful cycle: a telemetry entity or group member at its first successful cycle (even when the publish strategy suppresses the publish), a stream at its first produced item, a device at its first `yield` (where `_clear_device_unavailable` already clears the `device` source). This clear is a recovery event that P-3 (cos-4mv5.6) can log; P-3 owns the details.

**Counters.** The supervisor keeps readable per-registration counters: restarts in the current budget window, total restarts, last failure time and last failure type. Their heartbeat representation is left to P-5 (cos-4mv5.7).

### 4. Budget exhausted, `"exit"` and `"ignore"`

When a registration fails with no budget left, the supervisor logs CRITICAL (`task <name> exceeded its restart budget`), sets `shutdown_event` so the normal phase-4 teardown runs (tasks cancelled, lifespan exited, `{prefix}/status` `offline`), and then `App.run()` raises **`TaskSupervisionError(RuntimeError)`**. It carries the task name, the restart count, an `internal` flag, and the original exception as `__cause__`. `App.cli()` maps it to the new constant `EXIT_TASK_FAILURE = 4` in `_constants.py`, distinct from `EXIT_RUNTIME_ERROR = 3`. The framework never calls `sys.exit` itself; embedders of `App.run()` can catch the exception. No `CosaletteError` base class is introduced (out of scope).

`"exit"` escalates on the first failure. `"ignore"` performs only the immediate reaction and leaves the entity offline in status `error` until the process restarts.

### 5. Policy resolution

`on_task_failure` is **app-wide only**: `App(on_task_failure="restart" | "exit" | "ignore")`. The implementation resolves the effective policy through a single helper that takes the registration, so a per-registration override can be added later without a breaking change. The rule for a coalescing group whose members would differ is fixed now: the strictest wins, `exit` > `restart` > `ignore`.

### 6. Framework-internal loops

These loops are always supervised and on an unexpected death escalate **straight to exit** - CRITICAL log, graceful teardown, `TaskSupervisionError(internal=True)`, exit code 4 - with no restarts, whatever `on_task_failure` says (`"ignore"` included). They are the watchdogs, a death is a framework bug, and recreating them would lose their state.

- heartbeat loop: `heartbeat_loop` / `start_heartbeat_task` (`_wiring/_task_lifecycle.py:39-72`);
- freshness watchdog (ADR-080): `freshness_loop` / `start_freshness_task` (`_wiring/_task_lifecycle.py:78-132`), started only when some entity has `stale_after`;
- adapter health checker (ADR-028/029): `HealthCheckRunner.run_loop` (`_health/_checker.py:96-107`) via `start_health_check_task` (`_wiring/_task_lifecycle.py:369`); it also hosts the ADR-029 restart callback;
- MQTT connection loop: `MqttClient._connection_loop` (`_mqtt/_client.py:350-353`, task `cosalette-mqtt-connection-loop`); it reconnects on `Exception` per iteration but can die (e.g. the `RuntimeError` raised when `aiomqtt` is missing, `:646-648`) and nothing restarts it;
- MQTT 5 retained-refresh loop: `MqttClient._refresh_loop` (`_mqtt/_client.py:361-364`, task `cosalette-mqtt-refresh-loop`), only with message expiry enabled.

The MQTT loops are owned by the `MqttClient` adapter, so it exposes them to the supervisor through a small hook; test doubles without such loops opt out. Not supervised: command-dispatch workers (`_mqtt/_router.py:242-246`) guard every message, remove themselves through their own done-callback and are recreated on the next message; one-shot helpers (connect-callback task, stream watcher, first-connect waiters) are awaited or cancelled by their owners.

### 7. Interaction with ADR-029

- Once an ADR-029 adapter restart begins it owns that adapter's tasks: the supervisor cancels pending supervisor restarts for them, and failures during the adapter restart do not count against the task budget. Invariant: **at most one live task per registration** (for a group, per group); it needs a dedicated test.
- Budgets and counters belong to the registration, not the task instance, and survive re-creation by ADR-029.
- Task failures do not feed the adapter's health failure count; the health check stays ADR-029's only signal.
- When an adapter is restart-exhausted (permanently offline), the supervisor does not restart its tasks; they stay offline and the app keeps running. See cos-5c4e for the related ADR-029 restart semantics.
- Because the ADR-029 restart callback runs inside the health-checker loop, an exception escaping it would now end the process (section 6). The callback must treat any exception - including from the post-restart `health_check()` - as a failed restart before supervision ships.

ADR-029's thresholds, budget and cooldown are otherwise unchanged.

### Future work

A per-registration `on_task_failure=` on `@app.telemetry`, `@app.device`, `@app.periodic`, `@app.stream` and their `Router` equivalents can be added later without breaking anything, through the resolution helper and group rule of section 5.

```python
import cosalette

app = cosalette.App(
    name="airthings2mqtt",
    version="1.4.0",
    on_task_failure="restart",  # default; "restart" | "exit" | "ignore" - app-wide only
)

# A telemetry task that dies:
#   CRITICAL Task 'telemetry:radon' died: <traceback>          (one traceback per crash)
#   airthings2mqtt/error        <- {"error_type": "error", "message": "OSError",
#   airthings2mqtt/radon/error         "details": {"task_failure": true, "task": "telemetry:radon"}, ...}
#   airthings2mqtt/radon/availability -> "offline"  (source="supervisor")
#   heartbeat: {"radon": {"status": "error", ...}}
#   re-created after 1 s, 2 s, 4 s (cap 60 s); first poll one interval later
#   4th failure before 300 s of quiet -> graceful teardown, then

try:
    app.run()
except cosalette.TaskSupervisionError as exc:
    print(exc.task_name, exc.restart_count, exc.internal, repr(exc.__cause__))
    raise
# App.cli() maps TaskSupervisionError to exit code 4 (EXIT_TASK_FAILURE).
```

## Decision Drivers

- A dead entity task must be reported within seconds, not at shutdown and not only after a telemetry stale_after window (ADR-080)
- Transient faults should heal without operator action in unattended deployments
- A persistent fault must not leave a healthy-looking process with a permanently dead entity; the container orchestrator owns full restarts
- A restart must never poll a device faster than its configured interval
- Task-level recovery should behave like ADR-029 adapter recovery (same budget, reset period, no jitter) and reuse DeviceTaskMap, the task factories and ADR-077 multi-source availability
- Expected cancellations (shutdown, ADR-029 adapter restart) must never trigger the policy
- The maintainer decided the default (restart with a budget, then exit) and all follow-up questions on 2026-10-02

## Considered Options

### Option 1: Supervisor with restart-with-budget default escalating to exit (chosen)

Attach a done-callback to every framework-started task; runners re-raise instead of returning. On a failure, log one CRITICAL, publish one error payload marked task_failure, mark mapped entities error and offline (source "supervisor"), and apply the app-wide on_task_failure: restart with 1-60 s backoff and a 3-restart budget reset after 300 s of quiet, skipping the immediate first cycle; raise TaskSupervisionError after graceful teardown when the budget is exhausted. Internal loops always escalate to exit.

- *Advantages:* Detects a dead task immediately for every archetype, not only telemetry; Recovers transient faults automatically with the same budget semantics as ADR-029; Bounded: a crash loop ends in about 10 s with a graceful teardown and a distinct exit code 4; Restarts never poll a device faster than its interval; Reuses existing task factories, trigger slots and availability sources
- *Disadvantages:* Most moving parts: backoff timers, per-registration budgets, expected-cancel tracking, a skip-first-cycle runner flag; Restart loses task-local state (dedup baseline, retry state, device loop variables); Behaviour change: device and stream crashes no longer end silently, and a persistent crash now ends the process

### Option 2: Supervisor that only reports (ignore by default)

Attach the done-callback, log CRITICAL and mark mapped entities error and offline, but never restart or exit unless the app opts in. Recovery relies on an operator, on ADR-080 freshness, or on a P-5 liveness probe.

- *Advantages:* Smallest behaviour change; no risk of restart storms; Simplest implementation and test surface
- *Disadvantages:* A transient fault still needs a manual container restart - the outage pattern the proposal reports; Depends on every adopter wiring a liveness probe to get recovery

### Option 3: Fail fast: exit on the first task failure

Treat any unexpected task end as fatal: log CRITICAL, run graceful teardown and exit non-zero, leaving all recovery to the container orchestrator (Docker restart policy, Kubernetes).

- *Advantages:* Simple and predictable; one code path; Fresh process state on every recovery
- *Disadvantages:* One flaky entity takes every healthy entity of the app offline for the restart duration; Deployments without a restart policy stay down; Each recovery re-runs lifespan, adapter initialisation, discovery and an immediate first poll of every device

### Option 4: Structured concurrency with asyncio.TaskGroup

Run all entity tasks inside one TaskGroup so an exception in any task cancels the group and propagates out of the run loop.

- *Advantages:* Idiomatic modern asyncio; no hand-written callbacks; Exceptions can never be silently lost
- *Disadvantages:* Semantically fail-fast for the whole app, with no per-task restart; Conflicts with ADR-029, which cancels and recreates individual tasks at runtime; Large refactor of the wiring layer for no gain over an explicit supervisor

## Decision Matrix

| Criterion | Supervisor with restart-with-budget default escalating to exit | Supervisor that only reports (ignore by default) | Fail fast: exit on the first task failure | Structured concurrency with asyncio.TaskGroup |
| --- | --- | --- | --- | --- |
| Time to detect a dead entity task | 5 | 5 | 5 | 5 |
| Automatic recovery from transient faults | 5 | 1 | 3 | 3 |
| Isolation: healthy entities stay online | 4 | 5 | 1 | 1 |
| Bounded behaviour under persistent faults | 5 | 3 | 4 | 4 |
| Device poll rate respected during recovery | 5 | 5 | 2 | 2 |
| Consistency with ADR-029 and ADR-077 | 5 | 4 | 3 | 1 |
| Implementation and test complexity | 2 | 5 | 4 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- A task that dies is visible within one event-loop turn - one CRITICAL line with traceback, exactly one error payload (original error_type and message, details.task_failure marker), heartbeat status error and retained offline - instead of a silent outage that surfaces only at shutdown
- Device and stream failures, which today end their task silently, are now reported, recovered and bounded like telemetry
- Transient faults heal without operator action; a fault that recurs within 300 s ends the process in about 10 s (deterministic) or on the 4th failure, with exit code 4 that Docker or Kubernetes restart policies act on, while faults rarer than once per 300 s are tolerated indefinitely
- Restarts never poll a device more often than its configured interval
- Task and adapter recovery share one budget model (3 restarts, 300 s reset, no jitter), so operators reason about both the same way
- Embedders of App.run() get a typed TaskSupervisionError with task name, restart count, internal flag and cause instead of a process exit
- Readable per-registration counters give P-5 (cos-4mv5.7) and P-3 (cos-4mv5.6) what they need without fixing a heartbeat format now
- The 'Task error during shutdown' path becomes a last-resort safety net

### Negative

- User-visible behaviour change: run_device and run_stream re-raise instead of returning, so a crashing device or stream handler is restarted and can end the process. docs/guides/command-device.md ("The device task ends, but other devices continue"), the streams concept page, the release notes and the version-migration guide must change. A device crash that ends the task no longer yields a runner payload plus a separate supervisor payload: it yields one payload, now carrying details.task_failure
- A supervisor restart loses task-local state - last_published, last_error_type, retry_count and a device handler's loop variables - so the first value after a restart is always published; adopters must persist state that has to survive via DeviceStore
- Skipping the immediate first cycle costs up to about two intervals of data gap per restart; the entity is offline during that time anyway
- A process exit takes every entity of the app offline until the orchestrator restarts it; deployments without a restart policy stay down
- Internal-loop deaths now end the process even under on_task_failure="ignore"; the ADR-029 restart callback, which runs inside the health-checker loop, must first be hardened so an adapter exception counts as a failed restart rather than killing the loop
- New public surface to document and keep stable: on_task_failure, TaskSupervisionError, EXIT_TASK_FAILURE = 4, the "supervisor" availability source and the details.task_failure payload marker
- The MqttClient adapter needs a hook to hand its connection and refresh loops to the supervisor, and the expected-cancel tracking plus the at-most-one-live-task-per-registration invariant add test burden

## Amendment (2026-10-02) — Corrective

**Rationale:** Section 1 said streams gain error/offline reporting through the supervisor, and the recovery rule cleared the supervisor source at a stream's first item. Implementing it (cos-pbd8) showed the per-stream availability topic {prefix}/{stream}/availability has no consumer: streams are excluded from Home Assistant discovery, AsyncAPI and the retained-cleanup snapshot, so the retained topic was never cleaned up, and a root stream published it, and its error payload, under its handler name ({prefix}/{funcname}/availability and {prefix}/{funcname}/error).

> **Justification for amendment (not supersession):** The decision is not yet released: ADR-081 and its implementation ship together in one unmerged PR (#484), so no adopter depends on the stream availability topic. The change is confined to how the supervisor reports stream failures; devices, telemetry, groups and periodic tasks are unaffected, and the restart policy is unchanged. Supersession would be disproportionate.

### Additional Sub-Decision: Streams report failure without availability

A stream task failure is reported through the CRITICAL log line, the one task-failure error payload and the stream's status in the `{prefix}/status` heartbeat only. The supervisor publishes no `{prefix}/{stream}/availability` (and, for a root stream, no `{prefix}/availability`) on failure, recovery, reannounce or shutdown, and a stream never enters the health reporter's device roster. The heartbeat status is `error` after a failure and `ok` at the first item of the re-created stream. A root stream (`@app.stream()` without a name) routes its payload with `is_root=True`, so it goes to `{prefix}/error` only. The `on_task_failure` policy, restart budget, backoff and exit code 4 apply to streams unchanged. This replaces "streams gain error/offline reporting" in section 1 and the stream case of the recovery rule.

!!! note "Editorial note (2026-10-02)"
    Future work (cos-4iim): a declared stream-to-device link, `@app.stream(..., feeds=[...])`, could let a stream failure mark the devices it feeds offline through the supervisor source. It is deferred: the documented stream pattern publishes everything itself, and no adopter needs it yet.

## Amendment (2026-10-02) — Corrective

**Rationale:** Sections 1 to 3 treat any exception that ends a coalescing-group task as a failure of the whole group, with one budget per group. A member's `init=` runs inside the group task, so one member whose sensor is out of range at startup failed the whole group: every healthy member went offline, and under the default policy the group was re-created until the budget ran out and the process exited with code 4. One flaky sensor out of range at startup must not crash-loop the whole app.

> **Justification for amendment (not supersession):** The decision is not yet released: ADR-081 and its implementation ship together in one unmerged PR (#484), so no adopter depends on group-wide handling of a member `init=` failure. The change is confined to member `init=` failures inside a coalescing group; every other group-task failure, ungrouped telemetry, devices, periodic tasks and streams keep the rules of sections 1 to 7, and the policy values, budget, backoff and exit code are unchanged. Supersession would be disproportionate.

### Additional Sub-Decision: A coalescing-group member's init= failure is isolated to that member

When a member's `init=` raises, the group runner hands the failure to the supervisor and the group task keeps running; the other members keep polling. The supervisor logs one CRITICAL line with the traceback, publishes one task-failure payload (`details.task` is the group task `group:<name>`, plus `details.member` and `details.phase = "init"`) to `{prefix}/error` and the member's `{prefix}/{member}/error`, and marks only that member offline through the `supervisor` source with heartbeat status `error`. The member is held out of the schedule and its trigger arms are held until it joins. The app-wide policy applies per member: under `"restart"` the runner retries the member's `init=` in place with the section-3 backoff (1 s doubling, 60 s cap) against a per-member budget of 3 restarts that resets after 300 s without a failure of that member, kept in the supervisor under the key `group:<name>/<member>` and reported through the same counters; a successful retry joins the member to the schedule at once, and the `supervisor` source and `error` status clear at its first successful cycle. An exhausted budget logs one CRITICAL line and leaves the member offline until the process restarts; it does not exit. Under `"exit"` the failure escalates as before (exit code 4), and the `TaskSupervisionError` names the member key `group:<name>/<member>`. Under `"ignore"` the failure is reported only and never retried. A member retry never creates a task, so the at-most-one-live-task-per-registration invariant holds.

### Additional Sub-Decision: A group with no active member idles until shutdown

When every member of a group is offline with no retry pending (budget spent or `"ignore"`), the group task logs one ERROR line and waits for shutdown. It neither returns nor raises, so there is no crash loop and no exit; health reporting already shows every member offline.

### Additional Sub-Decision: Member budgets survive task re-creation

Member records live in the supervisor, not in the group task, so they survive a supervisor re-creation of the group and an ADR-029 adapter restart. An ADR-029 adapter restart owns the group record (section 7) but not the member records. The re-created group attempts each member's `init=` once, and a failure counts against that member's carried-over budget, so a re-creation cannot reset a member's budget.

!!! note "Editorial note (2026-10-02)"
    Without a supervisor (a bare `TelemetryRunner`, as in unit tests) a member `init=` failure still propagates and ends the group task.

!!! note "Editorial note (2026-10-02)"
    Ungrouped telemetry `init=` failures are unchanged: the task fails and section 3 applies to it.

## Amendment (2026-10-03) — Corrective

**Rationale:** The first 2026-10-02 amendment ("Streams report failure without availability", Decision B) removed the per-stream availability topic because it had no consumer, was never cleaned up, and a root stream published it under its handler name. Each argument has since been answered: (1) consumers now exist: the new feeds= link drives fed entities from the stream's availability, ctx.mark_unavailable() / ctx.mark_available() give stream handlers manual marks, and anything watching a stream's /state needs to know whether that state is current; (2) cleanup is fixed: named streams join the ADR-048 retained-cleanup snapshot with the availability kind; (3) root streams are excluded: a root stream is heartbeat-only and never publishes any availability topic, so the {prefix}/{funcname}/availability leak cannot recur and {prefix}/availability stays app-wide; (4) the premise that streams are excluded from AsyncAPI was wrong: stream /state channels are already generated, and availability is auto-wired for every archetype just as for devices and telemetry. Without availability a stream cannot express a crash, a manual outage or staleness to anything but a human reading the heartbeat (cos-pbd8, cos-4iim).

> **Justification for amendment (not supersession):** ADR-081 is merged but unreleased: the latest release (0.10.6) predates it, so no adopter depends on streams lacking an availability topic. The change is confined to stream health reporting: devices, telemetry, groups, periodic tasks and the on_task_failure policy, restart budget, backoff and exit code are unchanged, and the two new stream parameters are opt-in with inert defaults. Supersession would be disproportionate.

### Additional Sub-Decision: Named streams own their availability (supersedes "Streams report failure without availability")

A named stream owns a retained `{prefix}/{stream}/availability`, managed by the health reporter's multi-source mechanism (ADR-077). It is announced `online` on first connect, re-asserted on reconnect, published `offline` at shutdown and recorded in the ADR-048 retained-cleanup snapshot with the `availability` kind. A crash marks it `offline` under the `supervisor` source with heartbeat status `error`; the first item of the re-created stream clears that source. `ctx.mark_unavailable()` and `ctx.mark_available()` work in stream handlers under the `manual` source. Every stream shows `ok` in the `{prefix}/status` heartbeat from startup. Home Assistant discovery still excludes streams. This replaces the sub-decision "Streams report failure without availability" of the first 2026-10-02 amendment; the on_task_failure policy, restart budget, backoff and exit code 4 apply to streams unchanged.

### Additional Sub-Decision: Root streams are heartbeat-only

A root stream (`@app.stream()` without a name) never publishes an availability topic and never touches `{prefix}/availability`, which stays app-wide. Its availability sources (supervisor, manual, freshness) still change its heartbeat status to `error`, `unavailable` or `stale`, and its task-failure payload goes to `{prefix}/error` only. This differs deliberately from root telemetry, whose explicit opt-ins publish `{prefix}/availability`: a stream is a bridge, not the app's primary entity.

### Additional Sub-Decision: Opt-in stale_after= for streams

`@app.stream(..., stale_after=...)` and the Router equivalent take the telemetry type (`TimeoutSpec | None`, default `None`). A callable is resolved against settings at bootstrap; nothing is derived. Every yielded item counts as a success. When no item arrives within the bound, the stream goes `offline` under the `freshness` source and shows `stale` in the heartbeat; the next item restores it. Stream bounds join the freshness watchdog, so they also feed `exit_after_stale=` (ADR-083) and the health file; `restart_on_stale` does not apply because streams are not in the adapter map. On a root stream the bound is heartbeat-only.

### Additional Sub-Decision: Opt-in feeds= link from a stream to the entities it supplies

`@app.stream(..., feeds=[...])` names devices or telemetry entities whose availability follows the stream. Names are validated at bootstrap, after name expansion and enabled= resolution: an unknown name or a root entity fails fast with ValueError; a bare str is rejected at decoration with TypeError. A root stream may not declare feeds= (ValueError at decoration). While the stream is offline from any source, each fed entity is held `offline` under the source `stream:{name}`, which clears when the stream is online again. The fed entity's own sources stay independent: neither side clears the other. The link carries availability only, no reactor or effects metadata.

!!! note "Editorial note (2026-10-03)"
    The approved design also asked for an AsyncAPI availability channel next to each named stream's /state. It was not added: the generator emits no availability channel for any archetype, so a stream-only channel would be inconsistent and would change the schema consumers see. Follow-up cos-0iyk tracks a uniform decision.

!!! note "Editorial note (2026-10-03)"
    Out-of-scope follow-ups found during implementation: streams are missing from the ADR-029 adapter-to-device map (cos-kg37), and ctx.sub_entity() in a stream publishes availability outside the health reporter (cos-cpbq).

!!! note "Editorial note (2026-10-03)"
    A stale stream that trips exit_after_stale= raises StaleTelemetryError, whose message still says "Telemetry"; the exception type and message are unchanged for compatibility.

### Additional Positive Consequences

- A stream crash, manual outage or stale feed is visible to machines on a retained topic, and can take the entities it supplies offline through feeds=

### Additional Negative Consequences

- Named streams add one retained topic each, and root streams behave differently from root telemetry for manual marks and stale_after

## Amendment (2026-10-03) — Minor

!!! note "Editorial note (2026-10-03)"
    Correction to the 2026-10-03 named-stream amendment: streams are now in the ADR-029 adapter map (cos-kg37), so adapter health checks and adapter restarts cover them. `restart_on_stale` still applies to telemetry only, because the stale-restart map is built from telemetry registrations.

!!! note "Editorial note (2026-10-03)"
    `ctx.sub_entity()` inside a stream keeps the ADR-031 behaviour (cos-cpbq): the context manager publishes the sub-entity's availability directly, outside the health reporter. A crash takes it offline because the handler leaves the `async with` block, and it comes back when the re-created handler enters the block again. It is not in the heartbeat roster, the reconnect re-announce or the shutdown sweep, and supervisor, manual, freshness, feed and adapter-health sources do not touch it, the same as for device sub-entities.

## Amendment (2026-10-03) — Minor

!!! note "Editorial note (2026-10-03)"
    Follow-up cos-0iyk is resolved by ADR-086. The generated AsyncAPI document now has a framework availability channel (`x-cosalette-framework: "availability"`) for every entity that owns availability, named streams included, not only for streams. This replaces the 2026-10-03 named-stream amendment's note that no availability channel is generated for any archetype.

!!! note "Editorial note (2026-10-03)"
    Correction to point (4) of the 2026-10-03 named-stream amendment's rationale: availability is not auto-wired for every archetype. It is owned by devices, telemetry, commands and named streams. Root streams are heartbeat-only and publish no availability topic. Sub-entities created with `ctx.sub_entity()` publish their own topic outside the health reporter and are not in the generated schema (ADR-086).

## Amendment (2026-10-08) — Minor

!!! note "Editorial note (2026-10-08)"
    Correction to the 2026-10-03 sub-decision "Opt-in stale_after= for streams" and the 2026-10-03 minor amendment: `restart_on_stale` now applies to streams with `stale_after=` (cos-02wc). The stale-restart map is built from telemetry entities and streams that have a freshness bound, so a stale stream requests a restart of the restartable adapters it depends on with the telemetry semantics. See the 2026-10-08 amendment of ADR-084.
