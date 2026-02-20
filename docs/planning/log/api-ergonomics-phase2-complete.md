## Epic API Ergonomics Complete: Phase 2 — Lifespan Context Manager

Replaced `@app.on_startup` and `@app.on_shutdown` decorators with a single
`lifespan` async context manager parameter on `App()`, matching FastAPI's
modern pattern. Old decorators fully removed (pre-1.0, no backwards compat).

**Files created/changed:**

- `packages/src/cosalette/_app.py` — added `lifespan` param, `_noop_lifespan`,
  `LifespanFunc` type alias; removed `on_startup`, `on_shutdown`, `_run_hooks`
- `packages/src/cosalette/_context.py` — minor cleanup
- `packages/src/cosalette/__init__.py` — export `LifespanFunc`
- `packages/src/cosalette/testing/_harness.py` — removed startup/shutdown hook
  references
- `packages/cosalette_debug_app.py` — converted to lifespan pattern
- `packages/tests/unit/test_app.py` — converted all hook tests to lifespan,
  added 4 new lifespan tests
- `packages/tests/unit/test_public_api.py` — added `LifespanFunc` to expected
  symbols
- `packages/tests/integration/test_integration.py` — converted to lifespan

**Functions created/changed:**

- `LifespanFunc` (type alias) — `Callable[[AppContext], AbstractAsyncContextManager[None]]`
- `_noop_lifespan()` — default no-op lifespan
- `App.__init__()` — added `lifespan` parameter
- `App._run_async()` — replaced hook calls with `async with lifespan`
- Removed: `App.on_startup()`, `App.on_shutdown()`, `App._run_hooks()`

**Tests created/changed:**

- Converted all startup/shutdown hook tests to lifespan pattern
- `test_lifespan_startup_error_prevents_device_launch`
- `test_lifespan_teardown_error_logged_not_raised`
- `test_no_lifespan_noop_works`
- `test_lifespan_teardown_runs_after_device_cancellation`

**Review Status:** APPROVED

**Git Commit Message:**

```text
feat!: replace startup/shutdown hooks with lifespan context manager

- Add lifespan parameter to App() accepting async context manager
- Add LifespanFunc type alias and export from cosalette
- Add _noop_lifespan default when no lifespan provided
- Remove @app.on_startup, @app.on_shutdown decorators
- Remove _startup_hooks, _shutdown_hooks, _run_hooks internals
- Update debug app, harness, and all tests to lifespan pattern
- Add 4 new lifespan-specific tests (happy, error, noop, ordering)
```
