---
status: Accepted
date: 2026-10-02
impact: high
tags: [telemetry, health, mqtt, lifecycle, error-handling]
---

# ADR-080: Telemetry freshness tracking and a stale device status

## Status

Accepted **Date:** 2026-10-02 | Amended **Date:** 2026-10-02 | Amended **Date:** 2026-10-03

## Context

Through 0.10.6 nothing in the framework records when a telemetry entity last produced data. ADR-012's heartbeat carries one `status` string per device (`ok`, `error`, `unavailable`, `circuit_open`) and no timestamps; ADR-077's automatic availability fires only from the runner's terminal-failure path and only for exception types matched by `unavailable_on`. A downstream BLE app (`airthings2mqtt`, 25 min interval) stayed `online` with a day-old retained reading for 11 to 38 hours, three times in eight days: once because the polling task died, once because the adapter raised an exception type the app had classified as non-retryable. ADR-077's terminal-failure fix (cos-4mv5.1) closes the second case only for errors inside `unavailable_on`; a dead or hung task, a starved loop, or an error outside `unavailable_on` still leaves the entity `online` indefinitely.

The gap is not classification but supervision: a consumer needs `offline` within a bounded time after an entity stops producing good data, whatever the cause. The bound must be safe for slow pollers (25 min, where one missed poll is normal) and fast ones (1 s). Three questions from the proposal (cos-4mv5 §6) were decided by the maintainer on 2026-10-02: the derived bound uses a fixed, jitter-free backoff allowance so a unit test can pin it; `stale` outranks `error` in the heartbeat; and the feature ships in the next minor release.

## Decision

Track freshness per telemetry entity in `HealthReporter` and run one framework-owned watchdog that marks an entity `stale` and publishes retained `offline` through a dedicated `freshness` availability source once no fully successful cycle has completed for `stale_after` seconds, because a time-based backstop catches dead tasks, hung handlers and unclassified errors without depending on the app classifying anything correctly.

- **Freshness.** A cycle is fresh when the handler returned and the result was published, suppressed by its `PublishStrategy`, or was `None`, and reactors succeeded — the same point that already clears the `telemetry` source (ADR-077). Each fresh cycle records `last_success_at` and resets `consecutive_failures`; each terminal handler, publish or reactor failure increments it. A missing broker connection (`MqttNotConnectedError`) is a transport condition and is not counted.
- **`stale_after=` registration parameter** on `@app.telemetry`, `App.add_telemetry` and `Router.telemetry`: a positive float, a settings callable such as `setting_ref(...)`, or `None` to disable. Omitted, a named entity derives `2 × period + timeout × (retry + 1) + 60 s × retry`, where `period` is the interval or, for a cron schedule, the longest gap among its next 16 fire times, `timeout` is the resolved timeout (`None` counts as 0), and 60 s per retry is a fixed backoff allowance equal to the built-in strategies' default cap. No jitter enters the formula. **Root** entities derive `None`: like ADR-077's `unavailable_on` default, they publish to the flat `{prefix}/availability` and would otherwise down the whole app; an explicit value opts them in.
- **Watchdog.** One task, started alongside the heartbeat only when some entity has a `stale_after`, checks every `min(heartbeat_interval, 60 s, smallest stale_after)`. When `now - last_success` exceeds `stale_after` (measured from task start before the first success), it publishes `offline` via `publish_device_unavailable(..., source="freshness")` and logs one WARNING with the age and last error type, on the transition only.
- **Recovery.** The next fresh cycle clears the `freshness` source; per ADR-077's multi-source rule `online` is republished only when no other source still holds the entity offline.
- **Status vocabulary.** `stale` joins `ok`, `error`, `unavailable` and `circuit_open`. While the `freshness` source is active the heartbeat reports `stale` regardless of the underlying status, so it outranks `error`; P-3 will add `last_error` alongside rather than replacing it.
- **Heartbeat.** Every telemetry device entry gains `last_success_at` (ISO-8601 UTC, `null` before the first success) and `consecutive_failures`. Device, command and stream entries are unchanged.

```python
@app.telemetry("airthings", interval=1500, timeout=120, retry=3)  # derived: 3000 + 480 + 180 = 3660 s
async def airthings() -> dict[str, float]: ...

@app.telemetry("fast", interval=1, stale_after=30)       # explicit
@app.telemetry("legacy", interval=60, stale_after=None)  # opt out

# heartbeat {prefix}/status
# "devices": {"airthings": {"status": "stale",
#   "last_success_at": "2026-10-01T18:09:28+00:00", "consecutive_failures": 27}}
```

## Decision Drivers

- Consumers must see offline within a bounded time after data stops, whatever the cause (dead task, hang, unclassified error)
- Defaults must be safe for 25-minute and 1-second pollers alike, so the bound must scale with the registration
- The derived default must be deterministic so a unit test can pin it (maintainer decision, no jitter)
- Must compose with ADR-077 multi-source availability and its root-entity exclusion without changing their semantics
- Operators reading the heartbeat need to tell a stale entity from a failing one and see when data was last good
- Additive API: existing apps keep working, and stale_after=None restores the old behaviour exactly

## Considered Options

### Option 1: Rely on consumers' expiry settings

Keep the framework unchanged and document consumer-side expiry such as openHAB `expire` metadata or Home Assistant `expire_after`.

- *Advantages:* No framework change and no new status value; Consumers already offer the mechanism
- *Disadvantages:* Protects one consumer at a time; alerting, the heartbeat and other subscribers still see online; Every consumer must be configured per entity, and the bound must be kept in sync with the app's interval by hand; Does nothing for a dead task, which is exactly the incident that stayed invisible longest

### Option 2: MQTT 5 message expiry on the state topic

Publish state with a per-message expiry short enough that a stalled entity's retained value disappears from the broker (building on ADR-078).

- *Advantages:* Broker-enforced, so it works even if the app process hangs entirely; Already partially supported through ADR-078
- *Disadvantages:* Deletes the last known value, which consumers want to keep for display; Requires MQTT 5 on broker and client; MQTT 3.1.1 deployments get nothing; Availability stays online, so consumers see an empty but available entity; ADR-078's refresh ledger is built for broker hygiene, not second-level freshness

### Option 3: Framework freshness watchdog with a stale status and a freshness availability source (chosen)

Record last success per telemetry entity, derive a per-registration stale_after bound, and let one watchdog task publish offline through a dedicated freshness source and report stale in the heartbeat.

- *Advantages:* Catches every cause of missing data — dead task, hang, unclassified error, open circuit — without classification; Bound derived from the registration, so slow and fast pollers are both safe by default; Reuses ADR-077's per-source availability: freshness and telemetry offline marks cannot clear each other; Heartbeat gains last_success_at and consecutive_failures, the inputs P-3 and P-5 need; stale_after=None is an exact opt-out
- *Disadvantages:* New status value and new offline publishes without an exception: behaviour change that needs a changelog note; One more framework-owned background task; A derived bound can be loose for cron schedules with irregular gaps

### Option 4: Consecutive-failure threshold in the runner

Publish offline after N consecutive failed cycles (the deferred `unavailable_after=` idea from cos-4mv5.10), with no time-based check.

- *Advantages:* No new task; runs inside the existing runner loop; Easy to explain as a count
- *Disadvantages:* A dead or hung task never completes another cycle, so the count never advances — the incident-1 case stays invisible; Adds the hysteresis machinery ADR-077 deliberately avoided; Count-based bounds mean wildly different wall-clock delays for 1 s and 25 min pollers

## Decision Matrix

| Criterion | Rely on consumers' expiry settings | MQTT 5 message expiry on the state topic | Framework freshness watchdog with a stale status and a freshness availability source | Consecutive-failure threshold in the runner |
| --- | --- | --- | --- | --- |
| Catches dead or hung tasks | 3 | 4 | 5 | 1 |
| Keeps the last known value for display | 4 | 1 | 5 | 5 |
| Works for every consumer and the heartbeat | 1 | 2 | 5 | 4 |
| Safe default for slow and fast pollers | 2 | 2 | 5 | 2 |
| Composes with ADR-077 without semantic change | 5 | 4 | 5 | 3 |
| Implementation and runtime cost | 5 | 3 | 3 | 4 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Within stale_after of the last good cycle, a named telemetry entity goes offline whatever stopped it, closing the class of incident behind cos-4mv5
- The heartbeat distinguishes stale from error and shows when data was last good and how many cycles have failed
- Freshness and telemetry availability are separate ADR-077 sources, so neither recovery path can declare an entity online while the other still holds it offline
- The derived default is deterministic and pinned by a unit test
- stale_after=None restores pre-ADR behaviour exactly for any entity

### Negative

- Behaviour change: named telemetry entities can now go offline without raising, and 'stale' is a new status value consumers of the heartbeat may need to handle
- A long MQTT outage makes entities stale because state cannot be published; they recover on the first fresh cycle after reconnect
- Root entities do not get the automatic default and must pass stale_after explicitly, the same asymmetry ADR-077 accepted for unavailable_on
- Cron entities with irregular schedules derive a bound from the longest gap among the next 16 fire times, which can be loose or miss a rarer, longer gap
- The fixed 60 s per-retry backoff allowance can undercount an explicit backoff with long delays; such apps should pass stale_after explicitly

## Amendment (2026-10-02) — Corrective

**Rationale:** Review found that a strict stale boundary can defer the transition by an entire watchdog interval when the first check lands exactly on stale_after.

> **Justification for amendment (not supersession):** ADR-080 has not been released; this is a boundary-condition correction within its implementation and has no downstream migration impact, so supersession is not warranted.

!!! note "Editorial note (2026-10-02)"
    The watchdog transitions to stale when now - last_success is greater than or equal to stale_after. This preserves the documented bounded freshness guarantee when a scheduled check lands exactly on the boundary.

## Amendment (2026-10-02) — Corrective

**Rationale:** The fixed 60 s per-retry backoff allowance undercounts a configured backoff whose cap is longer, so an app using ExponentialBackoff(max_delay=300) or FixedBackoff(delay=120) with retries could be marked stale while still inside one legitimately slow cycle (cos-4mv5.14).

> **Justification for amendment (not supersession):** ADR-080 has not been released (0.11.0 is still pending), so no downstream app depends on the exact derived value. The change is confined to one resolution function, only ever widens the derived bound (the 60 s figure becomes a floor), and keeps the bound deterministic and jitter-free, so supersession is not warranted.

!!! note "Editorial note (2026-10-02)"
    The per-retry backoff allowance in the derived default is now `max(60 s, max_delay)`, where `max_delay` is the configured backoff's jitter-free cap: the `max_delay` of `ExponentialBackoff` and `LinearBackoff`, and the `delay` of `FixedBackoff`, all exposed as a read-only `max_delay` property. The formula becomes `2 × period + timeout × (retry + 1) + allowance × retry`. A custom `BackoffStrategy` may expose a finite numeric `max_delay` attribute to be honoured; one without it (or with a non-numeric, boolean or non-finite value) keeps the 60 s allowance. Jitter still does not enter the formula, so a unit test can pin the bound; the two-period slack absorbs it. Existing derived values never shrink: a built-in backoff with a cap at or below 60 s, and the default backoff, derive exactly the bound originally specified here.

### Additional Positive Consequences

- Apps that configure a long built-in backoff get a correct derived stale_after without passing it explicitly

### Additional Negative Consequences

- BackoffStrategy gains an optional, duck-typed max_delay attribute that custom strategies must know about to benefit

## Amendment (2026-10-03) — Corrective

**Rationale:** PR #489 review found that two-period slack does not always cover positive jitter: interval=10, retry=2 and FixedBackoff(120) can sleep 288 seconds, exceeding the previous 260-second bound.

> **Justification for amendment (not supersession):** The 0.11.0 freshness decision and max_delay properties have not been released. This correction is confined to the resolution helper and new backoff properties, only widens derived defaults, and has no downstream migration cost.

### Revised Decision

Derive stale_after as 2 × period + timeout × (retry + 1) + max(60 s, maximum sleep) × retry. The built-in strategies expose max_delay as their actual maximum sleep, including the deterministic upper bound of +20% jitter; constructor max_delay and FixedBackoff delay remain pre-jitter configuration. The default backoff therefore contributes 72 seconds per retry. A custom strategy's optional max_delay must bound its actual delay, including any jitter. Missing, boolean, non-numeric, non-finite or unrepresentable custom caps fall back to 60 seconds; configure stale_after explicitly if that fallback is insufficient. Reject an unrepresentable derived bound with a clear ValueError. A disabled timeout contributes zero and cannot bound unbounded handler execution.

!!! note "Editorial note (2026-10-03)"
    This correction replaces the 2026-10-02 amendment's exclusion of jitter and its claim that two-period slack absorbs jitter. The calculation uses a fixed maximum jitter factor, not a random sample, so it remains deterministic. Existing derived values never shrink; defaults using the 60-second pre-jitter cap now allow 72 seconds per retry.

### Additional Positive Consequences

- Legitimate retries with maximum positive jitter remain inside the derived bound when handler attempts are bounded by timeout.

### Additional Negative Consequences

- The larger deterministic allowance can delay stale detection; custom strategies remain responsible for supplying a truthful maximum sleep.
