## Epic 3 Phase 1 Complete: Core Retry/Backoff Module + Registration

ADR-024 written and accepted. New `_retry.py` module with `BackoffStrategy` protocol,
three built-in strategies (`ExponentialBackoff`, `LinearBackoff`, `FixedBackoff`), and
`CircuitBreaker` state machine. Retry logic integrated into both `run_telemetry()` and
`_process_group_handler_result()` with cumulative counter and shutdown-aware backoff.

**Files created/changed:**

- `packages/src/cosalette/_retry.py` (NEW)
- `packages/src/cosalette/_registration.py`
- `packages/src/cosalette/_app.py`
- `packages/src/cosalette/_telemetry_runner.py`
- `packages/src/cosalette/__init__.py`
- `packages/src/cosalette/_introspect.py`
- `packages/tests/unit/test_public_api.py`
- `docs/adr/ADR-024-telemetry-retry-backoff.md` (NEW)
- `docs/adr/index.md`

**Functions created/changed:**

- `BackoffStrategy` protocol (new)
- `ExponentialBackoff.delay()`, `LinearBackoff.delay()`, `FixedBackoff.delay()` (new)
- `CircuitBreaker.should_attempt()`, `.record_success()`, `.record_failure()` (new)
- `TelemetryRunner.run_telemetry()` (retry loop added)
- `TelemetryRunner._process_group_handler_result()` (retry loop added)
- `App.telemetry()` (retry/backoff/circuit_breaker params added)
- `App.add_telemetry()` (retry/backoff/circuit_breaker params added)
- `App._validate_telemetry_args()` (retry validation added)
- `_describe_telemetry()` (retry fields in introspection)

**Tests created/changed:**

- `test_public_api.py` (updated expected exports)

**Review Status:** APPROVED

**Git Commit Message:**

```
feat: add retry/backoff to @app.telemetry (ADR-024)

- BackoffStrategy protocol with Exponential/Linear/Fixed implementations
- CircuitBreaker with closed/open/half-open state machine
- Cumulative retry counter, resets on success
- Retry transparent to publish strategies
- Shutdown-aware backoff via ctx.sleep()
```
