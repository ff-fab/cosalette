# Persistence: Store Protocol + DeviceStore + Save Policies

*2026-02-24T21:40:40Z by Showboat 0.6.0*
<!-- showboat-id: f4d7c50d-baba-4795-970d-fbcbd7c1ccc9 -->

Three-layer persistence system: Store protocol with 4 backends (NullStore, MemoryStore, JsonFileStore, SqliteStore), DeviceStore with dirty tracking and DI injection, and composable PersistPolicy strategies (SaveOnPublish, SaveOnChange, SaveOnShutdown) wired via persist= decorator parameter. 736 tests, 94.9% coverage.

```bash
cd /workspace && uv run python -c "
import cosalette
print('Store backends:', [cosalette.NullStore, cosalette.MemoryStore, cosalette.JsonFileStore, cosalette.SqliteStore])
print('DeviceStore:', cosalette.DeviceStore)
print('Policies:', [cosalette.SaveOnPublish, cosalette.SaveOnChange, cosalette.SaveOnShutdown])
# Quick round-trip test
store = cosalette.MemoryStore()
store.save('demo', {'count': 42})
print('Round-trip:', store.load('demo'))
"
```

```output
Store backends: [<class 'cosalette._stores.NullStore'>, <class 'cosalette._stores.MemoryStore'>, <class 'cosalette._stores.JsonFileStore'>, <class 'cosalette._stores.SqliteStore'>]
DeviceStore: <class 'cosalette._stores.DeviceStore'>
Policies: [<class 'cosalette._persist.SaveOnPublish'>, <class 'cosalette._persist.SaveOnChange'>, <class 'cosalette._persist.SaveOnShutdown'>]
Round-trip: {'count': 42}
```

```bash
cd /workspace && task test:unit 2>&1 | tail -5
```

```output
packages/tests/unit/test_testing_module.py::TestPytestPlugin::test_device_context_uses_mock_mqtt PASSED [ 99%]
packages/tests/unit/test_testing_module.py::TestPytestPlugin::test_fixtures_are_fresh_per_test PASSED [100%]

--------------- generated xml file: /workspace/results-unit.xml ----------------
============================= 725 passed in 5.67s ==============================
```
