# ADR-032: Cron Scheduling and Wall-Clock Sleep

## Status

Accepted **Date:** 2026-04-06

## Context

Some cosalette applications need time-of-day-aligned polling rather than fixed-interval
scheduling. caldates2mqtt polls calendar data every 2 hours, but calendar events change
on day boundaries — a cron-like schedule ("0 6,18 \* \* \*") would be more natural and
efficient than 12 polls/day when 2 well-timed polls suffice.

Two complementary features serve this need:

1. **`schedule=` on `@app.telemetry`** — declarative cron-based scheduling for the
   common case. Uses a built-in 6-or-7-field Quartz cron parser (no external dependency).
2. **`ctx.sleep_until()` on `DeviceContext`** — imperative wall-clock sleep for
   `@app.device` functions that need custom scheduling logic.

### Why both?

`schedule=` is the right API for the common "poll at fixed times" pattern — it's
declarative, validates eagerly, and fits the existing `@app.telemetry` model. But
`@app.device` functions need a composable primitive (`ctx.sleep_until`) because they
manage their own control flow.

## Decision

### 1. Built-in cron parser (`cosalette._cron`)

Provide a dependency-free cron parser supporting the **Quartz cron format**.
Expressions have 6 or 7 fields:

```
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

Supported syntax per field:

| Syntax   | Meaning                                    | Fields               |
| -------- | ------------------------------------------ | -------------------- |
| `*`      | Every value                                | All                  |
| `?`      | No specific value                          | day-of-month, day-of-week |
| `N`      | Specific value                             | All                  |
| `N-M`    | Range                                      | All                  |
| `N/S`    | Step from N                                | All                  |
| `*/S`    | Step from field min                        | All                  |
| `N,M,…`  | List                                       | All                  |
| `L`      | Last day of month, or last X day of week   | day-of-month, day-of-week |
| `L-N`    | Nth-to-last day of month                   | day-of-month         |
| `W`      | Nearest weekday to given day               | day-of-month         |
| `LW`     | Last weekday of month                      | day-of-month         |
| `N#M`    | Mth occurrence of weekday N                | day-of-week          |
| `SUN`…   | Named days                                 | day-of-week          |
| `JAN`…   | Named months                               | month                |

A `CronSchedule` value object exposes:

```python
class CronSchedule:
    def __init__(self, expression: str) -> None: ...
    def next_fire_after(self, after: datetime.datetime) -> datetime.datetime: ...
```

`next_fire_after` returns the next datetime strictly after `after` that matches the
expression.

### 2. `schedule=` parameter on `@app.telemetry`

```python
@app.telemetry("calendar", schedule="0 6,18 * * *")
async def read_calendar() -> dict[str, object]:
    ...
```

- `schedule=` and `interval=` are **mutually exclusive**. Providing both raises
  `ValueError` at registration time.
- `interval=` remains required when `schedule=` is not provided (backward compatible).
- When `schedule=` is set, the telemetry runner computes
  `_seconds_until_next_fire(schedule, tz)` instead of using a fixed interval.
- First execution runs immediately on startup (consistent with interval-based
  telemetry), then waits for the next scheduled time.

### 3. `ctx.sleep_until()` on `DeviceContext`

```python
async def sleep_until(
    self,
    target: datetime.time | Sequence[datetime.time],
    *,
    tz: datetime.tzinfo | None = None,
) -> None:
```

- Accepts a single time or a sequence of times. When given a list, sleeps until the
  nearest upcoming time.
- Shutdown-aware via existing `ctx.sleep()`.
- The time-difference math is extracted into a pure `_seconds_until()` helper for
  easy testing.

### 4. Default timezone — Local

Both `schedule=` and `ctx.sleep_until()` default to the system's local timezone when
`tz=None`. Most cosalette apps run in Docker containers with a configured `TZ` env var.
Users who want UTC pass `tz=datetime.timezone.utc` explicitly.

**Trade-off:** Behaviour varies by container/host timezone. DST transitions may shift
schedules by ±1 hour. Acceptable for day-aligned polling.

### 5. Testability — Pure functions

- `_seconds_until(target, tz=)` — testable without mocking datetime
- `CronSchedule.next_fire_after(dt)` — pure method, testable with known inputs
- `ctx.sleep()` shutdown awareness is already tested independently

## Decision Drivers

- **No new dependencies** — built-in Quartz-compatible parser avoids adding `croniter`
  to the dep tree
- **Declarative for common case** — `schedule=` on `@app.telemetry` is clean and
  validates eagerly
- **Composable for advanced case** — `ctx.sleep_until()` works with `@app.device` loops
- **Mutual exclusivity** — `schedule=` and `interval=` cannot be combined, preventing
  confusion
- **Local timezone default** — matches user mental model for home automation

## Considered Options

### Cron parser

| Criterion         | croniter (external) | cronsim (external) | Built-in parser |
| ----------------- | ------------------- | ------------------ | --------------- |
| Dep count         | 2                   | 2                  | 5               |
| Feature coverage  | 5                   | 4                  | 5               |
| Maintenance       | 3                   | 3                  | 3               |
| IoT suitability   | 3                   | 3                  | 5               |

_Scale: 1 (poor) to 5 (excellent)_

### `schedule=` vs `ctx.sleep_until()` only

| Criterion             | schedule= only | sleep_until() only | Both |
| --------------------- | -------------- | ------------------ | ---- |
| Declarative UX        | 5              | 2                  | 5    |
| @app.device support   | 1              | 5                  | 5    |
| Implementation cost   | 4              | 5                  | 3    |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Day-aligned polling with minimal user code — caldates2mqtt uses a one-liner
- No new external dependencies
- Full Quartz cron syntax (seconds, `L`, `W`, `#`, named days/months)
- `schedule=` validates eagerly at registration time
- Both `@app.telemetry` and `@app.device` patterns supported
- Composable: `ctx.sleep_until()` works for custom patterns beyond cron

### Negative

- Built-in cron parser is more code to maintain than using croniter
- Local timezone default adds container-configuration dependency
- DST transitions may shift scheduled times by ±1 hour

_2026-04-06_
