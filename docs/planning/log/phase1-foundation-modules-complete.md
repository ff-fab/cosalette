## Epic Phase 1 Complete: Foundation Modules — Settings, Clock, Logging

Created the three foundational modules that every cosalette application depends on:
`_clock.py` (monotonic clock port + system adapter), `_settings.py` (pydantic-settings
configuration with nested MQTT and logging sub-models), and `_logging.py` (NDJSON
structured formatter + `configure_logging()` function with file rotation support).

**Files created/changed:**

- `packages/src/cosalette/_clock.py`
- `packages/src/cosalette/_settings.py`
- `packages/src/cosalette/_logging.py`
- `packages/src/cosalette/__init__.py`
- `packages/tests/unit/test_clock.py`
- `packages/tests/unit/test_settings.py`
- `packages/tests/unit/test_logging.py`
- `packages/tests/fixtures/config.py`
- `packages/pyproject.toml`
- `Taskfile.yml`

**Files deleted:**

- `packages/src/cosalette/config.py` (placeholder replaced by `_settings.py`)

**Functions created/changed:**

- `ClockPort` — `@runtime_checkable` Protocol with `now() -> float`
- `SystemClock` — production adapter wrapping `time.monotonic()`
- `MqttSettings` — pydantic `BaseModel` with MQTT broker fields + `SecretStr`
- `LoggingSettings` — pydantic `BaseModel` with level, format, file, backup_count
- `Settings` — pydantic `BaseSettings` root with nested delimiter `__` and `.env`
- `JsonFormatter` — NDJSON log formatter with service/version correlation
- `configure_logging()` — configures root logger from `LoggingSettings`

**Tests created/changed:**

- `test_clock.py` — 5 tests (protocol conformance, monotonic ordering)
- `test_settings.py` — 31 tests (defaults, validation, SecretStr, env override)
- `test_logging.py` — 13 tests (JSON shape, UTC timestamps, file handler)
- `test_package.py` — 2 existing smoke tests (unchanged)
- Removed stale `_reset_settings_cache` fixture from `fixtures/config.py`

**Review Status:** APPROVED (all minor findings addressed)

**Git Commit Message:**

```
feat: add foundation modules — settings, clock, logging

- Create _clock.py with ClockPort protocol and SystemClock adapter
- Create _settings.py with MqttSettings, LoggingSettings, Settings
- Create _logging.py with JsonFormatter and configure_logging()
- Wire up public API re-exports in __init__.py
- Add aiomqtt and typer to project dependencies
- Delete old config.py placeholder
- 54 tests, 94.7% line coverage, 100% branch coverage
```
