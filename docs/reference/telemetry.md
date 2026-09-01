---
icon: material/thermometer-lines
---

# Telemetry Reference

Lookup reference for telemetry configuration values, scheduling syntax, and
resilience parameters. For task instructions see
[Build a Telemetry Device](../guides/telemetry-device.md); for advanced features
(cron scheduling, retry, coalescing, triggerable) see
[Advanced Telemetry Techniques](../guides/telemetry-advanced.md).

## Interval Guidelines

| Sensor Type             | Typical Interval | Notes                              |
| ----------------------- | ---------------- | ---------------------------------- |
| Temperature / humidity  | 30–60 s          | Slow-changing physical quantities  |
| Energy / impulse        | 10–60 s          | Depends on consumption rate        |
| Motion / presence       | 1–5 s            | Fast-changing binary sensor        |
| Battery level           | 300–600 s        | Very slow-changing                 |

## Trigger Sources

`triggerable=` declares which paths may wake a telemetry entity ahead of its
next scheduled run (ADR-036, ADR-064). `interval=` remains required and acts as
the heartbeat / fallback poll.

| `triggerable=`    | Subscribes `{prefix}/{device}/set` | Armed by `EntityNotifier` | Root device |
| ----------------- | ---------------------------------- | ------------------------- | ----------- |
| `False` (default) | no                                 | no                        | allowed     |
| `True` / `"mqtt"` | yes                                | no                        | rejected    |
| `"local"`         | no                                 | yes                       | allowed     |
| `"both"`          | yes                                | yes                       | rejected    |

`TriggerPayload.source` reports what armed the current run: `"scheduled"`,
`"mqtt"` or `"local"`. `triggerable=` cannot be combined with `group=`.

`@app.device` also accepts `triggerable=`, but only `False` or `"local"`:
`{prefix}/{device}/set` is already the device command topic, so an MQTT
trigger source is rejected (ADR-065). A triggerable device must declare a
`DeviceTrigger` parameter and await the wake itself.

For task instructions see
[Triggerable Telemetry](../guides/telemetry-advanced.md#triggerable-telemetry).

## Storm Throttle

`min_interval=` bounds the minimum spacing, in seconds, between the **starts**
of two trigger-initiated runs (ADR-066). It is opt-in: the default `None` is
off, and the unthrottled path is unchanged.

| Value              | Effect                                                     |
| ------------------ | ---------------------------------------------------------- |
| `None` (default)   | No throttle — every wake runs as soon as it is observed     |
| positive `float`   | Leading edge runs at once; wakes inside the window coalesce |

Semantics:

- **Leading edge** — the first wake after a quiet period runs immediately.
- **Trailing edge** — wakes arriving inside a closed window coalesce into
  exactly one run that fires when the window reopens, carrying the **last**
  payload. Nothing is dropped.
- **A quiet period reopens the window**, so the next wake is a leading edge.
- **`interval=` is never throttled.** A heartbeat run is not postponed past its
  own deadline by a held wake, sees `TriggerPayload.scheduled()`, and does not
  consume the wake — the trailing run still gets it.
- **`publish=` is orthogonal.** The window counts run *starts*, so a run whose
  publish was suppressed by `OnChange` still spends the window.

With `min_interval=2.0` and wakes at `t=0.0, 0.1, 0.4, 1.9`:

| Time  | Event                    | Result                              |
| ----- | ------------------------ | ----------------------------------- |
| 0.0   | wake (quiet window)      | leading-edge run                    |
| 0.1   | wake (window closed)     | held                                |
| 0.4   | wake (window closed)     | held, replaces the `t=0.1` payload  |
| 1.9   | wake (window closed)     | held, replaces the `t=0.4` payload  |
| 2.0   | window reopens           | one trailing run, `t=1.9` payload   |

`min_interval=` requires a trigger source: without `triggerable=` there is
nothing to throttle, and registration raises `ValueError`. The value must be a
finite, strictly positive number of seconds.

`@app.device` accepts `min_interval=` on the same terms; `DeviceTrigger.wait()`
enforces it. A `timeout=` shorter than the remaining window still returns
`TriggerPayload.scheduled()` **while the wake stays pending** — the next
`wait()` delivers it.

## Timeout Backstop


A handler that *hangs* mid-`await` — a BLE characteristic read, a serial port
blocking on `.read()`, an HTTP call with no internal timeout — never raises, so
the retry/error machinery never activates. In production (framework finding F-3),
an airthings2mqtt BLE read hung indefinitely: the device's retained state topic
went silent for ~10 days with zero log entries, zero error publishes, and a restart
counter of 0.

The `timeout=` parameter bounds each handler invocation with
`asyncio.wait_for`. If the handler does not return within the deadline,
a `TimeoutError` is raised — making the hang visible to the full
error/retry/health pipeline exactly like any other raised exception.

/// admonition | Behavior change for interval-based handlers
    type: warning

As of this release, **every interval-based telemetry handler gets an implicit
timeout backstop equal to its resolved `interval`**. This is a behavior change:
handlers that previously hung indefinitely will now raise `TimeoutError` after
one interval period. To opt out, pass `timeout=None` explicitly.
///

### Semantics

| `timeout=` value | Resolved backstop |
| ---------------- | ----------------- |
| omitted | auto-default: resolved `interval` — the framework cannot know whether an adapter has its own timeout, so this is the universal defence |
| `None` | disabled — no backstop; use for legitimately long-running handlers |
| explicit `float` | seconds, used as-is |
| `Callable` | deferred-resolved at bootstrap, exactly like `interval` — supports `setting_ref()` and per-device callables |

Cron-scheduled telemetry (`schedule=`) receives **no auto-default** — a daily or
hourly cron period would be a useless hang bound for most handlers. Cron handlers
opt in with an explicit `timeout=`.

### Composing with Retry

`TimeoutError` is a subclass of `OSError` (PEP 3151, Python 3.3+). Because the
default `retry_on=(OSError,)` already covers `TimeoutError`, a timed-out handler
automatically flows through the full retry/backoff/error pipeline with zero extra
configuration:

```python title="app.py"
@app.telemetry(
    "sensor",
    interval=1500,      # 25 min poll cycle
    timeout=120,        # raise TimeoutError if handler runs > 2 min
    retry=3,            # TimeoutError ⊂ OSError → auto-retried
)
async def sensor(ctx: cosalette.DeviceContext) -> dict[str, object]:
    adapter = ctx.adapter(BLESensorPort)
    return {"temperature": await adapter.read_temperature()}  # (1)!
```

1. If the BLE read hangs for over 120 seconds, `asyncio.wait_for` raises
   `TimeoutError`. The retry loop sees an `OSError` subclass, logs the attempt
   at WARNING, waits for the backoff delay, and retries up to 3 times before
   the error is published to `{prefix}/sensor/error`.

For handlers that are expected to run longer than the poll interval — e.g. a
nightly sync that legitimately takes several minutes — pass `timeout=None` to
disable the backstop:

```python title="app.py"
@app.telemetry("nightly", schedule="0 0 2 * * ?", timeout=None)
async def nightly_sync() -> dict[str, object]:
    """Runs at 02:00 — may take several minutes."""
    ...
```

See [ADR-024](../adr/ADR-024-telemetry-retry-backoff.md) (Decision 6) for the
full design rationale, or run `cosalette ai help resilience` for an inline summary.

## Retry and Backoff Strategies

### Built-in Strategies

| Strategy | Default parameters | Delay sequence | Best for |
| -------- | ------------------ | -------------- | -------- |
| `ExponentialBackoff(base, max_delay)` | `base=2.0`, `max_delay=60.0` | `base^attempt` ± 20 % jitter, capped | Default — most transports |
| `LinearBackoff(step, max_delay)` | `step=1.0`, `max_delay=30.0` | `step × attempt`, capped | Predictable, bounded delays |
| `FixedBackoff(delay)` | `delay=2.0` | constant `delay` | External rate-limited APIs |

All three are imported from `cosalette`. Custom strategies implement the
`BackoffStrategy` protocol: a single method `delay(attempt: int) -> float`.

### Circuit Breaker States

| State        | Behaviour                                               |
| ------------ | ------------------------------------------------------- |
| **Closed**   | Normal operation — handler runs, failures counted       |
| **Open**     | Handler skipped — no retries, no error publishes        |
| **Half-open**| A single probe attempt — success closes, failure re-opens |

`CircuitBreaker(threshold=N)` opens after `N` consecutive failures across poll
cycles. See [Retry / Backoff](../guides/telemetry-advanced.md#retry-backoff) for
full usage examples and [ADR-024](../adr/ADR-024-telemetry-retry-backoff.md) for
design rationale.

## Cron Syntax Reference

Cosalette uses Quartz-compatible cron expressions with 6 or 7 fields:

```text
┌───────────── second (0-59)
│ ┌───────────── minute (0-59)
│ │ ┌───────────── hour (0-23)
│ │ │ ┌───────────── day of month (1-31)
│ │ │ │ ┌───────────── month (1-12 or JAN-DEC)
│ │ │ │ │ ┌───────────── day of week (1-7, 1=SUN, or SUN-SAT)
│ │ │ │ │ │ ┌───────────── year (optional)
│ │ │ │ │ │ │
* * * * * * *
```

Common examples:

| Expression | Fires at |
| ---------- | -------- |
| `0 0 6 * * ?` | Daily at 06:00:00 |
| `0 0 6,18 * * ?` | Daily at 06:00 and 18:00 |
| `0 30 * * * ?` | Every hour at :30 |
| `0 0 0 1 * ?` | First day of each month at midnight |
| `0 0 8 ? * MON-FRI` | Weekdays at 08:00 |

!!! note "Timezone"
    Scheduled times use the system's local timezone by default.
    In Docker containers, this is controlled by the `TZ` environment variable.
    DST transitions may shift scheduled times by ±1 hour.

### Pre-Parsed Schedules

For validation at import time or reuse, parse the expression explicitly:

```python
from cosalette import CronSchedule

morning = CronSchedule("0 0 6 * * ?")
```

`CronSchedule` validates eagerly — invalid expressions raise `ValueError`
at construction time, not when the first fire is due.

See [Cron-Based Scheduling](../guides/telemetry-advanced.md#cron-based-scheduling)
for full usage examples and
[ADR-032](../adr/ADR-032-sleep-until-wall-clock-scheduling.md) for design rationale.
