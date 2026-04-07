---
status: Accepted
date: 2026-03-09
impact: moderate
tags: [signal-filters, dependencies]
---

# ADR-022: Rust-Only Signal Filters

## Status

Accepted **Date:** 2026-03-09

## Context

ADR-014 established signal filters as a utility library (`cosalette.filters`),
not framework-integrated infrastructure. The initial implementation was pure
Python: `Pt1Filter`, `MedianFilter`, and `OneEuroFilter` in
`cosalette._filters`, totalling ~350 lines.

As part of the Rust acceleration initiative (Epic P4.1), all three filters were
reimplemented as pyo3 `#[pyclass]` structs in the `cosalette-filters-rs` crate.
The Rust implementations are API-identical drop-ins passing the same test suite
(unit, property-based, and benchmark). A `cosalette.filters` auto-select module
was introduced with a try/except ImportError pattern: prefer Rust, fall back to
Python.

This created a dual-backend architecture with two complete implementations
maintained in parallel — every algorithm in two languages, every property
parametrised twice, every bug fixed in two places.

### Why the fallback no longer makes sense

The original rationale for keeping Python as a fallback was platform portability:
pure-Python packages install everywhere without a compiler toolchain. However:

1. **Cosalette already depends on compiled extensions.** `orjson` (Rust-based,
   ADR-021) and `pydantic` (with compiled validators) are hard dependencies.
   Anyone installing cosalette already accepts native wheels.

2. **Python ≥ 3.14 requirement.** The project does not chase maximum
   portability — it targets a single modern Python version.

3. **IoT/embedded audience wants performance.** Filters running on RPi Zeros at
   10 kHz sample rates are the primary use case. The Python implementations were
   prototypes; the Rust versions are ~50× faster.

4. **Maintenance cost is real.** Two languages × three filters × unit tests ×
   property tests × benchmarks = significant ongoing sync burden. The Python
   implementations become zombie code nobody intentionally runs.

5. **Precedent: ADR-021.** The JSON serialisation decision followed the same
   logic — `orjson` moved from optional to hard dependency because the
   portability argument was not compelling for this project's audience.

## Decision

Make `cosalette-filters-rs` a **hard dependency** of cosalette and remove the
Python filter implementations.

Specifically:

- Move `cosalette-filters-rs>=0.1.0` from `[project.optional-dependencies]` to
  `[project.dependencies]` in `pyproject.toml`.
- Delete the `fast-filters` optional extra.
- Reduce `cosalette._filters` to the `Filter` protocol only (a Python typing
  construct that cannot exist in Rust).
- Simplify `cosalette.filters` to direct imports — no try/except fallback.
- Remove the dual-backend test parametrisation fixture
  (`tests/fixtures/filter_impls.py`).
- Delete the `_alpha_from_cutoff` helper and its tests (Rust-internal now).

The public API is unchanged: `from cosalette.filters import Pt1Filter` works
exactly as before. The `Filter` protocol remains available for structural typing
and `isinstance()` checks.

## Decision Drivers

- **Single source of truth** — one implementation to maintain, test, and debug
- **ADR-021 precedent** — compiled hard dependency already established
- **Performance by default** — users get the fast path without opting in
- **Reduced test surface** — no dual-backend parametrisation overhead
- **YAGNI** — the fallback path was never intentionally exercised in production

## Consequences

### Positive

- Filter maintenance effort halved — Rust is the single canonical implementation
- Test suite simplified — no dual-backend parametrisation
- Import chain simplified — `filters.py` becomes a thin re-export module
- No user-visible API change — import paths and class signatures are identical

### Negative

- Cosalette is uninstallable on platforms without pre-built
  `cosalette-filters-rs` wheels (mitigated by COS-8ex: CI wheel matrix for
  manylinux, musllinux, macOS)
- Development requires a working Rust toolchain (already required for
  `maturin develop`)

### Neutral

- The `Filter` protocol remains in `cosalette._filters` as a Python typing
  construct — pyo3 classes satisfy it structurally

## Supersedes

This ADR partially supersedes the "optional accelerator" pattern described in
the original Rust integration strategy (P4.1d). The fallback architecture served
its purpose during the transition period while Rust implementations were being
validated against the Python reference.
