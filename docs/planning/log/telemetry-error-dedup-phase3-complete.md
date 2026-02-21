## Epic Telemetry Error Dedup Complete: Phase 3 — Documentation Updates

Updated all documentation to reflect telemetry error deduplication and health integration.
Code snippets, behavioral descriptions, and the T1 TODO status are now accurate.

**Files created/changed:**

- `docs/guides/telemetry-device.md`
- `docs/concepts/error-handling.md`
- `docs/concepts/device-archetypes.md`
- `docs/concepts/health-reporting.md`
- `docs/TODO/T1-telemetry-error-deduplication.md`

**Sections updated:**

- Telemetry guide: "Under the hood" code + "Error Behaviour" section rewritten
- Error handling concept: `_run_telemetry` snippet + dedup explanation paragraph
- Device archetypes concept: "Telemetry Internals" code + description
- Health reporting concept: heartbeat example + device status info admonition
- T1 TODO: status → Resolved, resolution section added

**Review Status:** APPROVED (after fixing 2 issues: removed unused param, corrected log level)

**Git Commit Message:**

```text
docs: document telemetry error dedup and health integration

- Update telemetry guide error behaviour with dedup semantics
- Update error-handling and device-archetypes code snippets
- Show "error" device status in health heartbeat example
- Mark T1 TODO as resolved with implementation decisions
```
