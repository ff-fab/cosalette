## Epic on-configure Complete: Dict-Name Multi-Device

Added dict-name and list-name callable support to `@app.telemetry`, `@app.device`,
and `@app.command` decorators. Users can now pass `name=lambda s: {"a": ConfigA(), "b":
ConfigB()}` to register N devices from a single decorator, with per-device config
automatically injected via DI. Per-device interval resolution is supported when both
`name=` and `interval=` are callables.

**Files created/changed:**

- `packages/src/cosalette/_registration.py`
- `packages/src/cosalette/_app.py`
- `packages/src/cosalette/_wiring.py`
- `packages/src/cosalette/_injection.py`
- `packages/src/cosalette/_telemetry_runner.py`
- `packages/src/cosalette/_command_runner.py`
- `packages/tests/unit/test_dict_name.py`

**Functions created/changed:**

- `_expand_telemetry_names()` — expand callable name specs for telemetry
- `_expand_device_names()` — expand callable name specs for devices
- `_expand_command_names()` — expand callable name specs for commands
- `_check_expanded_duplicates()` — post-expansion duplicate name validation
- `_validate_config_type()` — guard against shadowing framework-injectable types
- `expand_name_specs()` — orchestrator calling all three expansion functions
- `build_providers()` — added `per_device_config` parameter for DI injection
- `add_device()`, `add_telemetry()`, `add_command()` — accept callable name, store in `name_spec`
- `_run_async()` — added `expand_name_specs()` call in lifecycle

**Tests created/changed:**

- `test_dict_name.py` — 17 tests: dict/list expansion (telemetry, device, command),
  per-device config injection, empty dict/list warnings, config type shadowing,
  duplicate names, per-device intervals, group conflicts, invalid return types

**Review Status:** APPROVED (one minor inconsistency fixed during review)

**Git Commit Message:**

```
feat: add dict-name multi-device decorator convenience

- Decorators accept name=callable returning dict/list for N devices
- Dict values injected as per-device config via DI
- Per-device interval resolution with callable interval
- Validation: duplicates, type shadowing, empty results, group conflicts
- 17 new tests covering all scenarios
```
