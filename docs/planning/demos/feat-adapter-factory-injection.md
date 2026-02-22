# Adapter Factory Settings Injection

*2026-02-22T09:19:08Z by Showboat 0.5.0*

Adapter factory callables can now declare a Settings-typed parameter. The framework introspects the factory's signature using the existing injection system (build_injection_plan/resolve_kwargs) and automatically injects the parsed settings instance. Zero-arg factories remain backward compatible. This eliminates the need for duplicate settings parsing in factory closures.

```bash
uv run pytest packages/tests/unit/test_app.py -v -k 'TestAdapterFactoryCallable' 2>&1 | tail -20
```

```output
cachedir: .pytest_cache
rootdir: /workspace
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.3.0, cov-7.0.0, httpx-0.36.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collecting ... collected 95 items / 84 deselected / 11 selected

packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_factory_callable_registration PASSED [  9%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_factory_callable_with_constructor_args PASSED [ 18%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_factory_callable_for_dry_run PASSED [ 27%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_class_impl_factory_dry_run PASSED [ 36%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_factory_impl_class_dry_run PASSED [ 45%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_factory_impl_resolves_in_normal_mode PASSED [ 54%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_string_impl_factory_dry_run PASSED [ 63%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_factory_with_settings_injection PASSED [ 72%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_factory_with_settings_subclass_injection PASSED [ 81%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_zero_arg_factory_still_works PASSED [ 90%]
packages/tests/unit/test_app.py::TestAdapterFactoryCallable::test_factory_with_unknown_type_raises PASSED [100%]

====================== 11 passed, 84 deselected in 0.10s =======================
```
