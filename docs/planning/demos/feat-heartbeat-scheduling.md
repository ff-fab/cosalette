# Periodic heartbeat scheduling

*2026-02-19T17:29:47Z by Showboat 0.5.0*

Implements workspace-ehk: App now publishes periodic JSON heartbeats to {prefix}/status. The heartbeat_interval parameter (default 60s, None to disable) controls the loop. An initial heartbeat is published immediately on connect to overwrite the LWT offline string.

```bash
task test:file -- tests/unit/test_app.py -k heartbeat -v --tb=short 2>&1 | sed 's/ in [0-9.]*s/ in Xs/' | tail -10
```

```output
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.3.0, cov-7.0.0, httpx-0.36.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collecting ... collected 53 items / 50 deselected / 3 selected

tests/unit/test_app.py::TestRunAsync::test_heartbeat_published_on_startup PASSED [ 33%]
tests/unit/test_app.py::TestRunAsync::test_periodic_heartbeat_publishes_multiple_times PASSED [ 66%]
tests/unit/test_app.py::TestRunAsync::test_heartbeat_disabled_with_none_interval PASSED [100%]

======================= 3 passed, 50 deselected in Xs =======================
```

```bash
task test:unit 2>&1 | tail -1 | sed 's/ in [0-9.]*s/ in Xs/'
```

```output
============================= 298 passed in Xs ==============================
```
