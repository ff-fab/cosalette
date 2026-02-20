## Epic Command Handler Complete: Phase 3 Documentation

All documentation updated to lead with `@app.command()` as the primary command device
pattern, with `@app.device()` + `@ctx.on_command` preserved as the advanced escape hatch.

**Files created/changed:**

- docs/guides/command-device.md (major rewrite — leads with `@app.command`)
- docs/concepts/device-archetypes.md (two → three archetypes)
- docs/guides/full-app.md (valve sections rewritten)
- docs/concepts/architecture.md (FastAPI analogy + registration table)
- docs/concepts/lifecycle.md (Phase 2/3 dispatch notes)
- docs/adr/ADR-010-device-archetypes.md (amendment for `@app.command`)

**Functions created/changed:**

- N/A (documentation only)

**Tests created/changed:**

- N/A (documentation only)

**Review Status:** APPROVED (one major code-fence fix applied, minor ADR date fix)

**Git Commit Message:**

```
docs: document @app.command() as primary command pattern

- Rewrite command-device guide to lead with @app.command()
- Update device archetypes from two to three archetypes
- Update full-app guide valve examples to use @app.command
- Add @app.command to architecture registration table
- Note @app.command dispatch in lifecycle Phase 2/3
- Amend ADR-010 with @app.command third archetype
```
