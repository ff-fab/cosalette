# Telemetry Error Deduplication & Health Integration

*2026-02-21T12:06:49Z by Showboat 0.5.0*

Implemented state-transition error deduplication in @app.telemetry polling loops. Consecutive same-type errors are suppressed, different types publish, recovery is logged at INFO. Device health status in heartbeat now reflects 'error'/'ok' transitions. Documentation updated across 5 files.

```bash
cd /workspace && task test:file -- packages/tests/unit/test_app.py -k 'telemetry_persistent_error_deduplicated or telemetry_different_error_types or telemetry_recovery_logged or telemetry_error_after_recovery or telemetry_error_updates_heartbeat' 2>&1 | grep 'PASSED\|FAILED'
```

```output
packages/tests/unit/test_app.py::TestRunAsync::test_telemetry_persistent_error_deduplicated PASSED [ 20%]
packages/tests/unit/test_app.py::TestRunAsync::test_telemetry_different_error_types_not_suppressed PASSED [ 40%]
packages/tests/unit/test_app.py::TestRunAsync::test_telemetry_recovery_logged PASSED [ 60%]
packages/tests/unit/test_app.py::TestRunAsync::test_telemetry_error_after_recovery_published PASSED [ 80%]
packages/tests/unit/test_app.py::TestRunAsync::test_telemetry_error_updates_heartbeat_status PASSED [100%]
```
