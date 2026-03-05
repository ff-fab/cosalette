## Epic Reduce Cognitive Complexity Complete: Remove noqa Suppressions

Removed all four `# noqa: CCR001` suppression comments from `_app.py` and `_cli.py`,
validating that every previously-flagged function now scores below the threshold 15
without exceptions. Full pre-PR gate passed (878 tests, 95.5% coverage, all complexity
checks clean).

**Files changed:**

- `packages/src/cosalette/_app.py`
- `packages/src/cosalette/_cli.py`

**Functions cleaned:**

- `_wire_router` — noqa removed (score now 2, was 32)
- `_run_telemetry` — noqa removed (score now 7, was 17)
- `telemetry()` — noqa removed (score now 6, was 20)
- `build_cli()` — noqa removed (score now 4, was 18)

**Tests created/changed:**

- None (validation-only phase — existing 878 tests confirmed no regressions)

**Review Status:** APPROVED

**Git Commit Message:**

```
refactor: remove all CCR001 noqa suppressions

- All 4 previously-flagged functions now score below threshold 15
- Cognitive complexity gate enforced without exceptions
- 878 tests pass, 95.5% coverage maintained
```
