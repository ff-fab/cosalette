---
status: Accepted
date: 2026-08-04
impact: high
tags: [architecture, cli, devices, configuration, naming]
---

# ADR-051: Settings-Aware Schema Pipeline for Settings-Derived Entity Names

## Status

Accepted **Date:** 2026-08-04 | Amended **Date:** 2026-08-08

> **Guard implemented** (cos-sdne.1/cos-sdne.2): `_reject_unexpanded_name_specs` ships in
> the companion PR and is live.  The settings-resolving dump mode (cos-sdne.3) is now
> implemented — see the Editorial note below.

## Context

The cosalette schema pipeline (`schema dump`, `schema init`, `schema check` in `_schema/_cli.py`) resolves the app via a bare `importlib.import_module` call in `_import_app` (`_schema/_cli_helpers.py`) — no settings, no bootstrap. Callable `name=` NameSpecs (ADR-023, Part B) are expanded only at bootstrap time: `expand_name_specs()` has exactly one caller, inside `app.run()` (`_app/_lifecycle.py`). Before that expansion runs, a callable-NameSpec registration carries `reg.name` equal to the handler's qualname, not the intended runtime name. `build_app_asyncapi()` (`_schema/_asyncapi.py`) emits `reg.name` verbatim with no NameSpec handling, so the static AsyncAPI artifact always reflects unexpanded handler qualnames for settings-derived entity sets.

Live, verified impact: a downstream application with settings-derived device names emits exactly **one** Home Assistant discovery payload whose `state_topic` is derived from the handler qualname — a phantom topic that nothing ever publishes — while every real entity is silently missing from discovery. Another such application emits **zero** HA discovery payloads. Every application whose discovery works today registers under Python literals known at import time; applications with settings-derived names produce incorrect or absent discovery payloads.

A companion fix landing in the **same PR as this ADR** (bugs cos-sdne.1 and cos-sdne.2) makes `schema dump`/`init`/`check` **fail loudly** when a registration still carries an unexpanded NameSpec, converting the previously silent wrong answer into a build error. That guard is the interim safety net — it stops shipping phantom discovery payloads — but it does NOT enable per-entity discovery for settings-derived applications. This ADR (cos-sdne.3) records the intended path that would actually unblock per-entity discovery, and is recorded here as a **design gate**: Proposed, implementation deferred pending acceptance.

The architectural split that causes this: the import-time pipeline sees handler qualnames; the bootstrap-time pipeline sees resolved entity names. Bridging them requires either (a) running bootstrap phases in the pipeline, or (b) expressing entity sets abstractly in the artifact and resolving them downstream.

## Decision

Adopt a settings-resolving schema dump mode — a dump mode that resolves settings and runs the configure and expand phases (including the same `expand_name_specs()` used at bootstrap) before building the AsyncAPI document — as the intended path to represent settings-derived entity sets in the static artifact. This decision is a **design gate**: the approach is chosen but implementation is deferred to a future issue; the companion fail-loud guard (cos-sdne.1/cos-sdne.2) serves as the interim safety net.

## Decision Drivers

- Applications that register entities under settings-derived names emit phantom or zero HA discovery payloads due to unexpanded callable NameSpecs reaching the static schema artifact.
- The import-time vs bootstrap-time split means the schema pipeline and the runtime live in fundamentally different name universes — the static artifact cannot equal the runtime name set without running bootstrap phases.
- Static-artifact parity with runtime names is a requirement for CI-committed schemas and reliable per-entity Home Assistant discovery.
- An ADR must precede implementation for architectural changes of this scope (design gate policy).

## Considered Options

### Option 1: Settings-resolving dump mode (chosen)

Extend the schema pipeline with a mode that resolves settings (loading from env/config) and runs the configure and expand phases — including `expand_name_specs()` — before calling `build_app_asyncapi()`. The resulting static artifact contains post-expansion, concrete entity names that match the runtime name set by construction.

- *Advantages:* Reuses the exact same `expand_name_specs()` expansion path as the runtime, so static artifact == runtime names by construction with no divergence risk.; Per-entity HA discovery works correctly from the static artifact — discovery payloads have real `state_topic` values matching published topics.; Aligns dump, check, and runtime on a single name set, closing the import-time vs bootstrap-time split in the pipeline.; No new name-resolution logic needed; the existing bootstrap machinery does the work.
- *Disadvantages:* Requires settings to be available and resolvable at schema-generation time — an env file or representative settings profile must be present in CI.; The static artifact becomes settings-profile-specific: different configurations yield different entity sets, so multi-profile applications may need multiple schema generation runs.; Introduces a settings dependency into a previously import-time-only pipeline, increasing CI setup complexity.

### Option 2: Parameterised AsyncAPI channels

Emit parameterised AsyncAPI channels (e.g. `{prefix}/{entity}/state`) with the entity set expressed as channel parameter enumerations resolved at generation time, per the AsyncAPI channel-parameters specification. Concrete per-entity topics are derived from the parameter values by consumers.

- *Advantages:* Keeps the artifact itself settings-independent — the template structure is stable across configurations.; Expresses the entity set abstractly, which is standards-aligned with AsyncAPI parameterisation.; Allows schema validation of topic patterns without requiring a specific settings profile.
- *Disadvantages:* HA discovery needs concrete per-entity topics and payloads, not templates, so a separate resolution step is still required downstream to materialise individual discovery payloads.; Substantially larger change to `build_app_asyncapi()` and the HA discovery generator than the settings-resolving dump mode.; Parameter value enumeration still ultimately requires settings at some point in the pipeline — the dependency is deferred, not eliminated.

### Option 3: Status quo with operational stopgap

Make no pipeline change. Rely on the runtime-published AsyncAPI artifact: after bootstrap, the application publishes the post-expansion document retained to `{prefix}/_meta/registry` (`_wiring/_infra.py`). Operators obtain correct per-entity discovery payloads via `mosquitto_sub -C 1 -t {prefix}/_meta/registry > /tmp/app.json` followed by `cosalette schema ha-discovery /tmp/app.json`.

- *Advantages:* Zero framework change and zero risk of regressions.; The operational stopgap works today for applications with a running broker and live application instance.; Already documented as an escape hatch in the operational guide.
- *Disadvantages:* A deployment stopgap, not a design — the artifact cannot be statically committed to CI or used in offline validation pipelines.; Requires a running broker and a live, fully-bootstrapped application instance to obtain the correct artifact.; Operationally fragile: the operator must extract the artifact manually on every configuration change.; Leaves the import-time vs bootstrap-time split unresolved as an ongoing source of phantom or missing discovery payloads.

## Decision Matrix

| Criterion | Settings-resolving dump mode | Parameterised AsyncAPI channels | Status quo with operational stopgap |
| --- | --- | --- | --- |
| Static artifact correctness | 5 | 3 | 1 |
| Per-entity HA discovery | 5 | 2 | 2 |
| Settings independence | 2 | 4 | 5 |
| Implementation cost | 3 | 2 | 5 |
| Alignment with runtime name set | 5 | 3 | 1 |
| Operational simplicity | 4 | 3 | 1 |

_Scale: 1 (poor) to 5 (excellent)_

## Consequences

### Positive

- When implemented, the static AsyncAPI artifact will contain post-expansion entity names that exactly match the runtime name set — per-entity HA discovery will work correctly for applications with settings-derived entity names without requiring a running broker.
- The schema pipeline and the runtime converge on a single name universe, eliminating the class of bug where phantom topics appear in the static artifact.
- The companion fail-loud guard (cos-sdne.1/cos-sdne.2) provides an immediate safety net: `schema dump`/`init`/`check` now fail with a build error rather than silently emitting incorrect payloads, bridging the gap until this ADR is implemented.

### Negative

- Implementation is deferred: until this ADR is acted on, applications with settings-derived entity names cannot use the static schema pipeline for HA discovery — they must fall back to the operational stopgap (runtime-retained `_meta/registry` artifact).
- When implemented, the settings-resolving dump mode introduces a settings dependency into a previously import-time-only pipeline — CI environments will need to provide a representative settings profile or env file for `schema dump` to succeed.
- Artifact becomes settings-profile-specific: applications with multiple configurations (e.g. different entity sets per deployment) will need per-profile schema generation runs, increasing CI surface.

!!! note "Editorial note (2026-08-05)"
    Implemented as an opt-in `--resolve-settings` / `--env-file` flag pair on `schema dump` only (`_schema/_cli.py`), not `init`/`check` — those two remain import-time-only for now; extending them is a natural follow-up if per-entity discovery validation is needed at those stages. When passed, `_resolve_app_settings()` (`_schema/_cli_helpers.py`) runs a *subset* of the sequence used by `app.run()` (`_app/_lifecycle.py`): settings → adapters → `run_configure_hooks` → `expand_name_specs` → `resolve_enabled` → `_check_expanded_duplicates`. It deliberately does **not** call `resolve_intervals`, `resolve_timeouts`, or `resolve_intervals_periodic` — harmless for the AsyncAPI document since neither interval nor timeout fields are emitted there, but worth stating plainly so nobody assumes full runtime parity for those fields from this pipeline. The included steps also carry two deliberate divergences: adapters are always resolved with `dry_run=True` regardless of the app's own dry-run default, since schema generation is static analysis and must never construct real hardware/network adapters (and `resolve_adapters` never enters `__aenter__`/`__aexit__`, so this is safe); and the `Store` is never resolved (`store=None` passed to `resolve_enabled`) since schema generation performs no persistence I/O — a surviving telemetry registration that declares `persist=` behind a callable `enabled=` is therefore still rejected, same as it would be with no store configured at runtime. `resolve_enabled` and `_check_expanded_duplicates` were added beyond what this ADR's Decision literally names (`expand_name_specs` only) because expansion alone does not prune config-gated (ADR-038 `enabled=`) registrations or catch post-expansion name collisions — both are needed for the static artifact to actually match the runtime name set. Settings construction failures and post-expansion `ValueError`s (duplicate names, `persist=` without a store) are caught and re-raised as friendly `typer.Exit` errors rather than raw tracebacks. The interim fail-loud guard (`_reject_unexpanded_name_specs`) is retained as a safety net inside the new pipeline, not the primary guard — it should never trigger once expansion has run, but exists to fail loudly rather than silently emit a phantom channel if a future `name_spec` kind is added without updating `expand_name_specs`.

## Amendment (2026-08-08) — Minor

!!! note "Editorial note (2026-08-08)"
    The follow-up flagged in the 2026-08-05 editorial note above is now implemented (cos-mxk1) — `--resolve-settings` / `--env-file` have been extended from `schema dump` to `schema check` and `schema init` as well (`_schema/_cli.py`). Both commands accept the same flag pair with the same semantics: when passed, they swap `_import_validated_app()` for `_resolve_app_settings(_import_app(app_spec), env_file)`, reusing the exact ADR-051 pipeline dump already used — no new resolution logic was introduced. `schema check` (the CI gate) can therefore validate a settings-derived (ADR-023 callable `name=`) app's real, post-expansion entity names against a schema written for those names, instead of only being able to refuse to run via the `_reject_unexpanded_name_specs` guard. `schema init` scaffolds real per-entity channels for such apps under the same flag. Without the flag, both commands are unchanged and the fail-loud guard still applies. This closes the schema-check/runtime-enforcement asymmetry this ADR's Context section originally described, for the two commands still on the import-time-only path.
