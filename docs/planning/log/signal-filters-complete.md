## Epic Complete: Signal Filters Utility Library

Added three signal‐processing filters to cosalette as a stdlib-only utility library
(ADR-014). Filters are domain logic — the framework provides the import path but no
automatic wiring, preserving the hexagonal architecture boundary (ADR-006). All
filters satisfy a runtime-checkable `Filter` protocol.

**Phases Completed:** 3 of 3

1. ✅ Phase 1: Filter protocol + Pt1Filter
2. ✅ Phase 2: MedianFilter + OneEuroFilter
3. ✅ Phase 3: Documentation + ADR-014

**All Files Created/Modified:**

- `packages/src/cosalette/_filters.py` (new)
- `packages/src/cosalette/filters.py` (new)
- `packages/src/cosalette/__init__.py`
- `packages/tests/unit/test_filters.py` (new)
- `packages/tests/unit/test_public_api.py`
- `docs/adr/ADR-014-signal-filters.md` (new)
- `docs/guides/telemetry-device.md`
- `docs/reference/api.md`
- `docs/adr/index.md`
- `docs/planning/signal-filters.md` (new — planning doc)
- `docs/planning/demos/feat-signal-filters.md` (new — showboat demo)

**Key Functions/Classes Added:**

- `Filter` — `@runtime_checkable` Protocol: `update(raw) → float`, `reset()`,
  `value → float | None`
- `Pt1Filter(tau, dt)` — First-order low-pass, sample-rate-independent EWMA
- `MedianFilter(window)` — Sliding-window median for spike rejection
- `OneEuroFilter(min_cutoff, beta, d_cutoff, dt)` — Adaptive 1€ filter (Casiez 2012)
- `_alpha_from_cutoff(cutoff, dt)` — Shared helper for smoothing coefficient calculation

**Test Coverage:**

- Total tests written: 44
- All tests passing: ✅
- Suite total: 588 tests, 95.9% line coverage, 90.4% branch coverage

**Recommendations for Next Steps:**

- Consider adding `__repr__` to filter classes for debugging convenience
- Evaluate adding `EwmaFilter` as a thin convenience wrapper if users request it
- Consider `OutlierGuard` or `Clamp` utilities if patterns emerge from real usage
