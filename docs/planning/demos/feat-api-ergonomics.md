# API Ergonomics: Lifespan + Runner + Injection

*2026-02-20T06:39:16Z by Showboat 0.5.0*

Three complementary API improvements: (1) app.run() sync entrypoint replaces manual asyncio.run + signal handling, (2) lifespan context manager replaces @app.on_startup/@app.on_shutdown for paired resource management, (3) signature-based handler injection allows handlers to declare only the parameters they need. All 15+ documentation files updated. ADR-013 created.

```bash
uv run pytest packages/tests/unit/test_app_run.py packages/tests/unit/test_injection.py -v --tb=short 2>&1 | tail -40
```

```output
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collecting ... collected 35 items

packages/tests/unit/test_app_run.py::TestRunSyncEntrypoint::test_run_starts_and_stops_cleanly PASSED [  2%]
packages/tests/unit/test_app_run.py::TestRunSyncEntrypoint::test_run_executes_device_function PASSED [  5%]
packages/tests/unit/test_app_run.py::TestRunSyncEntrypoint::test_run_with_mock_mqtt PASSED [  8%]
packages/tests/unit/test_app_run.py::TestRunSyncEntrypoint::test_run_suppresses_keyboard_interrupt PASSED [ 11%]
packages/tests/unit/test_app_run.py::TestRunSyncEntrypoint::test_run_propagates_system_exit PASSED [ 14%]
packages/tests/unit/test_app_run.py::TestRunSyncEntrypoint::test_run_propagates_runtime_errors PASSED [ 17%]
packages/tests/unit/test_app_run.py::TestRunSyncEntrypoint::test_run_passes_all_kwargs_to_run_async PASSED [ 20%]
packages/tests/unit/test_app_run.py::TestRunSyncEntrypoint::test_run_with_no_args_calls_run_async_with_nones PASSED [ 22%]
packages/tests/unit/test_app_run.py::TestRunSignalHandling::test_sigterm_triggers_shutdown PASSED [ 25%]
packages/tests/unit/test_app_run.py::TestRunSignalHandling::test_sigint_triggers_shutdown PASSED [ 28%]
packages/tests/unit/test_app_run.py::TestCliMethod::test_cli_method_builds_and_invokes_typer PASSED [ 31%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_zero_params_returns_empty_plan PASSED [ 34%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_single_ctx_param PASSED [ 37%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_single_settings_param PASSED [ 40%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_single_logger_param PASSED [ 42%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_single_clock_param PASSED [ 45%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_single_event_param PASSED [ 48%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_multi_params PASSED [ 51%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_unknown_type_accepted_in_plan PASSED [ 54%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_missing_annotation_raises_type_error PASSED [ 57%]
packages/tests/unit/test_injection.py::TestBuildInjectionPlan::test_param_name_is_irrelevant PASSED [ 60%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_empty_plan_returns_empty_kwargs PASSED [ 62%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_resolves_device_context PASSED [ 65%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_resolves_settings PASSED [ 68%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_resolves_logger PASSED [ 71%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_resolves_clock PASSED [ 74%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_resolves_shutdown_event PASSED [ 77%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_resolves_adapter PASSED [ 80%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_unresolvable_type_raises_type_error PASSED [ 82%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_resolves_settings_subclass PASSED [ 85%]
packages/tests/unit/test_injection.py::TestResolveKwargs::test_resolves_multiple_types PASSED [ 88%]
packages/tests/unit/test_injection.py::TestBuildProviders::test_contains_all_known_types PASSED [ 91%]
packages/tests/unit/test_injection.py::TestBuildProviders::test_logger_has_device_scoped_name PASSED [ 94%]
packages/tests/unit/test_injection.py::TestBuildProviders::test_adapter_types_included PASSED [ 97%]
packages/tests/unit/test_injection.py::TestBuildProviders::test_settings_subclass_included PASSED [100%]

============================== 35 passed in 0.12s ==============================
```

```bash
uv run pytest packages/tests/ --tb=short -q 2>&1 | grep -oE '[0-9]+ passed'
```

```output
358 passed
```
