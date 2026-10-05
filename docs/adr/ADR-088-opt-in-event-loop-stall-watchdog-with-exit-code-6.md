---
status: Accepted
date: 2026-10-05
impact: moderate
tags: [health, lifecycle, configuration, cli]
---

# ADR-088: Opt-in event-loop stall watchdog with exit code 6

## Status

Accepted **Date:** 2026-10-05

## Context

Since the ADR-083 amendment of 2026-10-04, the recommended health setup is MQTT signals plus a supervised restart: `restart: unless-stopped` together with `exit_after_stale` (exit code 5) and `on_task_failure` (exit code 4, ADR-081). Both exits run on the event loop. When the loop itself is wedged (a synchronous serial or BLE read in an adapter, a deadlock, a C extension that never returns), neither check runs and the process stays up forever. The broker publishes the LWT once keepalives stop (about 1.5 x the aiomqtt default keepalive of 60 s), but plain Docker and Docker Compose never restart a running container, and the ADR-083 health file only helps where an orchestrator acts on a probe. The amendment names this as the remaining gap.

A Python thread can watch the loop from outside: it keeps running while the loop thread sleeps or does I/O in C code that releases the GIL, and it can call `os._exit`, which needs no loop. It cannot run while the loop thread holds the GIL in C code. The standard library's `faulthandler.dump_traceback_later(timeout, exit=True)` runs in a C thread without the GIL, but always exits with code 1. Startup (settings, adapter `__aenter__`, `on_configure`, `@app.state` factories, the lifespan) may legitimately run synchronous code for seconds: opening a serial port, loading a model, a blocking BLE scan. The maintainer-approved plan (cos-ht8o.6) fixes two points up front: the watchdog is configured by an environment variable, not an `App()` parameter, and it is off by default.

## Decision

Use an opt-in watchdog thread, armed from the run phase until shutdown begins, plus a faulthandler backstop, to end a process whose event loop has stalled with the new exit code 6 (`EXIT_LOOP_STALL`), because exiting is the only recovery a supervisor can apply to a wedged loop and a distinct code tells operators why it happened.

- **Configuration.** `COSALETTE_LOOP_STALL_TIMEOUT` holds the timeout in seconds. Unset or blank means off. Any other value must be a finite number greater than 0; otherwise the app does not start and the CLI exits with code 1 (`LoopStallConfigError`). The variable name is fixed, not app-prefixed, like `COSALETTE_HEALTH_FILE`. The docs suggest 300 s.
- **Mechanism.** A timer callback on the loop (`loop.call_later`, no task) records `time.monotonic()` every `min(timeout / 4, 5 s)`. A daemon thread checks the timestamp at the same interval. When it is older than the timeout, the thread writes one `CRITICAL cosalette: event loop stalled ...` line and `faulthandler.dump_traceback(all_threads=True)` straight to fd 2 with `os.write`, not through `logging` (a loop wedged inside a log handler holds the handler lock), then calls `os._exit(6)`. The MQTT client never disconnects, so the broker publishes the LWT.
- **Backstop.** Each beat also re-arms `faulthandler.dump_traceback_later(2 x timeout, exit=True)` on fd 2. It fires only if the thread could not run (loop stuck in C code holding the GIL); it writes faulthandler's `Timeout (H:MM:SS)!` header and every thread's stack, then exits with code 1, not 6. The timer is process-wide, so apps must not use `dump_traceback_later` themselves while the watchdog is on.
- **When it is armed.** The watchdog is armed when the run phase starts: after adapters, `@app.state` factories and the lifespan have been entered and the startup health checks have run, at the same point the health file starts. It therefore covers the wait for the first broker connection, every entity task and steady state. It is disarmed when graceful shutdown starts (the shutdown event is set), before tasks are cancelled and the lifespan exits, and on any error that ends the run phase. Startup and teardown are not watched.

```yaml
# docker-compose.yml
services:
  myapp:
    restart: unless-stopped  # restarts on exit codes 1, 3, 4, 5 and 6
    environment:
      COSALETTE_LOOP_STALL_TIMEOUT: "300"
```

## Decision Drivers

- A wedged event loop must end in a process exit so a plain `restart: unless-stopped` recovers it
- Operators must be able to tell a loop stall from other exits and see where the loop was stuck
- Apps that do not opt in must see no change: no thread, no faulthandler timer
- Legitimate synchronous work during startup must not be killed by a timeout sized for steady state
- The detection path must not depend on the loop, on logging locks, or on the GIL being free
- No new dependency and negligible idle cost on a Raspberry Pi

## Considered Options

### Option 1: Watchdog thread plus faulthandler backstop, armed for the run phase (chosen)

A daemon thread exits with code 6 when a loop-side timestamp is older than COSALETTE_LOOP_STALL_TIMEOUT; a re-armed faulthandler timer at twice the timeout covers a GIL-holding stall. Armed after the lifespan and startup health checks, disarmed when shutdown starts.

- *Advantages:* Distinct exit code 6 and a full stack dump on stderr; Covers stalls in C code that holds the GIL through the backstop; Startup work of any length is never mistaken for a stall
- *Disadvantages:* A stall during startup or teardown is not detected; The backstop exits with code 1, not 6; faulthandler's later-dump timer is process-wide, so an app that uses it itself conflicts

### Option 2: faulthandler.dump_traceback_later only

Re-arm `faulthandler.dump_traceback_later(timeout, exit=True)` from a loop callback and add no thread.

- *Advantages:* About two lines of code; Works even while the loop holds the GIL
- *Disadvantages:* Always exits with code 1, the same as a configuration error; No CRITICAL line saying why the process ended

### Option 3: Watchdog armed from process start

Arm the same watchdog as soon as `_run_async` starts, so startup is watched with the same timeout.

- *Advantages:* A hang in an adapter `__aenter__` or the lifespan also restarts the app; One rule, nothing to explain about phases
- *Disadvantages:* Legitimate synchronous startup work longer than the timeout causes a restart loop; Forces a timeout sized for the slowest startup instead of steady state

### Option 4: Separate startup grace period

Arm from process start with a second variable, e.g. COSALETTE_LOOP_STALL_STARTUP_GRACE, that applies until the run phase begins.

- *Advantages:* Watches startup and steady state with suitable limits each
- *Disadvantages:* A second knob to document, validate and test; Most startup hangs are I/O waits that do not stall the loop and are bounded by startup_connect_timeout or adapter timeouts anyway

### Option 5: App(exit_on_loop_stall=) parameter

The proposal's API: configure the timeout in code, like exit_after_stale.

- *Advantages:* Discoverable next to exit_after_stale; Type-checked
- *Disadvantages:* A per-deployment knob baked into the app; a host-specific value needs an app release; Rejected by the maintainer in favour of an environment variable

## Decision Matrix

| Criterion | Watchdog thread plus faulthandler backstop, armed for the run phase | faulthandler.dump_traceback_later only | Watchdog armed from process start | Separate startup grace period | App(exit_on_loop_stall=) parameter |
| --- | --- | --- | --- | --- | --- |
| Distinct, explained exit | 5 | 2 | 5 | 5 | 5 |
| No false positives during startup | 5 | 4 | 2 | 4 | 4 |
| Coverage of stalls | 4 | 4 | 5 | 5 | 4 |
| Configuration surface | 5 | 5 | 5 | 3 | 3 |
| Implementation and runtime cost | 4 | 5 | 4 | 3 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Closes the gap named in the ADR-083 amendment: on plain Docker a wedged loop now ends in a restart instead of an app that is offline forever
- Exit code 6 and the stack dump on stderr say what was blocking the loop
- The broker publishes the LWT immediately because the process ends without a clean disconnect
- Off by default; apps that do not set the variable start no thread

### Negative

- Adds exit code 6, which process supervisors must treat as a failure
- A handler or adapter that blocks the loop longer than the timeout, even once, restarts the app; the timeout must exceed the longest legitimate blocking call
- A stall during startup (adapter `__aenter__`, `on_configure`, lifespan) or teardown is not detected
- A stall in C code that holds the GIL exits with code 1, not 6, from the faulthandler backstop after twice the timeout. Its stderr signature is faulthandler's `Timeout (H:MM:SS)!` header (for example `Timeout (0:10:00)!` with a 300 s timeout; fractional delays add microseconds) followed by every thread's stack, with no `CRITICAL cosalette: event loop stalled` line. `restart: on-failure` and `unless-stopped` still restart the container on code 1, but the exit code alone no longer says that the loop stalled
- faulthandler has a single process-wide `dump_traceback_later` timer. The watchdog re-arms it on every beat and cancels it on stop, so an app must not combine the watchdog with its own `dump_traceback_later`: each call replaces the other's timer
- `os._exit` skips the lifespan exit, store flushes and the retained offline publishes of a graceful shutdown

_2026-10-05_
