---
status: Accepted
date: 2026-08-08
impact: high
tags: [di, architecture]
---

# ADR-053: Semantics of `T | None` optional dependency injection

## Status

Accepted **Date:** 2026-08-08

## Context

ADR-046 (Typed Handler Contract Validation) established the `Depends()`, `Payload()`, and `Topic()` binding marker family and the DI-validation-must-be-loud philosophy: every unresolvable dependency must fail at registration time, not silently at runtime. ADR-006 (Hexagonal Architecture) governs adapter resolution: adapters are concrete provider instances registered by type. The framework already uses `Annotated[T | None, Payload()]` as the idiomatic way to declare an optional typed payload — this is the key precedent the present decision builds on.

Today in `packages/src/cosalette/_injection.py`, an injected handler parameter must resolve to a concrete provider type. Two consequences follow:

- `T | None` (e.g. `store: DeviceStore | None`) is **rejected at registration** because a union has `get_origin(...) is types.UnionType` and fails the `isinstance(annotation, type)` check in `_resolve_annotation`.
- `param.default` is **ignored entirely** — the injection plan is `(name, type)` pairs only.

Net effect: there is no way to express "inject this dependency if a provider is registered, otherwise leave it unset." Users who need a genuinely optional dependency must currently hand-register a null-object adapter — unnecessary boilerplate that this ADR resolves.

`T | None` has several conflicting plausible meanings in a DI context; picking one silently is exactly how DI frameworks acquire surprising behaviour. This ADR answers four questions explicitly:

1. **Optionality opt-in vs. inferred** — is optionality inferred from `T | None` syntax, or does it require an explicit opt-in marker?
2. **`param.default` participation** — does the default value participate in resolution, and what happens when there is no default?
3. **Standalone vs. composable** — may the optionality marker be combined with `Depends()`, `Payload()`, or `Topic()`?
4. **Semantic disambiguation** — is "no provider registered" the same as "provider registered but resolved to None", and does `T | None` on a handler parameter mean the same as on a provider return type?

## Decision

Adopt an explicit `Optional()` binding marker for optional dependency injection — `Annotated[T | None, Optional()]` (or `Annotated[T, Optional()]`) resolves the provider if registered and otherwise falls back to the parameter default (implicitly `None`) — because it fills the optional-injection gap while preserving the loud-failure guarantee that DI validation exists to provide, and it stays consistent with the existing `Depends`/`Payload`/`Topic` marker family.

**Sub-decision 1 — Optionality is opt-in.** Bare `T | None` on an injected parameter remains rejected at registration, now with an improved diagnostic that redirects to `Annotated[T, Optional()]` (for optional DI) or `Annotated[T, Payload()]` (for payloads). Optionality is never inferred from type syntax alone.

**Sub-decision 2 — `param.default` participates.** On an `Optional()` parameter, when no provider resolves, the framework injects the explicit default if one is present; if there is no default, `None` is injected implicitly. There is no registration error for a missing default on an `Optional()` parameter.

**Sub-decision 3 — `Optional()` is standalone.** `Optional()` may not be combined with `Depends()`, `Payload()`, or `Topic()` on the same parameter. Multiple binding markers on one parameter is a registration `TypeError`.

**Sub-decision 4 — "No provider" vs. "resolved to None" are distinct.** Providers in the resolution map are always concrete instances (never `None`), so a plain-type `Optional()` parameter only ever yields `None` from the no-provider fallback. A `Depends(callable)` whose callable returns `None` injects that `None` as-is — that is the callable's explicit choice and is unaffected by this ADR. `T | None` on a provider or `Depends` return type retains its ordinary Python meaning; `T | None` on a handler parameter only carries optional-injection semantics when combined with the `Optional()` marker.

```python
from typing import Annotated
from cosalette import App
from cosalette.di import Optional
from myapp.stores import DeviceStore

app = App("demo")


@app.command("update_setpoint")
async def handle_update(
    store: Annotated[DeviceStore | None, Optional()],          # inject if registered, else None
    store_with_default: Annotated[DeviceStore | None, Optional()] = DeviceStore.null(),
) -> None:
    if store is None:
        return  # adapter not registered in this deployment
    ...


# REJECTED — bare union without Optional() marker:
# async def bad_handler(store: DeviceStore | None) -> None: ...
# Registration error: use Annotated[DeviceStore | None, Optional()] for optional DI
```

## Decision Drivers

- Preserve the loud-failure guarantee that ADR-046-era DI validation exists to provide: a registration typo (e.g. misspelled adapter type) must not silently become None and cause an AttributeError deep in the handler at runtime.
- Consistency with the established marker family (Depends/Payload/Topic) and the existing `Annotated[T | None, Payload()]` optional-payload precedent: optionality is always expressed via a marker, never inferred from type syntax.
- Optionality should be explicit and greppable rather than inferred from punctuation (PEP 20: explicit is better than implicit); `grep 'Optional('` surfaces all optional-injection sites immediately.
- Type-checker honesty: the declared type must match the possible runtime value; `Annotated[T | None, Optional()]` is honest because the handler genuinely may receive `None`.
- Minimise surprising silent behaviour in dependency resolution: the same `T | None` annotation should not behave differently depending on whether a default is present.
- Keep public API surface growth justified by the capability it unlocks: `Optional()` fills a real gap (optional adapters) and the cost is proportionate to the benefit.

## Considered Options

### Option 1: Explicit Optional() marker (chosen)

Add a new public binding marker `Optional()` as a sibling of `Depends()` in `cosalette/di.py`. Usage: `Annotated[T | None, Optional()]` (or `Annotated[T, Optional()]`). The framework strips `| None` from the inner type, resolves `T` against the providers map, and injects the resolved instance if found; otherwise it injects `param.default` if present, else `None` implicitly. Bare `T | None` without a marker remains rejected but now emits an improved diagnostic pointing to `Annotated[T, Optional()]`. `Optional()` is standalone and may not be combined with `Depends()`, `Payload()`, or `Topic()`.

- *Advantages:* Preserves the loud-failure guarantee from ADR-046: a typo'd provider registration still errors loudly instead of silently becoming None.; Consistent with the existing Depends/Payload/Topic marker family and the `Annotated[T | None, Payload()]` optional-payload precedent already in use.; Optionality is opt-in and greppable (`grep 'Optional('` finds all optional-injection sites across the codebase).; Type-checker honest: the inner type is genuinely `T | None`, matching the possible runtime value.
- *Disadvantages:* Adds one more public marker to learn, document, test, and maintain.; More verbose than a bare union at the call site.; Marker-validation logic must be extended to recognise `Optional()` and enforce the standalone constraint.

### Option 2: Infer optionality from union, inject None

Make bare `T | None = None` legal without any marker. When no provider is registered for `T`, the framework injects `None`. The default value is used as the fallback if present; otherwise `None` is used implicitly.

- *Advantages:* Zero new API surface; no new marker to document or test.; Reads the way most Python developers expect: a union with None means the value may be absent.; Least verbose at the call site.
- *Disadvantages:* Reintroduces the exact silent-failure mode that ADR-046 DI validation exists to prevent: a misspelled adapter registration silently yields None causing an AttributeError deep in the handler at runtime.; Cannot distinguish 'operator forgot to register the adapter' from 'intentionally optional dependency'.; Diverges from the established `Annotated[T | None, Payload()]` marker-governed precedent, creating an inconsistent API surface.

### Option 3: Infer optionality from union, require explicit default

Like Option B, but a default value is mandatory; the absence of a default on a `T | None` parameter is a registration error. The framework falls back only to the explicit default, never injects `None` implicitly.

- *Advantages:* The fallback value is always visible at the call site — no implicit None.; No new marker; optionality is expressed through type syntax plus a required default.; Requiring an explicit default is a mild forcing function toward intentionality.
- *Disadvantages:* Infers optionality from type syntax, so the silent-typo-to-fallback failure mode remains: a misspelled adapter registration causes the default to be used instead of a loud error.; `T | None` acquires different semantics depending on whether a default is present, making the annotation meaning context-dependent.; Handler-vs-provider `T | None` divergence is unaddressed: the annotation means different things in different positions.

### Option 4: Keep rejecting, improve the message only

Retain the current behaviour of rejecting `T | None` at registration and improve the diagnostic to clearly explain why the union is rejected and how to work around it (e.g. hand-register a null-object adapter). No new API is introduced.

- *Advantages:* Cheapest implementation: no new surface, no new validation logic, fully reversible.; Zero regression risk: existing behaviour is unchanged.; Leaves the optional-injection decision open for a future ADR with more implementation experience.
- *Disadvantages:* Leaves the 'inject if available' capability gap unfilled; users must hand-register a null-object adapter for every optional dependency.; Does not answer the four semantic questions, deferring the problem rather than resolving it.

## Decision Matrix

| Criterion | Explicit Optional() marker | Infer optionality from union, inject None | Infer optionality from union, require explicit default | Keep rejecting, improve the message only |
| --- | --- | --- | --- | --- |
| Loud on registration typo | 5 | 2 | 2 | 5 |
| No new API surface | 2 | 5 | 5 | 5 |
| Greppable / explicit optionality | 5 | 2 | 2 | 1 |
| Consistency with existing marker precedent | 5 | 2 | 2 | 3 |
| Type-checker honesty | 5 | 5 | 5 | 2 |
| Fills the capability gap | 5 | 5 | 5 | 2 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Fills the 'inject if available' capability gap without weakening DI validation: handlers can declare optional adapter dependencies, and registration typos still fail loudly.
- Optional dependencies become greppable and explicit: `grep 'Optional('` surfaces every optional-injection site across the codebase.
- Reuses the established marker family (Depends/Payload/Topic/Optional) — no new mental model is required for users already familiar with Annotated-based DI.
- Type-checkers see the honest `T | None` declared type, enabling correct static analysis of handler parameters that may receive `None`.

### Negative

- Adds one more public marker (`Optional()`) to learn, document, test, and maintain; the public API surface grows by one binding marker.
- More verbose than a bare union at the call site: `Annotated[T | None, Optional()]` versus `T | None = None`.
- The marker-validation path must be extended to recognise `Optional()` and enforce the standalone constraint; the implementation cost is tracked in beads task cos-v1dj.7.

_2026-08-08_
