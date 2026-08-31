---
status: Superseded by ADR-064
date: 2026-04-18
impact: moderate
tags: [telemetry, mqtt, di, devices]
---

# ADR-036: Triggerable Telemetry

## Status

Superseded by ADR-064 **Date:** 2026-04-18

**Superseded by:** [ADR-064](ADR-064-local-in-process-trigger-source-for-triggerable-telemetry.md) — widens `triggerable=` to a string-enum source declaration and adds `EntityNotifier`.

## Context

Cosalette telemetry devices run on fixed intervals or cron schedules. Several downstream apps (caldates2mqtt, gas2mqtt, airthings2mqtt) need both periodic polling AND on-demand refresh via MQTT command. Today this forces using `@app.device` with manual `ctx.commands()` loops, duplicating 60–80 lines of retry, publish, and scheduling logic that `@app.telemetry` already provides. The framework should support triggerable telemetry natively so handlers keep all telemetry benefits while accepting external triggers. Additionally, some trigger scenarios benefit from passing payload data (e.g. "refresh next 3 days only"), but this must not add complexity for the common "just re-run" case.

## Decision

Add a `triggerable=True` flag to `@app.telemetry` that subscribes the device to `{prefix}/{device}/set` for on-demand MQTT-triggered execution. Additionally, provide a `TriggerPayload` injectable dataclass that handlers opt into via dependency injection to access trigger context and payload data. The trigger reuses the existing `TopicRouter` infrastructure and command topic pattern from ADR-002/ADR-025. Coalescing limits pending triggers to at most one. Scheduled and triggered runs use the identical execution pipeline (DI, retry, publish, persist).

```python
from cosalette import TriggerPayload

# Simple: just re-run on trigger (payload ignored)
@app.telemetry("sensor", interval=60, triggerable=True)
async def read_sensor(adapter: SensorPort) -> dict[str, object]:
    return {"temperature": await adapter.read()}

# Advanced: opt-in to payload via DI
@app.telemetry("garbage", interval=7200, triggerable=True)
async def calendar(
    reader: CalDavPort,
    cal: CalendarConfig,
    trigger: TriggerPayload,
) -> dict[str, object]:
    days = trigger.get("days", cal.days) if trigger.is_triggered else cal.days
    return {"events": await reader.read_events(days=days)}
```

## Decision Drivers

- Eliminate 60–80 lines of manual loop/retry/publish code per triggerable device
- Maintain zero-cost simplicity for the 90% "just re-run" case
- Follow existing DI conventions for opt-in payload access
- Reuse existing MQTT topic patterns and TopicRouter infrastructure
- Prevent thundering herd from chatty MQTT clients via coalescing

## Considered Options

### Option 1: triggerable flag with TriggerPayload injectable (chosen)

Add `triggerable=True` boolean flag to `@app.telemetry`. Framework subscribes to `{prefix}/{device}/set`. Trigger fires immediate out-of-cycle handler execution through the same pipeline. `TriggerPayload` frozen dataclass is opt-in via DI — handlers that don't declare it get zero-cost triggering; handlers that declare it receive trigger context (is_triggered, raw payload, parsed JSON data).

- *Advantages:* Single flag for simple case; Payload access via existing DI pattern; No new decorator or handler signature change required; Coalescing prevents thundering herd; Reuses TopicRouter
- *Disadvantages:* One new public type (TriggerPayload); Handler may need branching logic for triggered vs scheduled behavior

### Option 2: on_trigger callback decorator

Add `@handler.on_trigger` decorator for a separate callback that handles trigger events. The trigger handler receives the MQTT payload and can produce different results than the scheduled handler.

- *Advantages:* Clean separation of scheduled vs triggered logic; Full payload control; Trigger handler can differ from periodic handler
- *Disadvantages:* Two decorators and two functions per device; Handler function must become a stateful wrapper object; More API surface and framework complexity (~100+ lines); Rarely needed for the common "just re-run" case

### Option 3: payload-ignoring triggerable flag only

Add `triggerable=True` with no payload support at all. Trigger always means "re-run the handler exactly as scheduled, ignoring any MQTT payload."

- *Advantages:* Absolute minimum complexity; Zero new types; Covers "refresh now" use case
- *Disadvantages:* No way to optimize triggered reads (e.g. fetch fewer calendar days); Requires @app.device fallback for payload-aware scenarios; Limits future extensibility

## Decision Matrix

| Criterion | triggerable flag with TriggerPayload injectable | on_trigger callback decorator | payload-ignoring triggerable flag only |
| --- | --- | --- | --- |
| API simplicity | 4 | 2 | 5 |
| Payload flexibility | 4 | 5 | 1 |
| Framework implementation cost | 4 | 2 | 5 |
| DI convention alignment | 5 | 2 | 5 |
| Downstream app coverage | 5 | 5 | 3 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Downstream apps can convert @app.device handlers to @app.telemetry with triggerable=True, eliminating manual loop/retry/publish boilerplate
- TriggerPayload injectable enables opt-in payload access without adding complexity to the simple case
- Coalescing prevents thundering herd from rapid MQTT triggers
- Reuses existing TopicRouter and topic conventions — no new MQTT patterns

### Negative

- One new public type (TriggerPayload) in the framework API
- Triggerable telemetry devices occupy the {prefix}/{device}/set topic, preventing simultaneous @app.command registration for the same device name (already enforced by existing name uniqueness rules)
- Handlers needing different behavior for triggered vs scheduled runs must branch on trigger.is_triggered

_2026-04-18_
