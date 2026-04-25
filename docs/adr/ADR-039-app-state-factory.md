---
status: Accepted
date: 2026-04-25
impact: moderate
tags: [di, lifecycle, state]
---

# ADR-039: @app.state Shared-State Factory

## Status

Accepted **Date:** 2026-04-25

## Context

cosalette device handlers receive dependencies via type-based DI. Shared mutable
state constructed at bootstrap (e.g. a sensor registry built from settings) has
no DI-friendly path without the `lifespan=` trick added in ADR-027. That pattern
requires an async context manager function, a `lifespan=` argument on `App()`, a
`cast()` call for settings, and a re-export shim module for backward compatibility.
The ceremony outweighs the logic.

ADR-027 solved a real problem but stopped short of a first-class API. The pattern
it introduced (yield the state from the lifespan CM) is indirect: the lifespan's
primary responsibility becomes state construction instead of side-effect startup.

## Decision

Add `@app.state` as a first-class decorator on `App`. A factory decorated with
`@app.state` is called once at bootstrap; its return value is registered in the DI
container by its return type and injected into any handler declaring that type.

Four factory forms are supported, detected by return annotation at registration time:

| Form | Teardown |
|---|---|
| `def f(...) -> T` | None (simple return) |
| `def f(...) -> ContextManager[T]` | `__exit__` on shutdown |
| `async def f(...) -> AsyncIterator[T]` | generator finalized on shutdown |
| `async def f(...) -> AsyncContextManager[T]` | `__aexit__` on shutdown |

**Bootstrap order:**
1. Settings resolved
2. All `@app.state` factories called, in registration order
3. Lifecycle adapters entered
4. `lifespan=` context manager entered
5. Devices started

**Teardown order (reverse):**
1. Devices cancelled
2. `lifespan=` exited
3. Lifecycle adapters exited
4. `@app.state` generators/CMs exited in reverse registration order

**DI key:** The unwrapped return type `T` (stripping `Iterator[T]`, `ContextManager[T]`, etc.).

**Duplicate detection:** Two factories with the same return type raise `ValueError` at registration.

**Settings injection:** If the factory's first parameter is annotated with `Settings` or a
subclass, the framework passes the resolved settings instance narrowed to that type. No parameter is also valid (zero-arg factory).

**Testing:** `AppHarness.override_state(type_, instance)` bypasses the factory in tests.

## Consequences

- Apps using `lifespan=` purely for DI state can migrate to `@app.state`.
- `lifespan=` continues to work for side-effect startup (HTTP servers, etc.).
- ADR-027 (lifespan-yielded DI state) is superseded by this ADR for state-object
  use cases. `lifespan=` remains supported for `None`-yield side-effect patterns.

## Supersedes

ADR-027 (for state-object use cases; lifespan= remains valid for side-effect startup).
