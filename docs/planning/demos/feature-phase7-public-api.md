# Phase 7: Public API, Gate Tasks & Integration Tests

*2026-02-16T20:15:29Z by Showboat 0.5.0*

Implemented two gate task decisions (adapter factory callables + MqttLifecycle/MqttMessageHandler protocols), audited public API (26 symbols), removed main.py placeholder, and added 8 full-lifecycle integration tests validating the gas2mqtt pattern. 293 tests total, 94.6% coverage.

```bash
cd packages && uv run pytest tests/unit/test_integration.py tests/unit/test_public_api.py -v 2>&1 | tail -20
```

```output
platform linux -- Python 3.14.2, pytest-9.0.2, pluggy-1.6.0 -- /workspace/packages/.venv/bin/python
cachedir: .pytest_cache
rootdir: /workspace/packages
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.3.0, cov-7.0.0, httpx-0.36.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collecting ... collected 10 items

tests/unit/test_integration.py::TestFullLifecycle::test_device_publishes_state PASSED [ 10%]
tests/unit/test_integration.py::TestFullLifecycle::test_device_receives_command PASSED [ 20%]
tests/unit/test_integration.py::TestFullLifecycle::test_telemetry_publishes PASSED [ 30%]
tests/unit/test_integration.py::TestFullLifecycle::test_startup_hook_runs PASSED [ 40%]
tests/unit/test_integration.py::TestFullLifecycle::test_shutdown_hook_runs PASSED [ 50%]
tests/unit/test_integration.py::TestFullLifecycle::test_adapter_resolution_in_lifecycle PASSED [ 60%]
tests/unit/test_integration.py::TestFullLifecycle::test_dry_run_adapter_swap PASSED [ 70%]
tests/unit/test_integration.py::TestFullLifecycle::test_full_lifecycle_gas2mqtt_pattern PASSED [ 80%]
tests/unit/test_public_api.py::TestCosalettePublicAPI::test_all_contains_expected_symbols PASSED [ 90%]
tests/unit/test_public_api.py::TestCosalettePublicAPI::test_all_symbols_importable PASSED [100%]

============================== 10 passed in 0.09s ==============================
```
