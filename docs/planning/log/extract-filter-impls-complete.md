## Epic Extract Filter Impls Complete: Shared _filter_impls Test Helper

Extracted the duplicated `_pt1_impls` dual-backend parametrize list from 3 test files
into a single `tests/fixtures/filter_impls.py` module. Added `median_impls` and
`one_euro_impls` stubs ready for when Rust implementations land (COS-y9c). Lists are
frozen as tuples to prevent accidental mutation across test sessions.

**Files created/changed:**

- `packages/tests/fixtures/__init__.py` (created — makes fixtures a proper package)
- `packages/tests/fixtures/filter_impls.py` (created — shared parametrize lists)
- `packages/tests/unit/test_filters.py` (changed — import from shared module)
- `packages/tests/unit/test_filters_properties.py` (changed — import from shared module)
- `packages/tests/benchmarks/test_bench_filters.py` (changed — import from shared module)

**Functions created/changed:**

- Module-level `pt1_impls`, `median_impls`, `one_euro_impls` tuples in `filter_impls.py`

**Tests created/changed:**

- No new tests — all 973 existing unit tests pass
- 10 benchmark tests pass
- Import path verified across both unit and benchmark directories

**Review Status:** APPROVED

**Git Commit Message:**

```
refactor: extract shared filter_impls test helper

- Move duplicated _pt1_impls parametrize list to tests/fixtures/filter_impls.py
- Add median_impls and one_euro_impls stubs for future Rust backends
- Freeze lists as tuples to prevent accidental cross-test mutation
```
