# Scoped Name Uniqueness

*2026-03-04T10:45:05Z by Showboat 0.6.1*
<!-- showboat-id: c430de55-474d-4d3d-868d-d1f25409d969 -->

Telemetry and command handlers can now share the same device name. The framework enforces name uniqueness per registration type (device/telemetry/command) rather than globally, enabling the common IoT pattern where a single physical device publishes telemetry and receives commands on the same MQTT topic namespace.

```bash
uv run pytest packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness -v --tb=short 2>&1 | tail -20
```

```output
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.3.0, cov-7.0.0, httpx-0.36.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collecting ... collected 13 items

packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_telemetry_and_command_share_name PASSED [  7%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_command_then_telemetry_share_name PASSED [ 15%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_device_rejects_collision_with_telemetry PASSED [ 23%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_device_rejects_collision_with_command PASSED [ 30%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_telemetry_after_device_rejected PASSED [ 38%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_command_after_device_rejected PASSED [ 46%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_two_telemetry_same_name_rejected PASSED [ 53%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_two_commands_same_name_rejected PASSED [ 61%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_shared_name_with_decorator_api PASSED [ 69%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_add_telemetry_and_add_command_share_name PASSED [ 76%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_disabled_telemetry_allows_same_name_command PASSED [ 84%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_shared_name_publishes_availability_once PASSED [ 92%]
packages/tests/unit/test_app_registration.py::TestScopedNameUniqueness::test_shared_name_produces_one_context PASSED [100%]

============================== 13 passed in 0.06s ==============================
```
