---
status: Accepted
date: 2026-10-02
impact: moderate
tags: [telemetry, error-handling, health, logging]
---

# ADR-082: Persistent-error reminders and a recovery log for telemetry

## Status

Accepted **Date:** 2026-10-02

## Context

ADR-011 de-duplicates telemetry errors by type: the first error of a type is logged at ERROR and published to `{prefix}/error` and `{prefix}/{device}/error`; every repeat of the same type is silent until a successful cycle. In the three airthings2mqtt outages behind epic cos-4mv5 (proposal item P-3) a slow poller failed with the same exception for 11-38 h. The logs and the error topic showed one line at the onset and then nothing, so an operator reading either one hours later could not tell a long outage from a single blip, and the recovery line `Telemetry 'x' recovered` did not say how long the outage had lasted.

ADR-080 already counts `consecutive_failures` per telemetry entity in `HealthReporter` and reserves a `last_error` heartbeat field for this item; ADR-081 leaves the recovery details to it. The counter, a monotonic clock and the heartbeat all live in `HealthReporter`, which every telemetry error and success path already calls.

## Decision

Use a per-entity failure streak in `HealthReporter`, with a reminder schedule and an app-wide `App(error_reminder_interval=3600.0)`, for persistent telemetry errors because it keeps ADR-011's onset de-duplication while making a long outage visible in the log, on the error topic and in the heartbeat.

- **Streak.** `record_failure` starts a streak at the first failed cycle (monotonic start plus wall-clock `first_seen`) and counts each further failed cycle; `record_success` ends it and returns its count and duration. A missing MQTT connection is not a failure (ADR-011 amendment, cos-wjil) and does not count.
- **Onset.** Unchanged: the first error of each type is logged at ERROR and published. Its payload now carries `details.count` and `details.first_seen`.
- **Reminders.** A repeat of the same type is due a reminder at the 2nd, 4th, 8th, ... failure while the streak is younger than `error_reminder_interval`, then at the first failure in each further interval since the streak began. A reminder logs one WARNING (`Telemetry 'x' still failing after 2h00m00s (25 consecutive failures): ...`) and republishes the error with the same `details`; the publisher's own WARNING omits the traceback. A poll gap longer than the interval reminds once, never as a burst.
- **`None`.** `error_reminder_interval=None` disables reminders; only the onset and the recovery are logged.
- **Recovery.** The first successful cycle logs `Telemetry 'x' recovered after 2h10m00s (26 failed cycles)` at INFO.
- **Heartbeat.** Telemetry entries gain `last_error` (exception class name) and `failing_since` (ISO time of the first failure), both `null` while the entity is healthy.

The streak lives in `HealthReporter` rather than in the runner's loop, so it survives a task re-created by the supervisor (ADR-081) and is shared with the heartbeat.

```python
app = App("airthings2mqtt", error_reminder_interval=3600.0)  # default
app = App("quiet2mqtt", error_reminder_interval=None)  # onset + recovery only
```

## Decision Drivers

- An outage of many hours must be visible to someone reading the log or the error topic hours after it began
- ADR-011's protection against error floods from a persistently broken sensor must hold for fast pollers
- The schedule must be deterministic so it can be tested on a FakeClock replay of the incident timeline
- Reuse ADR-080's failure counter and heartbeat instead of a second bookkeeping place
- One app-wide knob; no per-registration surface until a use case needs it

## Considered Options

### Option 1: Doubling then interval reminders in HealthReporter (chosen)

Remind at failures 2, 4, 8, ... during the first interval, then once per interval of the streak; streak state kept with the freshness record.

- *Advantages:* A short outage is visible within a few cycles; a long one at a fixed, bounded rate; Rate is independent of poll interval after the first interval, so fast pollers cannot flood; Survives supervisor task re-creation and feeds the heartbeat directly
- *Disadvantages:* Changes the ADR-011 behaviour that a repeated error is never republished; Adds two heartbeat keys that consumers asserting an exact shape must accept

### Option 2: Fixed interval reminders only

Republish a persisting error once per error_reminder_interval, with no doubling phase.

- *Advantages:* Simplest schedule; Bounded rate
- *Disadvantages:* With the default hour, a two-cycle blip and a 50-minute outage look identical to a reader; Slow pollers get no early signal that an error persists

### Option 3: Keep ADR-011 de-duplication; rely on the heartbeat

Publish nothing new; expose consecutive_failures and last_error in the heartbeat only.

- *Advantages:* No change to log or error-topic volume; No new App parameter
- *Disadvantages:* Log readers and error-topic subscribers still see one line for a 38 h outage; Recovery line still has no duration

## Decision Matrix

| Criterion | Doubling then interval reminders in HealthReporter | Fixed interval reminders only | Keep ADR-011 de-duplication; rely on the heartbeat |
| --- | --- | --- | --- |
| Visibility of a long outage | 5 | 4 | 2 |
| Flood protection for fast pollers | 4 | 5 | 5 |
| Early signal that an error persists | 5 | 2 | 1 |
| Implementation and API surface | 3 | 4 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- An operator reading the log or the error topic at any point in a long outage sees it is still going, with its count and start
- The recovery line states the outage duration and failed-cycle count
- The heartbeat shows which error an entity is failing with and since when, next to ADR-080's consecutive_failures
- error_reminder_interval=None restores the old onset-only behaviour exactly, plus the richer recovery line

### Negative

- Error-topic subscribers receive repeat payloads for one error type; consumers that treated every payload as a new incident must read details.count
- Telemetry heartbeat entries gain last_error and failing_since keys
- A streak counts every failed cycle, including a publish or reactor failure in an otherwise successful poll, the same as ADR-080's counter
- Reminders cover telemetry only; device, command and stream errors keep their existing reporting

_2026-10-02_
