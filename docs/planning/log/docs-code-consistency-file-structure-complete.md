## Epic Docs & Code Consistency Complete: File Structure Refactoring

Split two oversized files into focused modules. `_app.py` (1,703 → 1,526 lines) had
dataclasses and free functions extracted to `_registration.py` (212 lines). `test_app.py`
(5,401 → 3,341 lines) had adapter and registration test classes extracted to dedicated
files, with shared helpers consolidated in `unit/conftest.py`.

**Files created/changed:**
- `packages/src/cosalette/_registration.py` (NEW, 212 lines)
- `packages/src/cosalette/_app.py` (1,703 → 1,526 lines)
- `packages/tests/unit/test_app.py` (5,401 → 3,341 lines)
- `packages/tests/unit/test_app_adapters.py` (NEW, 975 lines)
- `packages/tests/unit/test_app_registration.py` (NEW, 1,000 lines)
- `packages/tests/unit/test_app_run.py` (NEW, 293 lines)
- `packages/tests/unit/conftest.py` (NEW, 178 lines)

**Functions created/changed:**
- Extracted to `_registration.py`: `_DeviceRegistration`, `_TelemetryRegistration`,
  `_CommandRegistration`, `_AdapterEntry`, `_is_async_context_manager`, `LifespanFunc`,
  `_noop_lifespan`, `_build_adapter_providers`, `_validate_init`, `_call_init`,
  `_call_factory`

**Tests created/changed:**
- Moved 5 adapter test classes to `test_app_adapters.py`
- Moved 5 registration test classes to `test_app_registration.py`
- Created 3 new run/CLI test classes in `test_app_run.py`
- Moved 13 helper classes to `unit/conftest.py`

**Review Status:** APPROVED (1 auto-fixed lint issue, duplicate fixture removed)

**Git Commit Message:**
```
refactor: split oversized app module and test file

- Extract dataclasses and helpers from _app.py to _registration.py
- Split test_app.py into test_app_adapters.py, test_app_registration.py, test_app_run.py
- Consolidate shared test helpers in unit/conftest.py
- All 800 tests pass, no behavioral changes
```
