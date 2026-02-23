# init= callback for telemetry, device, and command decorators

*2026-02-23T19:52:43Z by Showboat 0.6.0*
<!-- showboat-id: 746e1f28-3d9a-4fd1-bd9c-9916871648e7 -->

Added init= parameter to @app.telemetry, @app.device, and @app.command decorators. Factory callback invoked once before handler loop, result injected into handler by type via DI. Includes fail-fast validation (bad signatures, async callables caught at decoration time), type collision guard, command init caching, and adapter factory fail-fast validation.

```bash
cd /workspace && task test:unit 2>&1 | grep -E '(passed|failed)' | sed 's/ in [0-9.]*s//'
```

```output
packages/tests/unit/test_errors.py::TestBuildErrorPayload::test_device_parameter_passed_through PASSED [ 37%]
packages/tests/unit/test_errors.py::TestBuildErrorPayload::test_details_passed_through_to_payload PASSED [ 37%]
============================= 610 passed ==============================
```

```bash
cd /workspace && task typecheck 2>&1 | grep -c 'no issues'
```

```output
1
```
