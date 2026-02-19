## Epic API Ergonomics Complete: Phase 1 — Public app.run() Entrypoint

Added a public synchronous `app.run()` method that wraps `asyncio.run()` with
`KeyboardInterrupt` suppression. Extracted the Typer CLI path to `app.cli()`.
The debug app now starts with just `app.run(mqtt=MockMqttClient())` — zero
asyncio/signal boilerplate.

**Files created/changed:**

- `packages/src/cosalette/_app.py` — replaced `run()` with sync entrypoint +
  `cli()` for Typer path
- `packages/cosalette_debug_app.py` — simplified from manual asyncio.run to
  `app.run(mqtt=...)`
- `packages/tests/unit/test_app_run.py` — 11 new tests

**Functions created/changed:**

- `App.run()` — sync entrypoint: `asyncio.run(_run_async(...))` with
  `KeyboardInterrupt` suppression
- `App.cli()` — extracted Typer CLI builder (was old `run()`)
- `App._run_async()` — parameter ordering aligned with `run()`

**Tests created/changed:**

- `TestRunSyncEntrypoint` — 8 tests: clean start/stop, mock MQTT, kwargs
  forwarding, KeyboardInterrupt suppression, SystemExit propagation, RuntimeError
  propagation, signal handling (SIGTERM/SIGINT)
- `TestCliMethod` — 1 test: Typer CLI invocation
- `TestRunDeviceExecution` — 2 tests: device function execution, telemetry
  function execution through `run()`

**Review Status:** APPROVED

**Git Commit Message:**

```text
feat: add public app.run() sync entrypoint

- Add App.run() wrapping asyncio.run() with KeyboardInterrupt suppression
- Extract Typer CLI path to App.cli()
- Align _run_async() parameter ordering with run()
- Simplify debug app to use app.run(mqtt=MockMqttClient())
- Add 11 tests covering run() and cli() behavior
```
