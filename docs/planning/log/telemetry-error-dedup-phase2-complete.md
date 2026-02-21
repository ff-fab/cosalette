## Epic Telemetry Error Dedup Complete: Phase 2 — Health Integration

Added health status integration to `_run_telemetry`. On error, device status in heartbeat
is set to `"error"`; on recovery, set back to `"ok"`. The heartbeat payload now reflects
actual device health instead of always showing `"ok"`.

**Files created/changed:**

- `packages/src/cosalette/_app.py`
- `packages/tests/unit/test_app.py`

**Functions created/changed:**

- `App._run_telemetry` — added `health_reporter` param, `set_device_status` calls
- `App._start_device_tasks` — added `health_reporter` param, forwards to `_run_telemetry`
- `App._run_async` — passes `health_reporter` to `_start_device_tasks`

**Tests created/changed:**

- `test_telemetry_error_updates_heartbeat_status` — verifies heartbeat shows "error" during failure and "ok" after recovery

**Review Status:** APPROVED

**Git Commit Message:**

```text
feat: reflect telemetry errors in health heartbeat status

- Thread health_reporter to _run_telemetry via _start_device_tasks
- Set device status to "error" on telemetry failure
- Restore device status to "ok" on recovery
- Add test verifying heartbeat reflects error/ok transitions
```
