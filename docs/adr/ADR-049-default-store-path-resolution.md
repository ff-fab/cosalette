---
status: Accepted
date: 2026-07-12
impact: moderate
tags: [persistence, lifecycle, architecture, mqtt, health]
---

# ADR-049: Default store path resolution

## Status

Accepted **Date:** 2026-07-12 | Amended **Date:** 2026-07-12 | Amended **Date:** 2026-07-13 | Amended **Date:** 2026-07-21

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

## Amendment (2026-07-12) — Additive

**Rationale:** Three gate tasks deferred from ADR-049 have shipped on branch feat/store-defaults-followups: (1) configurable default backend via set_default_store_backend() (cos-87p), resolving Open Question 5; (2) ephemeral default-store startup WARNING when running in a detected container without <NAME>_STORE_PATH (cos-4jh), resolving Open Question 2; (3) stricter env-var normalization mapping all non-alphanumeric characters to underscores (cos-nxc). These sub-decisions extend the original decision without altering it.

### Additional Sub-Decision: Configurable default backend (resolves Open Question 5)

Expose `cosalette.set_default_store_backend(factory)` as a process-wide override of the backend used when `store=` is omitted from `App(...)`. The default is `JsonFileStore`. Passing `None` resets to the default. Explicit `store=` arguments on `App()` are entirely unaffected — this only influences the auto-resolution path.

The function is process-global and not thread-safe; it must be called once at import or startup time, before any `App()` instances are constructed.

```python
import cosalette
from cosalette import SqliteStore

# High-write apps: swap the auto-resolved backend to SQLite
cosalette.set_default_store_backend(SqliteStore)

# App() now resolves a SqliteStore (path still from <NAME>_STORE_PATH / XDG)
app = cosalette.App(name="myapp", version="1.0.0")

# Explicit store= is unaffected — still uses JsonFileStore here
other = cosalette.App(name="other", store=cosalette.JsonFileStore("/tmp/x.json"))

# Reset to default (JsonFileStore)
cosalette.set_default_store_backend(None)
```

**Rationale:** opt-in process-global override keeps the public API surface minimal (no new `App()` parameter), while still allowing high-write apps to adopt SQLite without boilerplate. The `JsonFileStore` default is preserved for the common case — existing apps and tests are unaffected.

### Additional Sub-Decision: Ephemeral default-store startup WARNING (resolves Open Question 2)

When `App()` auto-resolves the default store AND no `<NAME>_STORE_PATH` environment variable is set AND a container runtime is detected (presence of `/.dockerenv`, `/run/.containerenv`, or the `container` environment variable), the framework logs a `WARNING` at startup pointing the operator to set `<NAME>_STORE_PATH` to a path on a mounted volume.

The warning fires once per app instance at bootstrap, only for the auto-default path. Apps that pass explicit `store=`, `store=None`, or that already set `<NAME>_STORE_PATH` are unaffected.

**Rationale:** the ephemeral container path is the most common source of silent cross-restart data loss (see ADR-049 Consequences — Negative). A startup warning surfaces the risk at the earliest opportunity without making the env var mandatory or changing default behaviour. Same-boot entity-removal cleanup still works from an ephemeral store; the warning only fires when cross-restart durability is at risk.

### Additional Sub-Decision: Stricter env-var normalization

The `<NAME>_STORE_PATH` stem now maps **every non-alphanumeric character** (not only hyphens and spaces) to an underscore, producing shell-safe variable names in all cases.

Examples: `sensor.hub` → `SENSOR_HUB_STORE_PATH`; `my-app v2` → `MY_APP_V2_STORE_PATH`.

The shared `_normalize_env_name()` helper is used consistently across all env-var derivation paths (store path, env-prefix lookup). Existing apps whose names only contain alphanumeric characters, hyphens, or spaces are unaffected — their derived env var names are identical to the previous behaviour.

### Additional Positive Consequences

- High-write apps can adopt SqliteStore as the auto-resolved backend with a single process-level call, without any per-App boilerplate
- Container operators are warned at startup when the default store is ephemeral, reducing the risk of silent cross-restart data loss
- App names containing dots, slashes, or other special characters produce valid, shell-safe env var names

### Additional Negative Consequences

- set_default_store_backend() is process-global and not thread-safe; incorrect use in multi-threaded test suites (without reset) can leak state across test cases

## Amendment (2026-07-13) — Minor

!!! note "Editorial note (2026-07-13)"
    ### Warning scoping refinement (cos-08t)

    The ephemeral default-store startup WARNING (introduced in the 2026-07-12 amendment, resolving cos-4jh) is now scoped more precisely. It fires only when the app's entity set **may vary by config across restarts** — specifically, when any of the following conditions hold:

    - a `device`, `telemetry`, or `command` registration uses a callable (config-derived) `name=` parameter;
    - a `device`, `telemetry`, or `command` registration uses a callable `enabled=` parameter (config-gated membership); or
    - the app registers at least one `@app.on_configure` hook (conservative: such hooks may register config-derived entities that are not visible at the pre-hook bootstrap checkpoint where the warning fires — see ADR-023).

    Apps whose entity set is **provably static** — static string `name=`, no callable `enabled=`, no `@app.on_configure` hooks — no longer emit the warning, because ADR-048 retained-topic cleanup has nothing to clean for them across restarts. Such apps no longer need `store=None` purely to silence a false-positive warning.

    **Conservative-by-design:** the predicate errs toward warning. A false negative (failing to warn a config-driven app) would silently allow the ADR-048 ghost-entity bug to recur — orphaned retained topics surviving cross-restart because the snapshot lived on ephemeral storage. This is worse than an occasional harmless over-warn on an app that uses `@app.on_configure` for non-entity-varying reasons (e.g. config validation only).

    The warning text, call site, and default-store creation path are unchanged; only the firing condition is narrowed.

    Context: beads cos-08t; `tmp/framework-enhancement-proposal.md`.

!!! note "Editorial note (2026-07-13)"
    ### Deferred options (for the record)

    **Option B — skip default-store creation for static apps:** Also omit `JsonFileStore` construction when `_has_dynamic_entity_set()` returns `False`. Deferred because default-store resolution happens in `App.__init__` (before configure hooks run); moving it to the post-configure lifecycle step introduces DI-timing risk — `_store_configured` must be correct at decoration time for `persist=` validation (see original ADR-049 Decision and Option 2 disadvantages). The narrowed warning already removes the main motivation for this option in the static-app case.

    **Option C — explicit self-documenting opt-out:** Expose a `retained_cleanup=` parameter on `App()` or a `NO_STORE` sentinel to let app authors document their intent explicitly, rather than relying on static-analysis heuristics. Deferred unless a real-world `@app.on_configure` app is shown to over-warn in practice. Would be tracked as a gate task under cos-08t.

### Additional Positive Consequences

- Apps with a provably-static entity set (static string name=, no callable enabled=, no @app.on_configure hooks) are spared the ephemeral-store startup WARNING — no store=None workaround required

### Additional Negative Consequences

- Apps that use @app.on_configure for non-entity-varying reasons (e.g. config validation only) will still receive the WARNING — the conservative heuristic over-warns in exchange for preventing silent ADR-048 ghost-entity regressions

## Amendment (2026-07-13) — Additive

**Rationale:** Option B was recorded as deferred in the ADR-049 2026-07-13 amendment (cos-08t) with a DI-timing concern: moving default-store creation to post-init would break decoration-time persist= validation. The implemented approach avoids that risk entirely by keeping store creation in __init__ and instead conditionally bypassing only the ADR-048 cleanup call-sites (register_connect_reannounce, publish_startup_snapshot). This makes Option B additive: zero API change, backward-compatible, no migration cost.

### Additional Sub-Decision: Option B — Skip ADR-048 snapshot I/O for provably-static apps (cos-ko1)

When `_has_dynamic_entity_set()` returns `False` at the pre-hook, pre-`expand_name_specs` checkpoint, pass `None` as the cleanup store to `register_connect_reannounce` and `publish_startup_snapshot`. Both propagate the store to `reconcile_retained_topics`, which already returns immediately when `store is None`. The `persist=` path (`wire_router`, `run_lifespan_and_devices`) continues to receive `self._store` unchanged.

Critically, this requires NO DI-timing change: the default `JsonFileStore` is still created in `App.__init__` (so `_store_configured` is correct at decoration time and `persist=` validation is unaffected). The predicate is evaluated once before `run_configure_hooks` as a local variable `_is_dynamic_app` and reused by both the warning gate and this cleanup gate. After `expand_name_specs`, `name_spec` fields are cleared, so re-evaluating the predicate post-expand would incorrectly return `False` for dynamic-name apps without `on_configure` hooks — the pre-computed flag avoids this.

The earlier deferred Option B concerned moving default-store creation to post-init; the chosen approach instead conditionally bypasses the cleanup call-sites only. No migration or API change. The two call-sites using `_cleanup_store` are the only write paths for the ADR-048 snapshot; store.json is never created on disk for a static app unless persist= is also used.

### Additional Positive Consequences

- Provably-static apps produce no store.json unless persist= is used — zero persistence overhead, no XDG state-home directory created for them
- Eliminates unnecessary store.load/store.save round-trip on every first-connect for static apps

### Additional Negative Consequences

- The _has_dynamic_entity_set() predicate must be evaluated before run_configure_hooks and cached as a local variable; the pre-hook timing constraint is now shared by both the warning gate and the cleanup gate

## Amendment (2026-07-21) — Additive

**Rationale:** Option C (explicit self-documenting opt-out), recorded as deferred in the 2026-07-13 amendment (cos-08t) pending a real-world over-warning case, is now implemented as `App(retained_cleanup=...)` (beads cos-mur). A production `@app.on_configure` app used solely for config validation — with no config-derived entity variation — was confirmed to over-warn under the conservative heuristic. The explicit parameter gives authors a call-site signal that names the ADR-048 concern directly, replacing reliance on structural analysis alone.

### Additional Sub-Decision: Option C — explicit retained_cleanup opt-out (cos-mur)

Expose `App(retained_cleanup: bool | None = None)` as a keyword-only tri-state override of the ADR-048 cleanup and ephemeral-store warning behaviour:

- `None` (default) — unchanged: the existing `has_dynamic_entities` auto-heuristic governs both the warning gate and the cleanup gate, identical byte-for-byte to behaviour before this amendment.
- `False` — never run ADR-048 cleanup AND suppress the ephemeral default-store warning, even when an explicit `store=` was passed. The store is **kept** for `persist=` device-state handlers; only cleanup and the warning are disabled. This is the self-documenting escape hatch for an `@app.on_configure` app that uses the hook for non-entity-varying reasons (e.g. config validation only) and would otherwise over-warn under the conservative heuristic.
- `True` — force cleanup on and emit the ephemeral-store warning even for structurally-static apps. Graceful no-op when combined with `store=None` (no store means no snapshot to compare against). Addresses the heuristic's known false-negative for apps whose entity names are derived from import-time config values (not callable `name=` specs) — static by the structural predicate but dynamic in practice.

`App.retained_cleanup` is exposed as a read-only public property returning the raw override value (`bool | None`). `has_dynamic_entities` and `_has_dynamic_entity_set` continue to report the structural heuristic unchanged; `retained_cleanup` is a separate override layer.

Both lifecycle gates — the startup warning check and the ADR-048 cleanup store selection — now route through a single `_cleanup_enabled()` predicate that resolves the three-way override, ensuring both gates remain in sync.

**Why `retained_cleanup=` rather than a `NO_STORE` sentinel?** A `NO_STORE` sentinel would overlap confusingly with the existing `store=None` (which explicitly drops all persistence). `retained_cleanup=False` is orthogonal to persistence: the store is kept for `persist=` device state while only ADR-048 cleanup and the ephemeral warning are disabled. The parameter name directly names the ADR-048 concern, making the author's intent grep-able and self-documenting at the call site.

```python
# @app.on_configure used only for config validation — no entity variation
app = cosalette.App(
    name="myapp",
    version="1.0.0",
    retained_cleanup=False,  # skip ADR-048 cleanup + ephemeral warning
)                            # store still resolved for persist=

# Force cleanup for an app with import-time config-derived entity names
app = cosalette.App(
    name="myapp",
    version="1.0.0",
    retained_cleanup=True,   # override heuristic false-negative
)
```

### Additional Positive Consequences

- An @app.on_configure app that varies no entities can now silence the ephemeral-store WARNING with an explicit, greppable call-site flag (retained_cleanup=False) while keeping the store available for persist= device-state handlers — no store=None workaround that sacrifices persistence
- retained_cleanup=True lets apps with import-time config-derived entity names force cleanup that the structural heuristic (has_dynamic_entities) would miss, closing a known false-negative

### Additional Negative Consequences

- A third store-related knob (store=, set_default_store_backend(), retained_cleanup=) that authors must understand; documentation and AI help content must cover the interaction clearly
- retained_cleanup=True combined with store=None is a silent no-op rather than an error — the cleanup snapshot cannot be written without a store, so the True override has no effect when persistence is explicitly disabled
