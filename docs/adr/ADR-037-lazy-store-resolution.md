---
status: Accepted
date: 2026-04-20
impact: low
tags: [persistence, lifecycle, di]
---

# ADR-037: Lazy Store Resolution

## Status

Accepted **Date:** 2026-04-20

## Context

`App(store=...)` only accepted a concrete `Store` instance at construction time. This forced adopters to resolve the store path eagerly — before settings were available — which conflicted with the `@app.on_configure` lifecycle designed to eliminate eager settings access.

For example, gas2mqtt needed a workaround function `resolve_store_path()` that duplicated path logic between a standalone resolver and the settings model. Any application whose store path depends on a settings field faced the same ergonomic issue.

## Decision

Accept `Callable[..., Store]` in addition to `Store` for the `store=` parameter. The factory is invoked during bootstrap — after settings and adapter resolution but before configure hooks — with signature-based dependency injection for the resolved settings and adapter instances.

```python
def make_store(settings: Gas2MqttSettings) -> Store:
    if settings.state_file:
        return JsonFileStore(settings.state_file)
    return NullStore()

app = App(name="gas2mqtt", store=make_store, settings_class=Gas2MqttSettings)
```

## Decision Drivers

- Minimal API surface change — `store=` stays a single parameter
- Backward compatible — existing concrete `Store` instances still work unchanged
- Follows the established `init=` factory pattern that adopters already understand
- Factory receives DI (settings, adapters) so it can use any settings field

## Considered Options

### Option 1: Callable store factory (chosen)

Accept `Callable[..., Store]` in addition to `Store` for the `store=` parameter. The factory is called during bootstrap with DI for resolved settings.

- *Advantages:* Minimal API change — `store=` stays a single parameter with a wider type; Factory receives dependency-injected settings and adapters; Fully backward compatible — concrete `Store` instances still work; No new lifecycle phase needed; Follows existing `init=` factory pattern familiar to adopters
- *Disadvantages:* Slightly more complex `__init__` logic (`isinstance` check before `callable` check); Factory must be synchronous (async factories not supported)

### Option 2: Store setter on App

Expose `app.store = ...` as a property setter usable from `on_configure` hooks.

- *Advantages:* Simple property assignment API
- *Disadvantages:* Mutation after construction is error-prone and hard to reason about; Ordering depends on lifecycle phase timing; Breaks the 'composition root declares, framework runs' principle

### Option 3: Store as DI-injectable in on_configure

Add `Store` to the `on_configure` injection providers with a default `NullStore` that can be replaced.

- *Advantages:* Uses existing `on_configure` mechanism
- *Disadvantages:* Semantic mismatch — handler receives a store it is supposed to set, not use; Requires a swap/replace mechanism that does not exist in the DI system

## Consequences

### Positive

- gas2mqtt and future applications can eliminate eager store-path resolution workarounds
- Store path can depend on any settings field without duplicating resolution logic
- Zero breaking changes to existing applications

### Negative

- `App.__init__` has slightly more complex detection logic (`isinstance` check before `callable` check)
- Store factory must be synchronous (store creation should not require async I/O anyway)

_2026-04-20_
