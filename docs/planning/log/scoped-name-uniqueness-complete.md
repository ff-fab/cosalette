## Epic Complete: Scoped Name Uniqueness

Enabled telemetry and command handlers to share the same device name in the
cosalette framework, supporting the common IoT pattern where a single physical
device publishes telemetry and receives commands on the same MQTT topic
namespace. The framework now enforces name uniqueness per registration type
rather than globally, with a clear collision matrix documented in ADR-019.

**Phases Completed:** 4 of 4

1. ✅ Phase 1: Scope validation per type (COS-bu4)
2. ✅ Phase 2: Deduplicate _build_contexts (COS-xkc)
3. ✅ Phase 3: Health/availability dedup (COS-thb)
4. ✅ Phase 4: ADR-019 + documentation (COS-edi)

**All Files Created/Modified:**

- `packages/src/cosalette/_app.py`
- `packages/tests/unit/test_app_registration.py`
- `packages/tests/unit/test_command.py`
- `packages/tests/integration/test_integration.py`
- `docs/adr/ADR-019-scoped-name-uniqueness.md`
- `docs/adr/index.md`
- `docs/concepts/device-archetypes.md`
- `docs/planning/T1-cosalette-shared-topic-namespace-for-telemetry-and-commands.md`

**Key Functions/Classes Added:**

- `_RegistryType = Literal["device", "telemetry", "command"]` — type alias
- `App._colliding_names(registry_type)` — replaced `_registration_summary()`
- `_check_device_name()` — gained `registry_type` parameter
- `_build_contexts()` — dedup guard (`if reg.name not in contexts`)
- `_publish_device_availability()` — dedup with `seen: set[str]`

**Test Coverage:**

- Total tests written: 15 (11 scoped uniqueness + 2 modified + 1 context dedup + 1 availability dedup)
- All tests passing: ✅ (866 total, 95.2% line coverage, 90.6% branch coverage)

**Recommendations for Next Steps:**

- Consider adding a user-facing guide for the shared name pattern (e.g., a "Telemetry + Command" cookbook entry)
- Monitor for edge cases in real-world adapter migrations
