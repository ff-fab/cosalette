## Epic Complete: Framework Persistence Stores

Three-layer persistence system for cosalette: Store protocol with 4 pluggable backends, DeviceStore with dirty tracking and DI injection, and composable PersistPolicy strategies wired via a `persist=` decorator parameter that mirrors the existing `publish=` pattern.

**Phases Completed:** 4 of 4
1. ✅ Phase 1: Store protocol + 4 backends (NullStore, MemoryStore, JsonFileStore, SqliteStore)
2. ✅ Phase 2: DeviceStore wrapper + DI integration + framework loop wiring
3. ✅ Phase 3: Save policies (SaveOnPublish, SaveOnChange, SaveOnShutdown) + composition
4. ✅ Phase 4: Documentation (ADR-015, concept doc, guide updates, API reference)

**All Files Created/Modified:**
- `packages/src/cosalette/_stores.py` — Store protocol, 4 backends, DeviceStore
- `packages/src/cosalette/_persist.py` — PersistPolicy protocol, 3 policies, composites
- `packages/src/cosalette/stores.py` — Public re-exports
- `packages/src/cosalette/persist.py` — Public re-exports
- `packages/src/cosalette/__init__.py` — Added 10 new exports
- `packages/src/cosalette/_app.py` — store=, persist= params, DeviceStore lifecycle
- `packages/src/cosalette/_injection.py` — DeviceStore in KNOWN_INJECTABLE_TYPES
- `packages/src/cosalette/testing/_harness.py` — store= param on AppHarness.create()
- `docs/adr/ADR-015-persistence.md` — Architecture decision record
- `docs/concepts/persistence.md` — Concept documentation
- `docs/guides/telemetry-device.md` — Persistence section added
- `docs/reference/api.md` — Persistence + Stores API reference
- `docs/reference/testing.md` — MemoryStore testing section
- `docs/adr/index.md` — ADR-015 entry
- `docs/concepts/index.md` — Persistence card

**Key Functions/Classes Added:**
- `Store` — @runtime_checkable protocol (load/save)
- `NullStore` — No-op backend
- `MemoryStore` — In-memory dict backend (testing)
- `JsonFileStore` — Atomic-write single-file JSON backend
- `SqliteStore` — WAL-mode SQLite backend
- `DeviceStore` — Per-device MutableMapping with dirty tracking
- `PersistPolicy` — @runtime_checkable protocol (should_save)
- `SaveOnPublish` — Save after MQTT publish
- `SaveOnChange` — Save when store is dirty
- `SaveOnShutdown` — Save only on shutdown
- `AnySavePolicy` / `AllSavePolicy` — Boolean composites via | and &

**Test Coverage:**
- Total tests written: 111 (38 stores + 34 device store + 28 persist + 11 integration)
- All tests passing: ✅ (736 total suite)
- Coverage: 94.9% lines, 89.8% branches

**Recommendations for Next Steps:**
- SqliteStore.close() is never called by framework — add lifecycle hook (COS-rst deferred)
- Consider `persist=` support for `@app.device` when use cases emerge
- gas2mqtt migration to framework persistence deferred (COS-rst)
