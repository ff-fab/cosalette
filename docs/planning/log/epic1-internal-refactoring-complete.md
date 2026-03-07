## Epic Complete: Internal Refactoring (P5.x)

Decomposed the monolithic `_app.py` and `_registration.py` modules by extracting
cohesive concerns into dedicated modules, replacing type lies with runtime-validated
narrowing, and removing dead delegate methods. Net reduction of ~200 lines from `App`
class surface area while maintaining full backward compatibility and 866+ passing tests.

**Phases Completed:** 5 of 5

1. ✅ P5.1: Extract `MqttClient` from `_mqtt.py` into `_mqtt_client.py`
2. ✅ P5.3: Relocate `_AdapterEntry` and lifecycle helpers to `_adapter_lifecycle.py`
3. ✅ P5.5: Evaluate unifying `_call_factory`/`_call_init` → closed (different
   semantics: lazy vs eager plan, different validation, different return-type handling;
   shared machinery already factored)
4. ✅ P5.4: Replace `cast(float, reg.interval)` with `_resolved_interval()` helper
5. ✅ P5.2: Remove 4 dead delegate methods from `App` class

**All Files Created/Modified:**

- `packages/src/cosalette/_mqtt_client.py` (new — extracted MqttClient adapter)
- `packages/src/cosalette/_mqtt.py` (reduced: ports/protocols only, re-exports
  MqttClient)
- `packages/src/cosalette/_adapter_lifecycle.py` (expanded: owns adapter lifecycle
  symbols)
- `packages/src/cosalette/_registration.py` (reduced: device registrations only)
- `packages/src/cosalette/_telemetry_runner.py` (new `_resolved_interval()` helper)
- `packages/src/cosalette/_app.py` (4 dead delegates removed, 1 call inlined)
- `packages/tests/unit/test_mqtt.py` (patch target updates)
- `packages/tests/unit/test_app_adapters.py` (import + docstring updates)
- `packages/tests/unit/test_app_wiring.py` (test updates for removed delegates)
- `packages/tests/unit/test_app_run.py` (docstring update)
- `packages/tests/unit/test_app_registration.py` (docstring update)
- `packages/cosalette_debug_app.py` (comment update)
- `docs/adr/ADR-020-deferred-interval-resolution.md` (editorial note)
- `docs/concepts/lifecycle.md` (code example update)

**Key Functions/Classes Added:**

- `_resolved_interval()` — runtime-validated interval narrowing (replaces `cast()`)

**Test Coverage:**

- Total tests: 866+ (all passing)
- All quality gates: lint ✅, typecheck ✅, tests ✅

**Recommendations for Next Steps:**

- Consider adding a unit test for `_resolved_interval()`'s error path (unresolved
  callable)
- The 4 remaining test-facing delegates on `App` could be removed in a future pass if
  tests are refactored to test module functions directly
