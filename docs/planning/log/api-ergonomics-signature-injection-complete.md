## Epic API Ergonomics Complete: Signature-based Handler Injection

Implemented signature-based dependency injection for device and telemetry handlers.
Handlers now declare only the parameters they need via type annotations — the framework
inspects signatures at registration time (fail-fast for missing annotations) and injects
matching objects at call time. Zero-parameter handlers are valid.

**Files created/changed:**

- `packages/src/cosalette/_injection.py` (NEW — core injection module)
- `packages/src/cosalette/_app.py` (decorator registration + handler execution)
- `packages/tests/unit/test_injection.py` (NEW — 24 unit tests)
- `packages/tests/unit/test_app.py` (9 integration tests in TestSignatureInjection)
- `packages/tests/unit/test_testing_module.py` (minor update)
- `packages/cosalette_debug_app.py` (showcases injection)

**Functions created/changed:**

- `build_injection_plan()` — inspects handler signature, returns `(name, type)` plan
- `build_providers()` — builds type→instance map from DeviceContext
- `resolve_kwargs()` — resolves plan against providers to kwargs dict
- `_is_settings_subclass()` — helper for Settings subclass matching
- `App.device()` — now calls `build_injection_plan()` at registration
- `App.telemetry()` — now calls `build_injection_plan()` at registration
- `App._run_device()` — uses `resolve_kwargs()` instead of `handler(ctx)`
- `App._run_telemetry()` — uses `resolve_kwargs()` instead of `handler(ctx)`

**Tests created/changed:**

- `TestBuildInjectionPlan` (10 tests) — zero/single/multi params, unknown types, errors
- `TestResolveKwargs` (10 tests) — all injectable types, Settings subclass, adapters
- `TestBuildProviders` (4 tests) — provider map contents, adapters, subclass
- `TestSignatureInjection` (9 tests) — full lifecycle integration tests

**Review Status:** APPROVED with minor recommendations (Settings subclass test added)

**Git Commit Message:**

```
feat: add signature-based handler injection

- Handlers declare only the parameters they need via type annotations
- Injectable types: DeviceContext, Settings, Logger, ClockPort, Event, adapters
- Fail-fast TypeError for missing annotations at registration time
- Zero-parameter handlers are valid (empty injection plan)
- 33 new tests (24 unit + 9 integration), 350 total passing
```
