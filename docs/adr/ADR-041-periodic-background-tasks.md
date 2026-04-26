---
status: Accepted
date: 2026-04-26
impact: moderate
tags: [lifecycle, background-tasks, scheduling]
---

# ADR-041: Periodic Background Tasks

## Status

Accepted **Date:** 2026-04-26

## Context

IoT bridge applications frequently need **side-effect tasks** that run on a fixed interval but have no MQTT presence: cache warming, watchdog pings, LED state synchronisation, write-buffer flushing, and background database sync. None of the existing primitives fits cleanly:

- `@app.telemetry` is MQTT-publish-centric: it owns a `/{name}/state` topic, carries publish strategies, retry logic, error publishing, and coalescing group participation. Adding `publish=None` would leave all of that machinery present but dormant, creating semantic confusion and dead-code paths in the hot loop.
- `@app.device` is a long-running coroutine where the developer owns the `while True` loop. For a simple periodic flush, this forces boilerplate that is not warranted: `ctx.sleep`, `ctx.shutdown_requested` checks, and a `DeviceContext` dependency with no MQTT calls.

The gap is a **purpose-built interval primitive** that runs on a fixed interval without MQTT coupling, isolates exceptions (log and continue) without an error topic, follows the established `@app`-decorator + dataclass registration pattern, reuses `IntervalSpec` (ADR-020) and `EnabledSpec` (ADR-038), and supports `init=` one-shot setup (same as `@app.device`, ADR-010).

## Decision

Add `@app.periodic` as a first-class decorator on `App`. A coroutine decorated with `@app.periodic` is registered as a `_PeriodicRegistration` dataclass, spawned as an `asyncio.Task` during Phase 3 (Run), and cancelled with a 5-second grace period during Phase 4 (Teardown).

The runtime loop sleeps first, then invokes the handler. Exceptions other than `CancelledError` are caught, logged at `ERROR` level, and the loop continues. `CancelledError` propagates so shutdown cancellation works cleanly.

New file `_periodic.py` contains `_PeriodicRegistration` and `run_periodic()`. `_wiring.py` gains `resolve_intervals_periodic()`, `start_periodic_tasks()`, and `cancel_periodic_tasks()`. `AppHarness` gains `tick_periodic(name)` for single-cycle test invocation and a `run_periodic=False` default on `create()` so existing tests are unaffected.

```python
import datetime
import cosalette
from cosalette import SettingRef

app = cosalette.App(name="bridge", version="1.0.0")


@app.periodic("flush-buffer", interval=30.0)
async def flush_buffer(cache: BufferCache) -> None:
    """Flush accumulated readings to the upstream API every 30 s."""
    await cache.flush()


@app.periodic(
    "watchdog",
    interval=datetime.timedelta(minutes=1),
    enabled=lambda s: s.watchdog_enabled,
)
async def watchdog_ping(settings: AppSettings) -> None:
    await ping_watchdog(settings.watchdog_url)


@app.periodic("led-sync", interval=SettingRef("led_interval", default=5.0))
async def led_sync(led: LedPort) -> None:
    await led.sync_state()
```

## Decision Drivers

- IoT apps need side-effect tasks (buffer flush, watchdog, LED control) that have no MQTT output and do not fit @app.telemetry or @app.device
- Extending @app.telemetry with publish=None would carry telemetry baggage (publish strategies, error publishing, coalescing) into an MQTT-free context, leaving dead code in the hot loop
- Exception isolation must be self-contained: no error topic exists for periodic tasks, so log-and-continue is the correct and explicit behaviour
- IntervalSpec (ADR-020) and EnabledSpec (ADR-038) already provide a proven deferred-resolution vocabulary that should be reused rather than duplicated
- AppHarness testing model requires opt-in spawning (run_periodic=False default) so existing test suites are unaffected by new periodic registrations

## Considered Options

### Option 1: Extend @app.telemetry with publish=None

Add a sentinel value publish=None to @app.telemetry that suppresses MQTT publication entirely, repurposing the polling loop as a background task.

- *Advantages:* No new decorator surface — developers already know @app.telemetry; Reuses the existing TelemetryRunner loop and scheduling infrastructure
- *Disadvantages:* publish strategies, ErrorPublisher, DeviceContext, and coalescing group logic are all present but inert — dead code in the hot path; Semantic confusion: a 'telemetry' handler that publishes nothing is architecturally misleading; Error publishing would need to be explicitly suppressed rather than being absent by design; No clean test seam: tick_periodic() cannot exist for a concept that does not have its own identity

### Option 2: New @app.periodic decorator (chosen)

Introduce a dedicated @app.periodic decorator backed by a _PeriodicRegistration dataclass and a purpose-built run_periodic() async loop with no MQTT coupling.

- *Advantages:* Clean separation: no telemetry baggage, no dead code in the loop; Purpose-built exception isolation (log and continue) without an error topic; Simpler DI: no DeviceContext needed — inject Settings, ports, ClockPort, and @app.state instances directly; Reuses proven IntervalSpec and EnabledSpec vocabulary from ADR-020 and ADR-038; AppHarness.tick_periodic() provides a clean, named test seam without spawning tasks
- *Disadvantages:* New decorator surface and registration dataclass to maintain; Adds _periodic.py and three new _wiring.py functions to the framework surface

### Option 3: Generic @app.background with task_type=

Add a single generic @app.background decorator parameterised by a task_type enum (periodic, oneshot, continuous) to unify all background task patterns.

- *Advantages:* One decorator for all background task variants — potential for future task types without additional API growth
- *Disadvantages:* Over-engineered for a well-scoped feature: no oneshot or continuous variants are currently needed; A task_type= parameter breaks the established archetype-per-decorator API convention; Would require all three task types to be designed simultaneously, increasing scope and review burden

## Decision Matrix

| Criterion | Extend @app.telemetry with publish=None | New @app.periodic decorator | Generic @app.background with task_type= |
| --- | --- | --- | --- |
| Semantic clarity | 2 | 5 | 3 |
| Exception isolation quality | 2 | 5 | 4 |
| API consistency with existing decorators | 3 | 5 | 2 |
| Implementation scope and risk | 3 | 4 | 2 |
| Testability via AppHarness | 3 | 5 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- IoT apps can declare cache-warming, watchdog, LED-sync, and buffer-flush tasks with a single decorator and zero MQTT boilerplate
- Exception isolation is purposeful: no MQTT error topic exists, so log-and-continue is the correct and explicit behaviour by construction
- DI injection (Settings, ports, @app.state instances) works identically to @app.device — no new learning required
- AppHarness.tick_periodic() provides a deterministic test seam: one call, one handler cycle, no sleeping
- Existing tests are unaffected: run_periodic=False (default in AppHarness.create()) suppresses task spawning transparently
- IntervalSpec accepts float, datetime.timedelta, Callable, and SettingRef — full parity with @app.telemetry interval flexibility

### Negative

- New file _periodic.py and three new functions in _wiring.py increase framework surface area
- Developers must choose between @app.periodic, @app.telemetry, and @app.device — the primitive set grows and the decision guide must be updated

_2026-04-26_
