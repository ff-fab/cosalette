# Epic 1: Adapter Lifecycle Protocol

*2026-02-26T07:50:10Z by Showboat 0.6.0*
<!-- showboat-id: 6d2bfbb3-7180-49ed-99e7-600418b63718 -->

Adapters implementing __aenter__/__aexit__ are now auto-managed by the framework. The AsyncExitStack enters them before the user lifespan hook and exits them after (LIFO), eliminating the need for a lifespan hook in the common case of adapter init/cleanup.

```bash
cd /workspace && task test:file -- packages/tests/unit/test_app.py -k 'TestAdapterLifecycle or TestIsAsyncContextManager' -v 2>&1 | grep -E 'PASSED|FAILED|passed|failed'
```

```output
packages/tests/unit/test_app.py::TestIsAsyncContextManager::test_detects_full_async_cm PASSED [  7%]
packages/tests/unit/test_app.py::TestIsAsyncContextManager::test_rejects_plain_object PASSED [ 14%]
packages/tests/unit/test_app.py::TestIsAsyncContextManager::test_rejects_sync_context_manager PASSED [ 21%]
packages/tests/unit/test_app.py::TestIsAsyncContextManager::test_rejects_partial_aenter_only PASSED [ 28%]
packages/tests/unit/test_app.py::TestIsAsyncContextManager::test_rejects_partial_aexit_only PASSED [ 35%]
packages/tests/unit/test_app.py::TestIsAsyncContextManager::test_detects_contextlib_async_cm PASSED [ 42%]
packages/tests/unit/test_app.py::TestAdapterLifecycle::test_lifecycle_adapter_entered_and_exited[asyncio] PASSED [ 50%]
packages/tests/unit/test_app.py::TestAdapterLifecycle::test_adapter_aenter_before_lifespan_and_aexit_after[asyncio] PASSED [ 57%]
packages/tests/unit/test_app.py::TestAdapterLifecycle::test_mixed_adapters_lifecycle_and_plain[asyncio] PASSED [ 64%]
packages/tests/unit/test_app.py::TestAdapterLifecycle::test_error_during_aenter_cleans_up_already_entered[asyncio] PASSED [ 71%]
packages/tests/unit/test_app.py::TestAdapterLifecycle::test_error_during_aexit_propagates[asyncio] PASSED [ 78%]
packages/tests/unit/test_app.py::TestAdapterLifecycle::test_lifo_exit_ordering[asyncio] PASSED [ 85%]
packages/tests/unit/test_app.py::TestAdapterLifecycle::test_coexistence_with_lifespan_and_lifo[asyncio] PASSED [ 92%]
packages/tests/unit/test_app.py::TestAdapterLifecycle::test_no_lifecycle_adapters_works_normally[asyncio] PASSED [100%]
====================== 14 passed, 127 deselected in 0.12s ======================
```

```bash
cd /workspace && task pre-pr 2>&1 | tail -5
```

```output
  Coverage: 25 file(s) · Lines 95.2% · Branches 90.2% · (threshold 80%) ✓
═══════════════════════════════════════════════════════════════════════════
  Result: [32mALL PASSED ✓[0m
═══════════════════════════════════════════════════════════════════════════

```
