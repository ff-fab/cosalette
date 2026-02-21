## Epic Telemetry Error Dedup Complete: Phase 1 — State-Transition Dedup

Added state-transition error deduplication to `_run_telemetry`. Consecutive same-type
errors are suppressed (only the first is published), different error types trigger a new
publish, and recovery from error to healthy is logged at INFO level.

**Files created/changed:**

- `packages/src/cosalette/_app.py`
- `packages/tests/unit/test_app.py`

**Functions created/changed:**

- `App._run_telemetry` — added `last_error_type` tracking, conditional publish, recovery log

**Tests created/changed:**

- `test_telemetry_persistent_error_deduplicated` — same type suppressed
- `test_telemetry_different_error_types_not_suppressed` — type change publishes
- `test_telemetry_recovery_logged` — error→healthy logs "recovered"
- `test_telemetry_error_after_recovery_published` — full cycle resets dedup

**Review Status:** APPROVED

**Git Commit Message:**

```text
feat: add state-transition error dedup to telemetry polling

- Track last_error_type per telemetry loop to suppress duplicate errors
- Publish only on error type change (healthy→error or error→different error)
- Log recovery at INFO level on error→healthy transition
- Add 4 state-transition tests covering all dedup paths
```
