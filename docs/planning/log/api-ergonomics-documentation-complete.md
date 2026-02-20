## Epic API Ergonomics Complete: Documentation Updates

Updated all documentation across 15 files to reflect the new API ergonomics:
lifespan context manager (replacing `@app.on_startup`/`@app.on_shutdown`),
`app.run()` sync entrypoint, and signature-based handler injection. Created a
comprehensive new Lifespan guide (366 lines) replacing the removed Lifecycle Hooks
guide.

**Files created/changed:**

- `docs/guides/lifespan.md` (NEW — 366 lines, replaces lifecycle-hooks.md)
- `docs/guides/lifecycle-hooks.md` (DELETED)
- `docs/concepts/architecture.md` (registration table, injection tabs, code examples)
- `docs/concepts/lifecycle.md` (mermaid diagram, lifespan sections, code snippets)
- `docs/concepts/device-archetypes.md` (handler signature claims)
- `docs/guides/full-app.md` (Section 8 rewrite, assembly, summary table)
- `docs/guides/adapters.md` (lifespan pattern for adapter init)
- `docs/guides/telemetry-device.md` (signature claims, injection note)
- `docs/guides/command-device.md` (injection contextual note)
- `docs/getting-started/quickstart.md` (annotations updated)
- `docs/getting-started/index.md` (hero example zero-arg)
- `docs/index.md` (quick example zero-arg)
- `README.md` (quick example zero-arg)
- `docs/guides/index.md` (nav card updated)
- `zensical.toml` (nav entry updated)
- `docs/adr/ADR-001-framework-architecture-style.md` (addendum + inline note)

**Review Status:** APPROVED (one formatting fix applied for code fence indent)

**Git Commit Message:**

```
docs: update all documentation for API ergonomics

- Replace @app.on_startup/@app.on_shutdown with lifespan pattern
- New comprehensive Lifespan guide (replaces Lifecycle Hooks)
- Update handler signatures to reflect optional DeviceContext
- Show zero-arg telemetry handlers in hero examples
- Add ADR-001 addendum documenting lifespan + injection changes
- Update nav, cross-references, and registration tables
```
