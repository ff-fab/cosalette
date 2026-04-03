## Epic Health Check Docs Complete: Zensical Documentation

Added adapter health check coverage to the two concept pages that were missing it:
`health-reporting.md` (new major section) and `lifecycle.md` (health checks woven
into Phase 3 and Phase 4).

**Files created/changed:**
- `docs/concepts/health-reporting.md`
- `docs/concepts/lifecycle.md`

**Sections created/changed:**
- health-reporting.md: Intro ("three levels"), Mermaid diagram (Adapter Level subgraph),
  summary table (Adapter row), new `## Adapter Health Checks` section (HealthCheckable
  protocol, implementation example, lifecycle sequence diagram, startup/periodic/timeout/
  availability/DI mapping/failure counting/log dedup), See Also (ADR-028 link)
- lifecycle.md: Mermaid sequence diagram (startup health checks + health check task +
  cancel), Phase 3 prose (6 steps), Phase 3 code block, Phase 4 prose (6 steps), Phase 4
  code block, Lifespan Execution Windows table, See Also (ADR-028 link)

**Tests created/changed:**
- N/A (documentation only)

**Review Status:** APPROVED after revision — fixed Phase 4 teardown ordering to match
implementation (device tasks first, then health check task), corrected method names
(`run_loop()`, `run_startup_checks()`), fixed broken anchor link.

**Git Commit Message:**
docs: add adapter health check coverage to concept pages

- Expand health-reporting.md with HealthCheckable protocol, lifecycle
  sequence diagram, startup/periodic checks, failure counting, log
  deduplication, and adapter-to-device DI mapping
- Update lifecycle.md Phase 3 and Phase 4 with health check steps
- Add ADR-028 cross-references to both pages
