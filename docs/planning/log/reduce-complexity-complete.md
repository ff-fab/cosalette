## Epic Complete: Reduce Cognitive Complexity Below 15

Refactored all four functions that exceeded the cognitive complexity threshold of 15,
eliminating every `# noqa: CCR001` suppression. The quality gate now enforces cognitive
complexity without exceptions across the entire codebase, complementing the existing
cyclomatic complexity and duplication checks.

**Phases Completed:** 6 of 6

1. ✅ Phase 1: Extract cross-cutting helpers (`_publish_error_safely`,
   `_create_device_store`, `is_root` parameter)
2. ✅ Phase 2: Collapse registration decorators to delegate to `add_*` methods
3. ✅ Phase 3: Extract `_init_telemetry_handler` and `_process_telemetry_result`
4. ✅ Phase 4: Decompose `_wire_router` into 4 focused helpers
5. ✅ Phase 5: Extract CLI helpers from `build_cli` callback
6. ✅ Phase 6: Remove all `# noqa: CCR001` suppressions

**All Files Created/Modified:**

- `packages/src/cosalette/_app.py`
- `packages/src/cosalette/_cli.py`
- `docs/planning/log/reduce-complexity-*.md` (6 phase completion docs)

**Key Functions/Classes Added:**

- `App._publish_error_safely()` — static helper for safe error publishing
- `App._create_device_store()` — device store factory
- `App._init_telemetry_handler()` — telemetry handler initialization
- `App._process_telemetry_result()` — telemetry result processing
- `App._register_device_proxy()` — device proxy registration
- `App._init_command_store()` — command store initialization
- `App._init_command_handler()` — command handler initialization
- `App._register_command_proxy()` — command proxy registration
- `_validate_log_options()` — CLI log option validation
- `_apply_cli_overrides()` — CLI override application
- `_run_app()` — CLI app runner

**Complexity Scores (Before → After):**

| Function        | Before | After |
| --------------- | ------ | ----- |
| `_wire_router`  | 32     | 2     |
| `telemetry()`   | 20     | 6     |
| `_run_telemetry`| 17     | 7     |
| `build_cli()`   | 18     | 4     |

**Test Coverage:**

- Total tests written: 0 new (existing tests validated all refactoring)
- All tests passing: ✅ (878 tests, 95.5% line coverage, 91.3% branch coverage)

**Recommendations for Next Steps:**

- Merge PR #71 (quality gate) first, then this refactoring PR
- Consider evaluating `_process_group_handler_result` unification with telemetry
  processing (deferred from Phase 3 evaluation)
