# Direct Function Registration

*2026-02-26T17:03:49Z by Showboat 0.6.0*
<!-- showboat-id: c859cd96-2da2-4b57-b2cf-4a5ab3dfcc5b -->

Added add_device(), add_telemetry(), and add_command() imperative registration methods to App. Refactored existing decorators to delegate to these methods for named registrations (DRY). Root devices remain decorator-only. 21 new tests cover registration, validation, collisions, decorator equivalence, and runtime execution.

```bash
cd /workspace && uv run pytest packages/tests/unit/test_app.py -k 'TestDirectFunctionRegistration' --tb=no -q 2>&1 | grep -oP '\d+ passed'
```

```output
21 passed
```

```bash
cd /workspace && uv run pytest packages/tests/ --tb=no -q 2>&1 | grep -oP '\d+ passed'
```

```output
792 passed
```
