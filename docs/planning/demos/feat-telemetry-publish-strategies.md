# Telemetry Publish Strategies

*2026-02-22T16:23:03Z by Showboat 0.5.0*

Added composable publish strategies (Every, OnChange) to @app.telemetry. Strategies decouple probing frequency from publishing frequency. Compose with | (OR) and & (AND).

```bash
task test:file -- packages/tests/unit/test_strategies.py 2>&1 | tail -3
```

```output
packages/tests/unit/test_strategies.py::TestComposition::test_base_bind_is_noop PASSED [100%]

============================== 47 passed in 0.06s ==============================
```

```bash
task test:file -- packages/tests/unit/test_app.py -k 'TestTelemetryPublishStrategies' 2>&1 | tail -3
```

```output
packages/tests/unit/test_app.py::TestTelemetryPublishStrategies::test_telemetry_strategy_on_published_called PASSED [100%]

======================= 6 passed, 95 deselected in 0.33s =======================
```

```bash
uv run python -c "from cosalette import Every, OnChange, PublishStrategy; print('Every:', Every); print('OnChange:', OnChange); print('Protocol:', PublishStrategy)"
```

```output
Every: <class 'cosalette._strategies.Every'>
OnChange: <class 'cosalette._strategies.OnChange'>
Protocol: <class 'cosalette._strategies.PublishStrategy'>
```
