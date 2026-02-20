## Epic Command Handler Redesign Complete: Phase 1 — Core Implementation

Added `@app.command()` decorator enabling FastAPI-style command handler registration.
Handlers are standalone module-level functions with injected parameters, eliminating the
need for `@ctx.on_command` nested inside `@app.device()`.

**Files created/changed:**
- [packages/src/cosalette/_app.py](packages/src/cosalette/_app.py) — `_CommandRegistration`, `command()`, `_run_command()`, updated routing/contexts/availability
- [packages/src/cosalette/_injection.py](packages/src/cosalette/_injection.py) — `mqtt_params` support in `build_injection_plan()`
- [packages/tests/unit/test_command.py](packages/tests/unit/test_command.py) — 22 new tests

**Functions created/changed:**
- `_CommandRegistration` dataclass (new)
- `App.command()` decorator (new)
- `App._run_command()` async method (new)
- `App._check_device_name()` — extended to check commands list
- `App._build_contexts()` — includes command registrations
- `App._publish_device_availability()` — includes command devices
- `App._wire_router()` — routes commands via `_cmd_proxy`
- `build_injection_plan()` — `mqtt_params` parameter for skipping topic/payload

**Tests created/changed:**
- `TestCommandRegistration` — 8 tests (registration, collisions, plan, transparency)
- `TestCommandRouting` — 10 tests (delivery, publish, injection, errors, availability, coexistence)
- `TestCommandInjection` — 4 tests (plan building, backward compat, edge cases)

**Review Status:** APPROVED (2 minor suggestions addressed: added telemetry collision test)

**Git Commit Message:**
```
feat: add @app.command() FastAPI-style handler decorator

- _CommandRegistration dataclass for handler metadata
- command() decorator with name validation and injection plan
- _run_command() dispatches per-message with DI + auto-publish
- build_injection_plan() gains mqtt_params to skip topic/payload
- 22 tests covering registration, routing, injection, availability
```
