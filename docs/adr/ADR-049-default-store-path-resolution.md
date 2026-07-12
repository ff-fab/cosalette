---
status: Accepted
date: 2026-07-12
impact: moderate
tags: [persistence, lifecycle, architecture, mqtt, health]
---

# ADR-049: Default store path resolution

## Status

Accepted **Date:** 2026-07-12

## Context

cosalette publishes retained MQTT topics (`{app}/{entity}/state` and `{app}/{entity}/availability`) on apps' behalf and, since ADR-048, clears orphaned retained topics for removed entities on the first MQTT connect — but only when a `store=` backend is configured on `App()`.

Activating the cleanup invariant today requires every app author to:

1. Pick a store path and write a `resolve_store_path()` helper (30–40 lines).
2. Call `JsonFileStore(resolve_store_path())` in `App(store=...)`.
3. Add a `<APP>_STORE_PATH` env override in `docker-compose.yml`.
4. Inject `MemoryStore()` in integration-test fixtures.

This is four to five files touched per app for a **framework-level invariant** — retained topics must match current entities — that apps have no sensible way to opt out of. Of eight apps in this monorepo, five have no `store=` wiring. A production smoke test on 2026-07-11 confirmed the risk: removing `contact_birthdays` from `caldates2mqtt`'s calendar config left `caldates2mqtt/birthday/availability` retained on the broker indefinitely.

The boilerplate across the three apps that do wire a store (`gas2mqtt`, `jeelink2mqtt`, `vito2mqtt`) is already diverging in env-var naming and fallback paths — it will worsen as more apps adopt the pattern.

Source: `tmp/framework-enhancement-proposal.md`. Relates to ADR-015 (persistence), ADR-037 (lazy store resolution), ADR-048 (orphaned retained-topic cleanup). Beads epic: cos-9ii.

## Decision

When `store=` is **omitted** from `App(...)`, auto-create a `JsonFileStore` with a path resolved from the app name using the following precedence:

1. `<NAME>_STORE_PATH` environment variable — name upper-cased, hyphens and spaces replaced by underscores (e.g. `CALDATES2MQTT_STORE_PATH`).
2. `$XDG_STATE_HOME/<name>/store.json`.
3. `~/.local/state/<name>/store.json` (the XDG default).

`store=None` is an explicit opt-out (no store). Explicit `Store` instances and `Callable[..., Store]` factories are used as-is. The distinction between "omitted" and `None` reuses the existing private `_UNSET` enum sentinel, keeping the public parameter type `Store | Callable[..., Store] | None`. Resolution is eager and side-effect-free in `App.__init__` — `JsonFileStore` creates parent directories only on first save.

**Sub-decision: internal sentinel over exported constant.** Use the existing private `_UNSET` sentinel rather than exporting a new `DEFAULT` constant, because `_UNSET` is already used for other optional parameters and the public surface stays unchanged.

**Sub-decision: no pytest-coupled default.** The framework does NOT detect pytest. Test fixtures pass `store=MemoryStore()` for hermetic persistence or `store=None` to disable it; the test suite sandboxes `XDG_STATE_HOME` to a temp dir.

**Sub-decision: XDG canonical default path.** `$XDG_STATE_HOME/<name>/store.json` is the canonical default, following the XDG Base Directory Specification for persistent application state.

```python
import cosalette

# Zero-config: store auto-resolved from app name
# → respects MYAPP_STORE_PATH, then XDG_STATE_HOME/myapp/store.json
app = cosalette.App(name="myapp", version="1.0.0")

# Explicit opt-out: no persistence, no retained-topic cleanup
app = cosalette.App(name="myapp", version="1.0.0", store=None)

# Explicit store (unchanged behaviour)
app = cosalette.App(
    name="myapp",
    version="1.0.0",
    store=cosalette.JsonFileStore("/app/data/state.json"),
)

# Factory (unchanged behaviour — path derived from settings)
def make_store(settings: MyAppSettings) -> cosalette.Store:
    return cosalette.JsonFileStore(settings.data_dir / "state.json")

app = cosalette.App(name="myapp", version="1.0.0", store=make_store)
```

## Decision Drivers

- Retained-topic cleanup (ADR-048) should be a zero-config framework invariant, not an opt-in per-app detail
- Eliminate 4–5 files of near-identical boilerplate per app (store path helper, env override, test fixture wiring)
- Existing decoration-time `persist=` validation must keep working — it checks `_store_configured` at decoration time
- Respect XDG Base Directory Specification for the default path so tools like `systemd-tmpfiles` can manage app state conventionally
- Must not introduce a pytest coupling — test code explicitly controls store via `store=MemoryStore()` or `store=None`

## Considered Options

### Option 1: Eager default JsonFileStore via _UNSET sentinel (chosen)

Resolve the default store eagerly in `App.__init__`: if `store=` was not provided (detected via the existing `_UNSET` sentinel), construct a `JsonFileStore` at the XDG-derived path. The `_store_configured` flag is set to `True` so decoration-time `persist=` validation passes unchanged. Explicit `store=None` leaves `_store_configured = False`.

- *Advantages:* `_store_configured` is correct at decoration time — existing `persist=` validation needs no changes; Simplest implementation: a single conditional in `__init__`, reusing `_UNSET` already in the codebase; Side-effect-free at construction time: `JsonFileStore` defers directory creation to the first `save()` call; Public type signature unchanged (`Store | Callable[..., Store] | None`)
- *Disadvantages:* Changes the constructor's default-value contract — callers that inspect `app._store` directly would see a `JsonFileStore` where they previously saw `None`; Default path is ephemeral in containers unless the operator sets `<NAME>_STORE_PATH` to a mounted volume

### Option 2: Internal default-store factory resolved at bootstrap

Defer store resolution to the bootstrap phase (reusing the ADR-037 lazy-store path). When `store=` is omitted, register an internal default-store factory that is invoked during bootstrap alongside explicit factories. The resolved store is then injected the same way explicit stores are.

- *Advantages:* Single resolution phase — store path is consistent with how settings-derived stores are resolved; Could support settings-derived default paths in future without API changes
- *Disadvantages:* Fights the existing eager decoration-time `persist=` / `_store_configured` checks — the flag would be `False` at decoration time, breaking validation for the default-store case without extra special-casing; More moving parts: requires a new factory registration path inside the bootstrap engine; Adds complexity to ADR-037's already-nuanced resolution flow

### Option 3: Keep opt-in (status quo)

Leave `store=` as a purely opt-in parameter. Document the `resolve_store_path()` pattern and ship a helper function for app authors to import and call.

- *Advantages:* No changes to the framework constructor or bootstrap; Fully backwards-compatible — existing apps unaffected
- *Disadvantages:* Boilerplate per app grows as more apps adopt ADR-048 cleanup; The retained-topic invariant is not held by default — the caldates2mqtt production bug is not fixed for new apps; Helper functions diverge across apps (already observed in three current implementations)

## Decision Matrix

| Criterion | Eager default JsonFileStore via _UNSET sentinel | Internal default-store factory resolved at bootstrap | Keep opt-in (status quo) |
| --- | --- | --- | --- |
| Zero-config retained-topic cleanup | 5 | 5 | 1 |
| Compatibility with decoration-time persist= validation | 5 | 2 | 5 |
| Implementation simplicity | 5 | 2 | 5 |
| Test ergonomics (no implicit filesystem I/O in tests) | 4 | 3 | 5 |
| Boilerplate reduction per adopting app | 5 | 5 | 1 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- Orphaned retained-topic cleanup (ADR-048) works with zero app-side configuration — all five previously store-less apps benefit immediately
- Approximately 30–50 lines of near-identical boilerplate eliminated per adopting app
- Default path follows the XDG Base Directory Specification (`~/.local/state/<name>/store.json`), compatible with `systemd-tmpfiles` and OS-level state management
- Existing apps that already configure `store=` are entirely unaffected — explicit stores take precedence
- `persist=` decorators now work without a `store=` argument on the App — the default store satisfies the requirement

### Negative

- In a container, the default XDG path (`~/.local/state/<name>/store.json`) is ephemeral unless the operator sets `<NAME>_STORE_PATH` to a path on a mounted volume — operators must be aware of this. Same-boot entity-removal cleanup still works from an ephemeral store; cross-restart cleanup requires a durable path.
- Apps that genuinely want no persistence must now pass `store=None` explicitly — the absence of `store=` no longer means 'no persistence'
- `persist=` no longer raises when `store=` is omitted (the auto-resolved default store satisfies it); it still raises when `store=None` is combined with `persist=`
- Deferred follow-ups tracked as beads gate tasks: startup WARNING for ephemeral/container store path (cos-4jh); configurable default backend (e.g. `SqliteStore`) (cos-87p); validate `XDG_STATE_HOME` is absolute and harden derived-name path safety (cos-nxc)

_2026-07-12_
