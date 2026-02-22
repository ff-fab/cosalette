## Epic Telemetry Publish Strategies: Phase 1c+1d — Framework Integration & API Export

Wired publish strategies into the telemetry loop, exported the public API, and fixed a composite short-circuit bug found during code review. All 486 unit tests pass, lint clean, typecheck clean.

**Files created/changed:**

- [packages/src/cosalette/_strategies.py](../../packages/src/cosalette/_strategies.py) — Added `_bind` to `PublishStrategy` protocol; fixed eager evaluation in composites
- [packages/src/cosalette/_app.py](../../packages/src/cosalette/_app.py) — Added `publish=` to `@app.telemetry`, updated `_TelemetryRegistration`, rewrote `_run_telemetry` with strategy lifecycle
- [packages/src/cosalette/__init__.py](../../packages/src/cosalette/__init__.py) — Exported `Every`, `OnChange`, `PublishStrategy`
- [packages/tests/unit/test_strategies.py](../../packages/tests/unit/test_strategies.py) — Added eager-evaluation tests, fixed protocol `Dummy`
- [packages/tests/unit/test_app.py](../../packages/tests/unit/test_app.py) — Added `TestTelemetryPublishStrategies` (6 tests)
- [packages/tests/unit/test_public_api.py](../../packages/tests/unit/test_public_api.py) — Added `Every`, `OnChange`, `PublishStrategy` to expected names

**Functions created/changed:**

- `PublishStrategy._bind()` — added to protocol contract
- `_TelemetryRegistration.publish_strategy` — new field (`PublishStrategy | None`)
- `App.telemetry()` — added `publish=` parameter
- `App._run_telemetry()` — strategy lifecycle (bind, first-publish guarantee, should_publish, on_published, None return)
- `AnyStrategy.should_publish()` — eager evaluation (no short-circuit)
- `AllStrategy.should_publish()` — eager evaluation (no short-circuit)

**Tests created/changed:**

- `TestTelemetryPublishStrategies.test_telemetry_with_strategy_stores_registration`
- `TestTelemetryPublishStrategies.test_telemetry_without_strategy_defaults_to_none`
- `TestTelemetryPublishStrategies.test_telemetry_strategy_suppresses_publish`
- `TestTelemetryPublishStrategies.test_telemetry_none_return_suppresses_publish`
- `TestTelemetryPublishStrategies.test_telemetry_first_publish_always_goes_through`
- `TestTelemetryPublishStrategies.test_telemetry_strategy_on_published_called`
- `TestAnyStrategy.test_all_children_evaluated_no_short_circuit`
- `TestAllStrategy.test_all_children_evaluated_no_short_circuit`

**Review Status:** APPROVED (after addressing 1 critical + 2 minor findings)

**Git Commit Message:**

```
feat: wire publish strategies into telemetry loop

- Add publish= parameter to @app.telemetry decorator
- Rewrite _run_telemetry with strategy lifecycle (bind, first-publish
  guarantee, should_publish/on_published, None return suppression)
- Add _bind to PublishStrategy protocol for explicit clock injection
- Fix composite short-circuit bug (eager evaluation in AnyStrategy/AllStrategy)
- Export Every, OnChange, PublishStrategy in public API
```
