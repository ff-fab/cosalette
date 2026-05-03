# cos-4a2.5 / cos-4a2.6 -- Optional Docker & Final Quality Gate

| Field | Value |
|-------|-------|
| Status | Approved and implemented |
| Tasks | cos-4a2.5 (epic), cos-4a2.5.1, cos-4a2.5.2, cos-4a2.6 |
| Scope | CI Docker optionalization analysis, lightweight-job split decision, pre-PR gate |
| PR strategy | One PR after all tasks are complete |

---

## 1. Docker Boundary Audit (cos-4a2.5.1)

### What uses Docker today

| Path | Docker required? | Why |
|------|-----------------|-----|
| `task test:mqtt` | YES -- hard dependency | testcontainers spins up Mosquitto container |
| `task test:integration:full` | YES -- includes mqtt | includes the mqtt suite |
| `integration-tests.yml` workflow | YES | calls devcontainer-run -> Docker-in-Docker |
| `ci.yml`: lint, unit-tests, integration-tests, complexity | NO (logic), YES (runner) | `uv run` commands, but wrapped in `devcontainer-run` action |
| `ci.yml`: codeql | NO | bare ubuntu-latest runner, no devcontainer |
| `ci.yml`: dependency-submission | NO | bare ubuntu-latest runner, SBOM generation |
| `docs.yml` | YES | devcontainer-backed: Docker login + Buildx + devcontainers/ci |
| `devcontainer-build.yml` | YES -- builds the image | purpose-built |
| `release-please`, `rust-wheels.yml` | YES/Maybe | separate concerns |

### What `devcontainer-run` adds to every non-doc code job

```
docker/login-action      -> GHCR authentication
docker/setup-buildx-action -> BuildKit (docker-container driver)
devcontainers/ci         -> pull image from GHCR cache, exec command inside container
```

Typical overhead when the image layer cache is warm: ~60-120s per job.
Typical overhead on a cold cache (`.devcontainer/**` change, scheduled rebuild, or manual dispatch): ~5-8 min per job.

### Devcontainer toolchain required for fast jobs

The devcontainer image bakes in these tools needed for lint/unit/complexity:

| Tool | Version pinned in Dockerfile | Runner availability |
|------|-----------------------------|--------------------|
| Python | 3.14 | `actions/setup-python` 3.14 is available; maturin ABI parity with devcontainer still requires matching Rust version |
| uv | 0.9.22 | `astral-sh/setup-uv` action available, version-pinnable |
| Rust (rustup) | 1.85 (for maturin build) | `dtolnay/rust-toolchain` available |
| task (go-task) | v3.49.1 | installable from release binary |
| dolt, bd | project-specific | not on runners; needed by `beads:sync` but not lint/test |

### Risk inventory

| Risk | Severity | Notes |
|------|----------|-------|
| Python 3.14 toolchain parity on bare runner | Medium | setup-python now lists 3.14; maturin build may fail on some runner images |
| Rust version drift | Low | rust-toolchain action is reliable |
| uv lockfile incompatibility | Low | uv version is pinned in the Dockerfile / devcontainer image |
| devcontainer cache miss | Low-ongoing | triggered by `.devcontainer/**` changes, scheduled rebuilds, or manual dispatch; bounded overhead |
| dolt/bd missing on bare runner | None for tests | only needed in session completion steps, not in CI lint/test |

---

## 2. Options for cos-4a2.5.2

### Option A: Keep devcontainer-run for all code jobs (document boundary only)

**What it does:** No CI changes. Document the current Docker boundary, state that
`task test` and `task ci:test:integration` already exclude the mqtt marker, and record
that full MQTT validation is available on demand via the integration-tests workflow.
Acceptance criteria are met via the documented rationale path.

**Advantages:**

- Zero CI risk -- no workflow changes to review or revert
- Devcontainer remains the single source of truth for Python 3.14 + Rust toolchain
- Warm devcontainer cache makes per-job overhead predictable (~60-120s)
- DRY: no duplicated toolchain setup across workflow files

**Disadvantages:**

- Every lint/unit/complexity job pulls and execs a Docker container even though the
  commands themselves are plain `uv run` invocations
- GHCR authentication and Buildx setup appear as unnecessary noise in job logs
- Cold-cache scenario (after a devcontainer rebuild) causes 5-8 min overhead on
  jobs that don't need Docker at all

---

### Option B: Split fast jobs off devcontainer-run

**What it does:** Replace `devcontainer-run` in lint, unit-tests, and complexity jobs
with a bare-runner setup: `actions/setup-python` (3.14) + `astral-sh/setup-uv` +
`dtolnay/rust-toolchain` + manual `task` install. The integration-tests and
integration-tests.yml workflow keep devcontainer (they need Docker-in-Docker for future
mqtt expansion). MQTT tests stay behind the separate integration-tests workflow.

**Advantages:**

- Eliminates GHCR auth + Buildx + devcontainer pull for 3 of 4 code jobs
- Lint and unit jobs become self-contained: no GHCR dependency, no Docker socket
- Faster cold starts (devcontainer image rebuild no longer blocks green CI for fast
  gates)

**Disadvantages:**

- Bare-runner jobs no longer execute inside the devcontainer, so devcontainer and CI
  behavior are no longer identical by construction; environment-specific failures
  become harder to reproduce locally
- Rust toolchain install adds ~30-60s per job (usually offset by eliminating
  devcontainer pull, but not always)
- Three workflow jobs now duplicate toolchain setup steps: version drift risk between
  the devcontainer Dockerfile and the individual job steps
- maturin requires a native Rust compile on first `uv sync`; if rust-toolchain action
  version drifts from the Dockerfile, the compiled wheel may differ
- go-task is not a standard action -- requires a manual binary install step or a
  community action
- Increases workflow maintenance surface: toolchain upgrades must be synchronized in
  two places (Dockerfile and workflow YAML)

---

### Option C: Devcontainer cache-optimized (lightweight devcontainer layer)

**What it does:** Introduce a lightweight "ci-slim" devcontainer target (multi-stage
Dockerfile) that includes Python 3.14 + uv + Rust + task but excludes Docker CE,
dolt, bd, opencode, and syft. Use this slim target for lint/unit/complexity; keep the
full target for MQTT integration jobs and devcontainer-build.

**Advantages:**

- Smaller image means faster pull on cold cache (the main overhead source)
- No bare-runner toolchain duplication -- devcontainer remains the single source
- Removes Docker CE from the layer that doesn't need it

**Disadvantages:**

- Requires Dockerfile multi-stage refactor: non-trivial given how tools are layered
- devcontainers/ci supports `imageName` override but not stage selection; would need
  a separate `docker build --target slim` step before devcontainers/ci
- Two images to maintain, build, and cache in GHCR
- Complexity-to-benefit ratio is unfavorable compared to Option A or B

---

## 3. Recommendation

**Start with Option A (document boundary), hold Option B as a follow-on.**

Reasoning:

1. **Acceptance criteria are already met.** The fast gate (`task test`, marker `-m
   'not mqtt'`) has always excluded Docker. `ci:test:integration` has never required
   Docker. The boundary is real -- it just is not written down.

2. **Toolchain duplication and parity are the primary blockers for Option B.** The
   devcontainer bakes in Python, Rust, uv, and task as a single tested unit. Moving
   fast jobs to bare runners means duplicating every version pin in both the Dockerfile
   and workflow YAML -- double the drift surface. Maturin builds the native extension
   inside that known ABI; a Rust or Python version skew on the bare runner produces
   wheel-ABI mismatches that are expensive to diagnose. Keeping all code jobs in the
   devcontainer ensures devcontainer and CI behavior stay identical by construction.

3. **Cache overhead is bounded.** The devcontainer build workflow triggers on
   `.devcontainer/**` changes, scheduled rebuilds, and manual dispatch. Routine PRs
   that touch only Python or docs hit a warm cache and see ~60-120s overhead.

4. **Option B is a valid follow-on after a real benchmark.** Revisit the split only
   after a small prototype or CI benchmark demonstrates that bare-runner toolchain
   setup is simpler and faster *without* introducing maturin/ABI parity regressions.
   A successful benchmark -- not a version-number milestone -- is the trigger.

Option C is not recommended: the maintenance overhead of a multi-stage devcontainer
exceeds the benefit for a project with ~60s warm-cache devcontainer overhead.

---

## 4. Implementation Plan (cos-4a2.5.1 -> cos-4a2.5.2 -> cos-4a2.6)

All steps target a single PR. One approval checkpoint: **this document**.

### Step 1 -- Docker boundary documentation (cos-4a2.5.1)

- [x] Add a "CI test layers" section to `CONTRIBUTING.md` that explains:
  - which markers are excluded from `task test` and `task ci:test:integration`
  - how to run MQTT tests locally and in CI
  - why the devcontainer is required (maturin ABI parity with CI toolchain)
  - why Docker-in-Docker is present (testcontainers needs an inner Docker daemon)

### Step 2 -- Optionalization rationale (cos-4a2.5.2)

- [x] Update `CONTRIBUTING.md` (same section) with the rationale for keeping
  devcontainer-run for all code jobs (toolchain duplication risk, maturin ABI parity,
  bounded cache cost)
- [x] Add a "Future: Option B" callout describing when to revisit the split
  (after a prototype/benchmark confirms parity is maintained on bare runners)
- [x] Confirm `integration-tests.yml` retains devcontainer; confirm `codeql` remains
  on bare runner -- no changes needed

### Step 3 -- Final quality gate (cos-4a2.6)

- [x] Run `task pre-pr` locally; confirm all gates pass
- [x] Confirm `task test:mqtt` passes in Docker-capable environment (devcontainer),
  or document the blocker
- [x] Confirm workflow YAML syntax is valid (`yamllint` or GitHub Actions parser)
- [x] Confirm beads state is synchronized (`task beads:sync`)
- [x] Push branch, create PR with this planning doc as context

---

## 5. Acceptance Criteria Checklist

| Criteria | Satisfied by |
|----------|-------------|
| Docker required only where integration tests need it, or documented rationale | Step 1-2 documentation |
| Lightweight CI/container paths avoid unnecessary Docker setup where practical | Deferred: all code jobs still use devcontainer-run (includes Docker login + Buildx); command-level exclusions (`-m 'not mqtt'`) are in place. Full job-level split deferred to Option B follow-on pending benchmark. |
| Non-integration jobs avoid unnecessary Docker setup where practical | Deferred to Option B follow-on, rationale documented |
| Integration/release jobs retain Docker/testcontainers support | No changes; confirmed in Step 2 |
| Local developer instructions cover MQTT integration tests | Step 1 CONTRIBUTING.md update |
| `task pre-pr` passes | Step 3 |
| Full/manual integration command passes or blocker documented | Step 3 |
| Workflow syntax and release dependencies checked | Step 3 |
| Beads state synchronized | Step 3 |
