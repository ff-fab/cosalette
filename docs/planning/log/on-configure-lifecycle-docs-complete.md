## Epic on-configure-lifecycle Complete: Documentation

Added comprehensive documentation for `@app.on_configure` lifecycle hook and
dict-name multi-device registration. Created a new guide with progressive examples
(problem → dict-name → on_configure → combined pattern), and updated 7 existing
pages with accurate lifecycle diagrams, cross-references, and safety notes.

**Files created/changed:**
- `docs/guides/multi-device.md` (new — ~350 lines)
- `docs/concepts/lifecycle.md` (Mermaid diagram + Phase 1 text)
- `docs/concepts/architecture.md` (Registration API table + Four-Phase table)
- `docs/guides/telemetry-device.md` (cross-reference tip)
- `docs/guides/command-device.md` (cross-reference tip)
- `docs/guides/lifespan.md` (ASCII lifecycle diagram)
- `docs/guides/configuration.md` (`--help` safety warning)
- `docs/guides/index.md` (grid card)
- `zensical.toml` (nav entry)

**Functions created/changed:**
- N/A (documentation only)

**Tests created/changed:**
- N/A (documentation only)

**Review Status:** APPROVED (after fixing lifecycle ordering in lifespan.md and multi-device.md, removing unused imports, fixing config type consistency)

**Git Commit Message:**
```
docs: add multi-device registration guide and update lifecycle docs

- Create docs/guides/multi-device.md covering dict-name decorators,
  @app.on_configure, per-device intervals, and combined patterns
- Update lifecycle diagram and Phase 1 text with configure hooks,
  name expansion, and interval resolution steps
- Add @app.on_configure row and callable name= notes to architecture
  Registration API table
- Add cross-references in telemetry, command, lifespan, and config guides
- Add --help safety warning to configuration guide
- Update nav and guide index grid
```
