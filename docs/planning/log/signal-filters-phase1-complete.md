## Epic Signal Filters: Phase 1 — Filter Protocol + Pt1Filter

Created the `cosalette.filters` utility library module with a `Filter` protocol and `Pt1Filter` — a first-order low-pass filter parameterised by time constant τ (seconds) and sample interval dt, making it sample-rate-independent unlike raw EWMA.

**Files created/changed:**

- packages/src/cosalette/_filters.py — Filter protocol + Pt1Filter implementation
- packages/src/cosalette/filters.py — public re-export module
- packages/tests/unit/test_filters.py — 15 tests across 3 test classes
- packages/src/cosalette/__init__.py — added Filter, Pt1Filter to exports
- packages/tests/unit/test_public_api.py — added Filter, Pt1Filter to EXPECTED_NAMES

**Functions created/changed:**

- `Filter` (Protocol) — `update(raw) → float`, `reset()`, `value → float | None`
- `Pt1Filter.__init__(tau, dt)` — constructor with bool/non-positive validation
- `Pt1Filter.update(raw)` — EWMA with alpha = dt/(tau+dt)
- `Pt1Filter.reset()` — clear internal state
- Properties: `tau`, `dt`, `alpha`, `value`

**Tests created/changed:**

- TestFilterProtocol (1 test) — structural subtyping check
- TestPt1FilterValidation (4 tests) — bool rejection, non-positive rejection
- TestPt1Filter (10 tests) — seeding, formula, convergence, reset, sample-rate independence

**Review Status:** APPROVED

**Git Commit Message:**
```
feat: add Filter protocol and Pt1Filter

- Create cosalette.filters utility library module
- Implement Filter protocol (update/reset/value contract)
- Add Pt1Filter: first-order low-pass with time constant tau
- Sample-rate-independent: alpha = dt/(tau+dt), unlike raw EWMA
- Constructor validation: reject bool, reject non-positive
- Public re-export via cosalette.filters sub-module
- 15 tests covering protocol conformance, validation, formula, convergence
```
