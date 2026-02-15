## Phase 3 Complete: Error Publisher + Health Reporter

Added structured error publication (`_errors.py`) and health/availability
reporting (`_health.py`) to the cosalette framework, implementing ADR-011
and ADR-012. Both modules follow the established hexagonal architecture
with Protocol-based ports, fire-and-forget publishing, and frozen dataclass
value objects.

**Files created:**

- `packages/src/cosalette/_errors.py`
- `packages/src/cosalette/_health.py`
- `packages/tests/unit/test_errors.py`
- `packages/tests/unit/test_health.py`

**Files changed:**

- `packages/src/cosalette/__init__.py`
- `packages/src/cosalette/_mqtt.py`

**Functions/classes created:**

- `ErrorPayload` — frozen dataclass for structured error events
- `build_error_payload()` — exception → payload with pluggable type mapping
- `ErrorPublisher` — fire-and-forget MQTT error publication service
- `DeviceStatus` — frozen dataclass for per-device health snapshots
- `HeartbeatPayload` — frozen dataclass for app-level heartbeat JSON
- `build_will_config()` — convenience builder for MQTT LWT configuration
- `HealthReporter` — heartbeat, device availability, and graceful shutdown

**Tests created:**

- `test_errors.py` — 23 tests across 4 classes (100% coverage)
- `test_health.py` — 30 tests across 6 classes (100% coverage)

**Review Status:** APPROVED (both sub-phases independently reviewed)

**Git Commit Message:**

```
feat: add error publisher and health reporter (_errors.py, _health.py)

- ErrorPayload frozen dataclass with pluggable error type mapping
- ErrorPublisher with fire-and-forget MQTT publication (ADR-011)
- HealthReporter with heartbeat, device availability, and LWT (ADR-012)
- build_will_config() convenience for MQTT LWT setup
- MockMqttClient.raise_on_publish for testing error paths
- 53 new tests across 10 test classes, 100% coverage
```
