## Epic Retry/Backoff Complete: Tests

Added 38 unit and integration tests covering all retry/backoff functionality from ADR-024,
including registration validation, retry behavior, circuit breaker state machine, shutdown
during backoff, and error dedup reset.

**Files created/changed:**

- `packages/tests/unit/test_retry.py` (NEW — 207 lines)
- `packages/tests/unit/test_telemetry_retry.py` (NEW — 618 lines)

**Functions created/changed:**

- `TestExponentialBackoff` — 6 tests: delay at attempts 1/2/3, max cap, jitter bounds, repr
- `TestLinearBackoff` — 3 tests: linear growth, max cap, repr
- `TestFixedBackoff` — 2 tests: constant delay, repr
- `TestCircuitBreaker` — 11 tests: full state machine (closed→open→half-open→closed),
  probe failure reopens, success resets, threshold property, repr
- `TestTelemetryRetryRegistration` — 6 tests: stored params, default backoff/retry_on,
  zero-retry no defaults, empty retry_on raises, circuit breaker stored
- `TestTelemetryRetryBehavior` — 8 tests: no-retry baseline, transient recovery, retry
  exhausted, retry_on filtering, intermediate not published, warning logging, shutdown
  during backoff aborts, error dedup resets after success
- `TestCircuitBreakerIntegration` — 2 tests: opens after threshold, recovers on probe

**Tests created/changed:**

- 22 unit tests in `test_retry.py`
- 16 integration tests in `test_telemetry_retry.py`
- Total: 38 new tests (1053 total suite)

**Review Status:** APPROVED (after addressing 2 major + 2 minor items)

**Git Commit Message:**

```
test: add retry/backoff unit and integration tests

- 22 unit tests for BackoffStrategy implementations and CircuitBreaker
- 16 integration tests for telemetry retry behavior end-to-end
- Covers all ADR-024 acceptance criteria including shutdown during backoff
  and error dedup reset after successful retry
```
