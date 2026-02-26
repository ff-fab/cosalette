# Epic 2: Settings-Aware Adapter Constructors

*2026-02-26T09:21:31Z by Showboat 0.6.0*
<!-- showboat-id: e5c073f8-7f3c-445f-82ce-b2adfd12b537 -->

All adapter forms (class, string, factory) now go through unified _call_factory() DI pipeline. Adapter classes whose __init__ declares a Settings parameter get it auto-injected. Fail-fast validation at registration time now includes classes too.

```bash
cd /workspace && task test:file -- packages/tests/unit/test_app.py -k 'TestAdapterClassDI' -v 2>&1 | grep 'PASSED\|FAILED'
```

```output
packages/tests/unit/test_app.py::TestAdapterClassDI::test_class_with_settings_injection PASSED [ 16%]
packages/tests/unit/test_app.py::TestAdapterClassDI::test_class_with_settings_subclass_injection PASSED [ 33%]
packages/tests/unit/test_app.py::TestAdapterClassDI::test_class_zero_arg_backward_compat PASSED [ 50%]
packages/tests/unit/test_app.py::TestAdapterClassDI::test_class_no_init_backward_compat PASSED [ 66%]
packages/tests/unit/test_app.py::TestAdapterClassDI::test_class_fail_fast_unknown_type PASSED [ 83%]
packages/tests/unit/test_app.py::TestAdapterClassDI::test_string_import_with_settings_injection PASSED [100%]
```

```bash
cd /workspace && task test:unit 2>&1 | tail -1 | sed 's/ in [0-9.]*s//'
```

```output
============================= 750 passed ==============================
```
