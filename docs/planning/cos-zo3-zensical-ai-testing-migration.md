# Planning: Zensical Docs Cosalette AI and Testing Migration

**Epic:** cos-zo3 — "Zensical docs cosalette ai and testing migration"

**Goal:** Align documentation, AI content, scaffolding templates, and testing harness with current Router-based architecture while keeping app-level decorators canonical for quickstart examples.

---

## Context

**Current state:**
- README/quickstart use app-level `@app.telemetry()` decorators (pre-Router examples)
- Zensical navigation exists but lacks coherent path from quickstart → concepts → guides → reference → migration
- AI content (`packages/src/cosalette/_ai_content.py` and guidance asset) needs Router/AsyncAPI updates
- Scaffolding templates (`packages/src/cosalette/_mcp/_templates/`) need modernization
- Testing harness (`packages/src/cosalette/testing/_harness.py`) lacks FastAPI TestClient-like helpers for new handler style

**Target state:**
- zensical.toml and docs navigation expose coherent learning path
- README/quickstart showcase typed payloads, typed returns, dependencies, Router pattern, AsyncAPI inspection
- AI help/prime/guidance/templates match documentation
- Testing harness supports Router-based examples
- Migration docs explain breaking changes and mechanical rewrites

**Conservative principle:** Keep app-level decorators **first-class/canonical** for tiny quickstart examples. Introduce Router as production/multi-module composition pattern, not a replacement.

---

## Options

### Option A: Sequential Waterfall

Update in strict sequence: IA → docs → AI content → templates → tests → validation.

**Advantages:**
- Clear dependencies, minimal coordination overhead
- Each phase completes before next begins
- Simpler tracking, easier rollback

**Disadvantages:**
- Longer total duration (6 phases serial)
- AI/template updates blocked until docs finish
- Testing harness improvements delayed

---

### Option B: Parallel Tracks

Run documentation track (IA, content, migration) and implementation track (AI, templates, testing) concurrently.

**Advantages:**
- ~40% faster (two tracks in parallel)
- Templates/AI can iterate on design while docs draft
- Testing harness available sooner for integration tests

**Disadvantages:**
- Requires coordination: docs and AI must converge on examples
- Risk of divergence if communication lapses
- More complex merge/validation at end

---

### Option C: Hybrid Phased

Phase 1 (IA + planning) serial, then parallel docs/implementation tracks, final validation serial.

**Advantages:**
- IA establishes contract before parallel work
- Faster than Option A, safer than full parallel
- Natural sync points (after IA, before validation)

**Disadvantages:**
- Slightly more complex than pure serial
- Still requires coordination during parallel phase

---

## Recommended Approach

**Option C: Hybrid Phased**

**Rationale:**
- IA first (cos-zo3.1) defines structure/navigation/topics — serves as contract for both docs and AI content
- Parallel docs (cos-zo3.2) and implementation (cos-zo3.3, cos-zo3.4, cos-zo3.5) maximizes throughput while minimizing risk
- Final validation (cos-zo3.6) ensures convergence before PR

**Example flow mapping:** IA defines that "Router pattern" lives in guides/routing.md with AsyncAPI examples. Docs track writes guides/routing.md. Implementation track updates `ai prime` and templates to reference Router with same example payload. Validation confirms parity.

---

## Phase Plan

### Phase 1: Information Architecture (cos-zo3.1)

**Objective:** Define docs navigation, topic hierarchy, and coherent learning path in zensical.toml.

**Tasks:**
1. Audit existing docs structure (concepts, guides, reference, migration)
2. Design navigation: quickstart → concepts → guides → reference → migration
3. Update `zensical.toml` navigation sections
4. Create stubs for missing pages (if needed)

**Quality gates:**
- `task docs:build` succeeds
- Navigation renders without broken links
- Coherent path verified manually (quickstart → concepts/router → guides/routing → reference/api)

**Output:** Updated zensical.toml, navigation plan, topic inventory

---

### Phase 2: Parallel Tracks

#### Track A: Documentation Content (cos-zo3.2)

**Objective:** Update README, quickstart, guides, and migration docs to show Router patterns, typed payloads, AsyncAPI.

**Tasks:**
1. **README/quickstart:** Add Router example alongside app-level decorator (keep both, Router as "production pattern")
2. **Guides:** Create or update routing guide with Router, AsyncAPI introspection, typed handlers
3. **Migration docs:** Document breaking changes, mechanical rewrites (e.g., app-level → Router)
4. **Reference:** Ensure API docs cover Router, typed returns, dependencies

**Red/green cycles:**
- Red: `task docs:build` fails on broken links or missing references
- Green: All docs build, examples render correctly, navigation flows

**Quality gates:**
- `task docs:build` succeeds
- `task lint` (Markdown) passes
- Manual review: quickstart → concepts → guides → migration flow is coherent

---

#### Track B: Implementation Updates (cos-zo3.3, cos-zo3.4, cos-zo3.5)

**Objective:** Update AI content, scaffolding templates, and testing harness to match documentation.

**cos-zo3.3: Packaged AI content**
- Update `packages/src/cosalette/_ai_content.py`: `ai help`, `ai prime`, feature map
- Ensure examples match docs (Router pattern, typed payloads)
- Add "Router pattern" topic if missing

**cos-zo3.4: Scaffolding templates/guidance assets**
- Update `packages/src/cosalette/_mcp/_templates/*.j2` with Router examples
- Update `packages/src/cosalette/assets/guidance/cosalette.instructions.md` to reference Router/AsyncAPI

**cos-zo3.5: Modernize testing harness**
- Add FastAPI TestClient-like helpers to `packages/src/cosalette/testing/_harness.py`
- Support Router-based examples in tests
- Update `docs/testing/` examples to use new harness API

**Red/green cycles:**
- Red: `task template:check` fails, tests using old handler style fail
- Green: Templates render valid Python, tests pass with new harness API

**Quality gates:**
- `task template:check` succeeds
- `task test:unit` passes (harness tests)
- `task lint` passes

---

### Phase 3: Validation and Parity (cos-zo3.6)

**Objective:** Ensure docs, AI content, templates, and testing harness are consistent.

**Tasks:**
1. Cross-check examples: docs ↔ AI content ↔ templates
2. Verify AsyncAPI introspection examples match across all artifacts
3. Confirm migration docs explain breaking changes correctly
4. Run full quality gate: `task pre-pr`

**Quality gates:**
- `task docs:build` succeeds
- `task pre-pr` succeeds (lint, typecheck, test, docs)
- Manual parity check: pick 3 examples (Router, typed handler, AsyncAPI), verify identical across docs/AI/templates

**Output:** PR-ready branch

---

## Open Decisions / User Review

### Decision 1: Quickstart Example Strategy

**Question:** Should quickstart show app-level decorator first, then Router as "next step", or vice versa?

**Default recommendation:** App-level first (2-3 lines), then "For production, use Router" section. Matches conservative principle: app-level remains canonical for tiny examples.

**User approval needed?** Yes, confirm before writing quickstart.

---

### Decision 2: Migration Doc Scope

**Question:** Should migration docs cover all breaking changes from v0.x → v1.0, or only Router-related changes?

**Default recommendation:** Focus on Router, typed handlers, AsyncAPI (the architectural changes). Mention other breaking changes in summary list, link to CHANGELOG for full detail.

**User approval needed?** No, proceed with default unless user objects.

---

### Decision 3: Testing Harness API Surface

**Question:** Should testing harness mirror FastAPI TestClient exactly, or provide cosalette-specific abstractions?

**Default recommendation:** Provide `test_client(app)` helper that returns FastAPI TestClient for Router apps, plus convenience methods for MQTT injection. Keep surface small, idiomatic.

**User approval needed?** No, proceed with default.

---

## Success Criteria (Mapped to Children)

| Child | Success Criteria |
|-------|------------------|
| **cos-zo3.1** | zensical.toml navigation defines coherent path; `task docs:build` succeeds; stubs created for missing pages |
| **cos-zo3.2** | README/quickstart show Router pattern, typed payloads, AsyncAPI; migration docs explain breaking changes; guides cover Router composition; `task docs:build` + `task lint` pass |
| **cos-zo3.3** | `ai help`, `ai prime` content matches docs examples; feature map includes Router pattern; packaged content references AsyncAPI |
| **cos-zo3.4** | Templates in `_mcp/_templates/` generate Router-based code; guidance asset references Router; `task template:check` passes |
| **cos-zo3.5** | Testing harness provides TestClient-like helpers; docs/testing examples use new API; harness tests pass |
| **cos-zo3.6** | Parity check (docs ↔ AI ↔ templates) clean; `task pre-pr` passes; no broken links; examples consistent |

---

## Summary

**Approach:** Hybrid phased (IA first, parallel docs/implementation, validation last)

**Key principles:**
- Keep app-level decorators canonical for quickstart
- Introduce Router as production/multi-module pattern
- Ensure docs, AI, templates, tests converge

**Timeline estimate:**
- Phase 1: ~2-3 hours (IA)
- Phase 2: ~6-8 hours (parallel tracks)
- Phase 3: ~1-2 hours (validation)
- **Total:** ~9-13 hours of focused work

**Next step:** User review and approval of this plan, especially Decision 1 (quickstart strategy).
