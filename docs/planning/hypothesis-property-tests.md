# P6.1: Property-Based Tests with Hypothesis

**Bead:** COS-rmy
**Branch:** `feat/hypothesis-property-tests`

---

## Context

The existing unit tests for signal filters and publish strategies use
example-based testing (specific inputs → expected outputs). Property-based
testing complements this by generating thousands of random inputs and verifying
that **invariants** (mathematical properties) always hold.

**Hypothesis** is the standard Python library for property-based testing
(inspired by Haskell's QuickCheck). It provides `@given` to declaratively
specify input domains, automatic shrinking to find minimal failing examples,
and a database of previously-found failures for regression testing.

---

## Scope

Two new test files, as specified in the bead:

| File | Tests | Source |
|---|---|---|
| `test_filters_properties.py` | Pt1Filter, MedianFilter, OneEuroFilter | `_filters.py` |
| `test_strategies_properties.py` | OnChange, Every (count + time modes) | `_strategies.py` |

---

## Filter Properties

### Pt1Filter (First-Order Low-Pass)

| # | Property | Hypothesis Strategy | Rationale |
|---|----------|---------------------|-----------|
| F1 | **Convergence** — constant input `c` for N steps → output converges to `c` | `st.floats` for `c`, `tau`, `dt` | Core mathematical guarantee: IIR filter converges to DC input |
| F2 | **Bounded output** — output always between seed and new input | `st.floats` for raw values | Weighted average can't exceed its operands |
| F3 | **Alpha in (0, 1)** — `dt/(tau+dt)` always in open interval | `st.floats` for positive `tau`, `dt` | Precondition for stable filtering |
| F4 | **Seed passthrough** — first `update()` returns raw value unchanged | `st.floats` for any raw | Constructor contract |

### MedianFilter (Sliding Window)

| # | Property | Hypothesis Strategy | Rationale |
|---|----------|---------------------|-----------|
| M1 | **Bounded by window** — output ∈ [min(window), max(window)] | `st.lists(st.floats)` for values, `st.integers` for window size | Definition of median |
| M2 | **Spike rejection** — single outlier in odd window doesn't dominate | Generate N-1 identical + 1 outlier | Key use case for MedianFilter |
| M3 | **Constant input** — constant sequence → output equals that constant | `st.floats` for value | Trivial median property |

### OneEuroFilter (Adaptive Low-Pass)

| # | Property | Hypothesis Strategy | Rationale |
|---|----------|---------------------|-----------|
| O1 | **Convergence** — constant input → output converges | `st.floats` for value, params | Same DC convergence as Pt1 (it's a Pt1 internally) |
| O2 | **beta=0 convergence** — with `beta=0`, still converges for constant inputs | `st.floats` for values, `min_cutoff` | Degenerate, non-adaptive configuration should remain stable |
| O3 | **Seed passthrough** — first `update()` returns raw unchanged | `st.floats` for raw | Constructor contract |

---

## Strategy Properties

### OnChange

| # | Property | Hypothesis Strategy | Rationale |
|---|----------|---------------------|-----------|
| S1 | **First publish always** — `previous=None` → `True` | `st.dictionaries` for current | First reading must always publish |
| S2 | **Identical dicts never publish** — `current == previous` (no threshold) | `st.dictionaries` | Exact equality semantics |
| S3 | **Below threshold suppressed** — `|cur - prev| ≤ T` → `False` | `st.floats` for values + threshold | Dead-band contract (strict `>`) |
| S4 | **Above threshold publishes** — `|cur - prev| > T` → `True` | `st.floats` for values + threshold | Dead-band contract |
| S5 | **Structural change always publishes** — added/removed key → `True` | `st.dictionaries` with varying keys | Key-set change detection |

### Every (count mode)

| # | Property | Hypothesis Strategy | Rationale |
|---|----------|---------------------|-----------|
| E1 | **Publishes on Nth call** — exactly N calls to trigger | `st.integers(1, 100)` for n | Counter semantics |
| E2 | **Counter resets** — after `on_published()`, needs N more calls | `st.integers(1, 50)` for n | Reset contract |

### Every (time mode)

| # | Property | Hypothesis Strategy | Rationale |
|---|----------|---------------------|-----------|
| T1 | **Publishes after elapsed** — `elapsed >= seconds` → `True` | `st.floats` for seconds, elapsed | Timer semantics |
| T2 | **Suppressed before elapsed** — `elapsed < seconds` → `False` | `st.floats` for seconds, elapsed | Timer semantics |

---

## Implementation Plan

### Phase 1: Foundation

1. Add `hypothesis` to dev dependency group
2. Use per-test `@settings(max_examples=200)` (no global `[tool.hypothesis]` profile)
3. Create `test_filters_properties.py` — properties F1–F4, M1–M3, O1–O3
4. Create `test_strategies_properties.py` — properties S1–S5, E1–E2, T1–T2

### Phase 2: Validation

5. Run full test suite — all 900+ tests must pass
6. Run `task pre-pr`

### Decisions

- **Hypothesis profile**: Use `@settings(max_examples=200)` per test — enough
  for confidence without slowing CI. Default deadline of 200 ms per example is
  fine for these pure-computation tests.
- **Float strategies**: Use `allow_nan=False, allow_infinity=False` for filter
  inputs (NaN/Inf don't make physical sense for sensor readings). Use
  `min_value`/`max_value` to constrain to realistic ranges.
- **Pytest marker**: All tests get `pytestmark = pytest.mark.unit` (same as
  existing unit tests).
- **No new conftest needed**: FakeClock is available from the plugin; strategies
  will be instantiated inline.

---

## Teaching Moments

### What is Property-Based Testing?

Traditional example-based tests verify specific input→output pairs:
`assert median([3, 1, 2]) == 2`. Property-based tests verify **invariants
over all possible inputs**: "for any list, the median is always between the
min and max." Hypothesis generates hundreds of random inputs and automatically
**shrinks** failing examples to the minimal reproduction.

### Key Hypothesis Concepts

- **`@given`**: Decorator that generates test inputs from strategies
- **`st.floats()`**: Generates IEEE 754 floats (configurable for nan/inf)
- **`st.integers()`**: Generates arbitrary integers
- **`st.lists()`**: Generates lists of elements from another strategy
- **`@settings(max_examples=N)`**: Controls number of generated examples
- **Shrinking**: When a test fails, Hypothesis automatically finds the
  simplest input that still triggers the failure
- **Database**: Hypothesis remembers previously-found failures and replays
  them in future runs

### PEP/RFC References

- **PEP 544** — Structural subtyping (filter/strategy protocols)
- **IEEE 754** — Float representation (why we exclude NaN/Inf)
