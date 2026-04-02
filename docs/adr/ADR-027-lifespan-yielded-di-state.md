# ADR-027: Lifespan-Yielded Injectable State

## Status

Accepted **Date:** 2026-04-02

## Context

cosalette device handlers receive dependencies via type-based DI — adapters, Settings,
DeviceContext, etc. However, shared mutable state created during application startup
has no DI-friendly injection path. Early adopter projects use a module-level
singleton (`_state: SharedState | None = None`) set during the lifespan hook because
the framework provides no mechanism to yield a value from the lifespan into the DI
system. This module-global pattern is untestable and fragile.

FastAPI solved the same problem by allowing lifespan context managers to yield a value
that becomes available to route handlers via `request.state`. cosalette can adopt a
similar pattern: the lifespan yields a value, the framework captures it, registers it
in DI by its runtime type, and device handlers declare the type as a parameter to
receive it.

## Decision

1. **Capture the value yielded by the lifespan context manager** and register it in
   the DI system using `type(yielded_value)` as the key.
2. **Change the `LifespanFunc` type alias** from
   `Callable[[AppContext], AbstractAsyncContextManager[None]]` to
   `Callable[[AppContext], AbstractAsyncContextManager[Any]]`.
3. **Raise `RuntimeError` at startup** if the yielded type conflicts with an existing
   DI registration (adapter port, Settings, etc.).
4. **Lifespan-yielded state is not available in `on_configure` hooks** — the lifespan
   runs after the configure phase. This is a documented constraint.
5. **Remove the yielded type from DI on lifespan teardown** to prevent stale
   references.

Lifespan functions that yield `None` (the existing pattern) continue to work unchanged.

## Decision Drivers

- Eliminating module-level singletons for shared state in adopter apps
- Consistency with the FastAPI lifespan pattern familiar to Python developers
- Backward compatibility — existing lifespans must not break
- Simplicity — `type(yielded_value)` requires no explicit configuration

## Considered Options

### DI registration key

| Option | Mechanism | Pros | Cons |
| ------ | --------- | ---- | ---- |
| **A: `type(yielded_value)` (chosen)** | Runtime type inspection | Simple, always works, FastAPI precedent | Cannot inject by Protocol/ABC |
| B: Return annotation | Inspect `AsyncIterator[T]` | Supports abstract types | Fragile with generics, forward refs |
| C: Explicit parameter | `App(lifespan_type=...)` | No magic | Verbose boilerplate |

### Type alias change

| Option | Impact | Chosen |
| ------ | ------ | ------ |
| **A: Change existing alias** | Runtime-compatible, minor type-checker noise | **Yes** |
| B: Add separate alias | Two aliases to maintain | No |
| C: Union in constructor | Complex type hints | No |

### Type conflict handling

| Option | Behavior | Chosen |
| ------ | -------- | ------ |
| **A: RuntimeError** | Clear, predictable, fail-fast | **Yes** |
| B: Lifespan wins | Silent override — surprising | No |
| C: Adapter wins | Lifespan value silently lost | No |

## Consequences

### Positive

- Adopter apps can replace module-level singletons with lifespan-yielded DI state
- Device handlers receive shared state through the same type-based DI used for
  adapters and Settings — no new API concepts
- Backward compatible — `yield` without a value (or `yield None`) triggers no
  registration
- Single lifespan per App keeps the model simple

### Negative

- Cannot inject by Protocol or ABC type — only concrete runtime type. Users who
  need abstract injection can wrap in a concrete class
- Lifespan-yielded state is not available in `on_configure` hooks (lifecycle ordering
  constraint)
- Single yielded type per App — multiple shared objects require a container class

_2026-04-02_
