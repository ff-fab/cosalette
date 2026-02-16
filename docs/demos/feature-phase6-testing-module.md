# Phase 6: cosalette.testing Module

*2026-02-15T21:34:22Z by Showboat 0.5.0*

Created cosalette.testing subpackage with MockMqttClient, NullMqttClient, FakeClock re-exports, make_settings factory, AppHarness test harness, and a pytest plugin with auto-registered mock_mqtt, fake_clock, and device_context fixtures. All existing tests migrated to use the new public API.

```bash
cd packages && uv run pytest tests/unit/test_testing_module.py -v 2>&1 | tail -30
```

```output

tests/unit/test_testing_module.py::TestPublicAPI::test_all_contains_expected_symbols PASSED [  3%]
tests/unit/test_testing_module.py::TestPublicAPI::test_all_symbols_importable PASSED [  7%]
tests/unit/test_testing_module.py::TestFakeClock::test_default_time_is_zero PASSED [ 11%]
tests/unit/test_testing_module.py::TestFakeClock::test_custom_initial_time PASSED [ 14%]
tests/unit/test_testing_module.py::TestFakeClock::test_time_can_be_updated PASSED [ 18%]
tests/unit/test_testing_module.py::TestFakeClock::test_satisfies_clock_port PASSED [ 22%]
tests/unit/test_testing_module.py::TestMakeSettings::test_returns_settings_instance PASSED [ 25%]
tests/unit/test_testing_module.py::TestMakeSettings::test_defaults_mqtt_host_localhost PASSED [ 29%]
tests/unit/test_testing_module.py::TestMakeSettings::test_defaults_mqtt_port_1883 PASSED [ 33%]
tests/unit/test_testing_module.py::TestMakeSettings::test_accepts_overrides PASSED [ 37%]
tests/unit/test_testing_module.py::TestReExports::test_mock_mqtt_client_identity PASSED [ 40%]
tests/unit/test_testing_module.py::TestReExports::test_null_mqtt_client_identity PASSED [ 44%]
tests/unit/test_testing_module.py::TestAppHarness::test_create_returns_harness_instance PASSED [ 48%]
tests/unit/test_testing_module.py::TestAppHarness::test_create_defaults_name_and_version PASSED [ 51%]
tests/unit/test_testing_module.py::TestAppHarness::test_create_custom_name_version PASSED [ 55%]
tests/unit/test_testing_module.py::TestAppHarness::test_create_settings_overrides PASSED [ 59%]
tests/unit/test_testing_module.py::TestAppHarness::test_mqtt_is_mock_instance PASSED [ 62%]
tests/unit/test_testing_module.py::TestAppHarness::test_clock_is_fake_instance PASSED [ 66%]
tests/unit/test_testing_module.py::TestAppHarness::test_shutdown_event_initially_not_set PASSED [ 70%]
tests/unit/test_testing_module.py::TestAppHarness::test_trigger_shutdown_sets_event PASSED [ 74%]
tests/unit/test_testing_module.py::TestAppHarness::test_run_executes_device PASSED [ 77%]
tests/unit/test_testing_module.py::TestPytestPlugin::test_mock_mqtt_fixture_returns_mock PASSED [ 81%]
tests/unit/test_testing_module.py::TestPytestPlugin::test_fake_clock_fixture_returns_fake PASSED [ 85%]
tests/unit/test_testing_module.py::TestPytestPlugin::test_device_context_fixture_returns_context PASSED [ 88%]
tests/unit/test_testing_module.py::TestPytestPlugin::test_device_context_has_test_defaults PASSED [ 92%]
tests/unit/test_testing_module.py::TestPytestPlugin::test_device_context_uses_mock_mqtt PASSED [ 96%]
tests/unit/test_testing_module.py::TestPytestPlugin::test_fixtures_are_fresh_per_test PASSED [100%]

============================== 27 passed in 0.05s ==============================
```
