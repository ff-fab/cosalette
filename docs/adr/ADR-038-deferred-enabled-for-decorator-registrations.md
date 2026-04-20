---
status: Accepted
date: 2026-04-20
impact: moderate
tags: [telemetry, lifecycle, configuration, devices]
---

# ADR-038: Deferred enabled= for Decorator Registrations

## Status

Accepted **Date:** 2026-04-20

## Context

The `@app.telemetry()`, `@app.device()`, and `@app.command()` decorators accept an `enabled=` parameter that controls whether a device is registered. Before this change the parameter only accepted a literal `bool`. When the enabled/disabled decision depends on runtime settings — for example `enabled=settings.enable_debug_device` — adopters cannot use the decorator form and must fall back to imperative `add_telemetry()` calls inside `@app.on_configure`.

This breaks the "declarative `main.py`" goal established in ADR-001: if even one device has a settings-dependent `enabled=`, all devices must move to `on_configure` for consistency, otherwise the module mixes module-level decorator registrations with imperative `on_configure` registrations.

The `interval=` parameter already supports `Callable[[Settings], float]` for deferred resolution via `resolve_intervals()` in `_wiring.py` (ADR-020). The `enabled=` parameter had no equivalent — it was consumed at decoration time and not stored in the registration dataclass.

The gas2mqtt early-adopter project surfaced this gap: a magnetometer with `enabled=settings.enable_debug_device` forced the entire `main.py` into `on_configure`-based registration for consistency.

## Decision

Extend `enabled=` on decorator forms to accept `EnabledSpec = bool | Callable[..., bool]`. When a callable is provided, the decision is deferred to the bootstrap phase — after settings resolution, alongside `resolve_intervals()` — via a new `resolve_enabled()` function in `_wiring.py`. The callable receives the resolved `Settings` instance (or a per-device config value in multi-device mode). Entries disabled by the callable are removed from the registry before MQTT wiring begins; entries enabled by the callable have deferred constraints (`persist=` requiring a store, `triggerable`/group exclusion) validated at that point. The imperative `add_telemetry()`, `add_device()`, and `add_command()` methods retain `enabled: bool` only — they run inside `on_configure` where settings are already available.

```python
@app.telemetry(
    "magnetometer",
    interval=lambda s: s.poll_interval,
    enabled=lambda s: s.enable_debug_device,  # deferred — resolved at bootstrap
)
async def magnetometer(mag: MagnetometerPort) -> dict[str, object]:
    reading = mag.read()
    return {"bx": reading.bx, "by": reading.by, "bz": reading.bz}
```

## Decision Drivers

- Preserve the fully-declarative main.py goal from ADR-001 — every device visible at module level
- Follow the established interval= callable precedent (ADR-020) for consistency and minimal API surface
- No on_configure boilerplate required for apps whose only settings dependency is enabled/disabled
- Backward compatible — literal bool still works identically, no migration required
- Deferred validation should only fire for devices that are actually enabled

## Considered Options

### Option 1: Callable EnabledSpec (Option A) (chosen)

Accept `bool | Callable[..., bool]` for `enabled=` on all three decorator forms. Store the callable in a new `enabled_spec` field on the registration dataclass. Resolve callables in `_wiring.resolve_enabled()` called during bootstrap after `resolve_intervals()`, mutating the registration lists in place (removing disabled entries). Defer validation of persist=/store and triggerable constraints until `resolve_enabled()` for callable-enabled registrations.

- *Advantages:* Exact parallel to the existing callable interval= pattern — minimal new concepts; Zero boilerplate: apps that only need settings-dependent enabled can stay fully declarative; Clean separation: resolution in _wiring.py keeps App class lean; Backward compatible: literal bool path unchanged
- *Disadvantages:* Registration exists in memory briefly before being pruned — minor overhead; Decorator validation flow is now conditional on whether enabled is callable or literal; Deferred validation produces bootstrap-time errors rather than decoration-time errors for callable-enabled devices

### Option 2: Setter / late-bind (Option B)

Keep `enabled=` as `bool` at decoration time. Add a separate `app.disable(name)` method or a `@app.on_configure` hook that can remove registrations. Adopters would configure conditional registration via `on_configure` but through a cleaner API.

- *Advantages:* No changes to registration dataclasses; Decoration-time validation is fully eager
- *Disadvantages:* Does not solve the core problem: main.py still splits registrations across module level and on_configure; Introduces a mutable app registry pattern that conflicts with the immutable-registration design; Still requires on_configure boilerplate

### Option 3: DI-injectable enabled flag (Option C)

Introduce a special `EnabledFlag` injectable type that the framework resolves before starting device tasks. Handlers would declare `enabled: EnabledFlag` as a parameter; the framework would skip starting the task if resolved to False.

- *Advantages:* Lazy — decisions made at task-start time, not bootstrap; Could handle dynamic enable/disable at runtime
- *Disadvantages:* Significantly more complex implementation; Introduces a new injectable type with special semantics; Does not integrate with the existing registration/wiring model; Runtime dynamic enable/disable is out of scope and would require task lifecycle management

## Decision Matrix

| Criterion | Callable EnabledSpec (Option A) | Setter / late-bind (Option B) | DI-injectable enabled flag (Option C) |
| --- | --- | --- | --- |
| Declarative main.py preserved | 5 | 2 | 3 |
| Consistency with existing patterns | 5 | 2 | 1 |
| Implementation simplicity | 4 | 3 | 1 |
| Backward compatibility | 5 | 4 | 5 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Fully declarative main.py — every device visible at module level with enabled=lambda alongside interval=lambda
- No on_configure needed for apps whose only settings dependency is enabled/disabled
- EnabledSpec mirrors IntervalSpec — one new concept, consistent mental model
- Deferred validation (persist=/store, triggerable/group) fires only for actually-enabled devices, giving accurate error messages
- No breaking changes — all existing code continues to work

### Negative

- Decorator validation is now split: structural checks (interval/schedule, group non-empty, retry) are always eager; constraint checks (persist/store, triggerable/group exclusion) are deferred when enabled is callable
- A device with callable enabled= briefly occupies a registration slot before bootstrap prunes it — negligible in practice
- The imperative add_*() methods intentionally do not support callable enabled= to keep their contract simple; this asymmetry must be documented

_2026-04-20_
