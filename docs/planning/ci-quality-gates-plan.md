# CI Quality Gates — GitHub Ruleset Migration Plan

## Context

Branch protection is temporarily disabled while migrating from classic branch
protection to GitHub rulesets. This document analyzes all CI pipeline checks and
proposes which should be mandatory, what pipeline changes are needed to support
rulesets, and how to handle conditional execution without the "required check
never appears" deadlock.

## Workflow Inventory

### Workflows that Trigger on PRs

| Workflow | File | Trigger Strategy | PR Check Names |
|----------|------|-----------------|----------------|
| **CI** | `ci.yml` | Always triggers; uses `dorny/paths-filter` for job-level gating | `Detect changes`, `Lint & Type Check`, `Unit Tests & Coverage`, `Code Quality (Complexity & Similarity)` |
| **CodeQL** | `codeql.yml` | `paths-ignore` on workflow trigger | `Analyze Python` |
| **Documentation** | `docs.yml` | `paths` on workflow trigger | `Build Documentation` |
| **Documentation Preview** | `docs-preview.yml` | `paths` on workflow trigger | `Build Documentation`, `Deploy Preview` |

### Workflows that Do NOT Trigger on PRs

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| **DevContainer Build** | `devcontainer-build.yml` | Push to `main` (.devcontainer/\*\*), weekly schedule, manual | Build/cache the dev container image to GHCR |
| **Release Please** | `release-please.yml` | Push to `main` only | Automate changelog, versioning, publishing |
| **Build Rust Wheels** | `rust-wheels.yml` | `workflow_call` / `workflow_dispatch` only | Cross-platform wheel builds for releases |

These last three are irrelevant for PR merge gating.

## The Core Problem: Skipped vs Absent Checks

GitHub rulesets can require status checks. When a required check is:

- **Passed** — merge allowed
- **Skipped** (job ran but was skipped by `if:` condition) — merge allowed (GitHub
  treats this equivalent to passed)
- **Failed** — merge blocked
- **Absent** (workflow never triggered, check name never appears) — **merge
  blocked forever** (deadlock)

### Current Conditional Strategies

**ci.yml** uses `dorny/paths-filter` at the job level:

```yaml
# Workflow always triggers → check names always reported
changes:
  name: Detect changes  # always runs
lint:
  if: needs.changes.outputs.src == 'true'  # skipped for docs-only PRs
```

This is the **correct pattern** — the workflow triggers, jobs appear as "skipped",
and GitHub rulesets see a passed/skipped status.

**codeql.yml** uses `paths-ignore` at the workflow trigger level:

```yaml
on:
  pull_request:
    paths-ignore:
      - 'docs/**'
      - '*.md'
      - 'LICENSE'
```

This causes the **deadlock**: for docs-only PRs, the workflow never triggers,
`Analyze Python` never appears, and a ruleset requiring it would block merge
indefinitely.

**docs.yml** and **docs-preview.yml** use `paths` at the workflow trigger level:

```yaml
on:
  pull_request:
    paths:
      - 'docs/**'
      - 'zensical.toml'
```

Same problem in reverse: for code-only PRs, `Build Documentation` never appears.

## Check-Name × PR-Type Matrix (Current State)

| Check Name | Code-only PR | Docs-only PR | Mixed PR | Config-only PR |
|------------|:------------:|:------------:|:--------:|:--------------:|
| `Detect changes` | ✅ Run | ✅ Run | ✅ Run | ✅ Run |
| `Lint & Type Check` | ✅ Run | ⏭ Skip | ⚠️ See note | ✅ Run |
| `Unit Tests & Coverage` | ✅ Run | ⏭ Skip | ⚠️ See note | ✅ Run |
| `Code Quality (Complexity & Similarity)` | ✅ Run | ⏭ Skip | ⚠️ See note | ✅ Run |
| `Analyze Python` (CodeQL) | ✅ Run | ❌ **Absent** | ✅ Run | ✅ Run |
| `Build Documentation` (docs.yml) | ❌ **Absent** | ✅ Run | ✅ Run | ❌ **Absent** |
| `Build Documentation` (preview) | ❌ **Absent** | ✅ Run | ✅ Run | ❌ **Absent** |
| `Deploy Preview` | ❌ **Absent** | ✅ Run | ✅ Run | ❌ **Absent** |

> **⚠️ `predicate-quantifier: 'every'` issue**: The current `dorny/paths-filter`
> config uses `predicate-quantifier: 'every'`, which means the `src` output is
> `true` only when **every** changed file is a source file. For mixed PRs
> (code + docs), the docs files don't match the `src` filter, so
> `src` evaluates to `false` and **all three CI quality jobs are skipped** despite
> code changes being present.
>
> This is almost certainly a bug. With `some` (the default), `src` would be `true`
> if **any** changed file is a source file — the intended behavior for mixed PRs.

## What Should Be Required?

### Quality Gates (Must Pass Before Merge)

These protect code quality and security. They must run on every PR that changes
source code, and be skippable (not absent) for non-code PRs:

| Check | Why Required |
|-------|-------------|
| **Lint & Type Check** | Catches syntax errors, type violations, style drift |
| **Unit Tests & Coverage** | Verifies correctness and prevents coverage regression |
| **Code Quality (Complexity)** | Enforces cyclomatic/cognitive complexity thresholds |
| **CodeQL (Analyze Python)** | Catches security vulnerabilities (OWASP, CWE) |
| **Docs Build** | Ensures documentation compiles without errors |

### Not Quality Gates (Should NOT Be Required)

| Check | Why Not Required |
|-------|-----------------|
| `Detect changes` | Infrastructure job, not a quality signal |
| `Build Documentation` (docs.yml) | Deployment to surge.sh; build validation handled by ci.yml `Docs Build` |
| `Deploy Preview` | Deployment side-effect, not quality |
| `DevContainer Build` | Infrastructure, push-only |
| `Release Please` | Post-merge automation |
| `Dependency Submission` | Push-only metadata |

## Proposed Changes

### Change 1: Fix `predicate-quantifier` (Bug Fix)

**File**: `ci.yml`

Change `predicate-quantifier: 'every'` to the default `'some'` (or remove the
line entirely).

**What**: `dorny/paths-filter` evaluates to `true` if *any* changed file matches,
not *all*. This ensures mixed PRs (code + docs) correctly trigger CI jobs.

**Why**: With `'every'`, a PR touching both `src/core.py` and `docs/guide.md`
would skip lint/tests/complexity because the docs file doesn't match the `src`
filter. This silently passes PRs with untested code changes.

**Before**:
```yaml
- uses: dorny/paths-filter@v3
  id: filter
  with:
    predicate-quantifier: 'every'
    filters: |
      src:
        - '**'
        - '!docs/**'
        - '!*.md'
        - '!LICENSE'
```

**After**:
```yaml
- uses: dorny/paths-filter@v3
  id: filter
  with:
    filters: |
      src:
        - '**'
        - '!docs/**'
        - '!*.md'
        - '!LICENSE'
```

### Change 2: Move PR-gated CodeQL into ci.yml (Eliminate Absent Check)

**Files**: `ci.yml` (add PR job) + `codeql.yml` (convert to schedule-only)

Move the PR-gated CodeQL analysis into the main CI workflow as a new job with
the same `if: needs.changes.outputs.src == 'true'` condition, while keeping
`codeql.yml` for scheduled-only scans. This means:

- For code PRs: CodeQL runs in `ci.yml` ✅
- For docs-only PRs: CodeQL appears as **skipped** (not absent) ✅
- Weekly schedule: continues in `codeql.yml` as a schedule-only workflow ✅

**New job in ci.yml**:
```yaml
codeql:
  name: CodeQL Security Analysis
  needs: changes
  if: needs.changes.outputs.src == 'true'
  runs-on: ubuntu-latest
  permissions:
    security-events: write
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v6
    - uses: github/codeql-action/init@v4
      with:
        languages: python
        queries: security-extended
    - uses: github/codeql-action/analyze@v4
      with:
        category: '/language:python'
```

**Weekly schedule**: Keep `codeql.yml` but reduce it to schedule-only:
```yaml
name: CodeQL (Scheduled)
on:
  schedule:
    - cron: '0 6 * * 1'
# ... same analyze job, no paths-ignore needed
```

Alternatively, add a `schedule` trigger to `ci.yml` itself with a condition
that makes the CodeQL job always run on schedule.

**Trade-off**: Adding a job to ci.yml increases its scope. However, this is
the standard pattern recommended by GitHub's own documentation on handling
required checks with path-filtered workflows.

### Change 3: Add CI Gate Aggregator Job

**File**: `ci.yml`

Add a single "gate" job that depends on all quality check jobs and always runs.
This becomes the **sole required status check** in the GitHub ruleset.

```yaml
ci-gate:
  name: CI Gate
  if: always()
  needs: [lint, unit-tests, complexity, codeql]
  runs-on: ubuntu-latest
  steps:
    - name: Evaluate results
      run: |
        results=(
          "${{ needs.lint.result }}"
          "${{ needs.unit-tests.result }}"
          "${{ needs.complexity.result }}"
          "${{ needs.codeql.result }}"
        )
        for r in "${results[@]}"; do
          if [[ "$r" == "failure" || "$r" == "cancelled" ]]; then
            echo "::error::Required check failed or was cancelled: $r"
            exit 1
          fi
        done
        echo "All required checks passed or were skipped."
```

**Why**: Instead of requiring 4 separate check names in the ruleset (any of
which might behave differently for skip vs absent), require a single `CI Gate`
check. This is:

- Resilient to workflow refactoring (rename/add/remove jobs without updating
  ruleset)
- Clearer for contributors (one status check, not four)
- The pattern recommended by GitHub for repos with conditional CI

### Change 4: Add ci.yml Permissions for CodeQL

**File**: `ci.yml`

Add `security-events: write` to the workflow-level permissions (needed for
CodeQL to upload results):

```yaml
permissions:
  contents: read
  packages: read
  security-events: write  # NEW: for CodeQL analysis uploads
```

## Resulting Check-Name × PR-Type Matrix (After Changes)

| Check Name | Code-only | Docs-only | Mixed | Config-only |
|------------|:---------:|:---------:|:-----:|:-----------:|
| `Detect changes` | ✅ Run | ✅ Run | ✅ Run | ✅ Run |
| `Lint & Type Check` | ✅ Run | ⏭ Skip | ✅ Run | ✅ Run |
| `Unit Tests & Coverage` | ✅ Run | ⏭ Skip | ✅ Run | ✅ Run |
| `Code Quality (Complexity & Similarity)` | ✅ Run | ⏭ Skip | ✅ Run | ✅ Run |
| `CodeQL Security Analysis` | ✅ Run | ⏭ Skip | ✅ Run | ✅ Run |
| `Docs Build` | ⏭ Skip | ✅ Run | ✅ Run | ⏭ Skip |
| **CI Gate** | ✅ Pass | ✅ Pass | ✅ Pass | ✅ Pass |

**Every check is always present** — either run or skipped. No absent checks.
The `CI Gate` always runs and always produces a status.

## GitHub Ruleset Configuration

After implementing the changes above:

**Required status checks** (ruleset):

| Check Name | Source Workflow |
|------------|---------------|
| `CI Gate` | `CI` (ci.yml) |

That's it — one required check. The CI Gate aggregates all quality sub-checks.

**Optional but visible on PRs** (not in ruleset):

- `Build Documentation` (docs.yml) — appears on docs PRs
- `Build Documentation` / `Deploy Preview` (docs-preview.yml) — appears on docs PRs
- `Detect changes` — visible but not required

## Implementation Order

1. **Fix `predicate-quantifier`** — remove `every`, revert to default `some`
2. **Add CodeQL job to ci.yml** — with `if: needs.changes.outputs.src == 'true'`
3. **Add CI Gate job to ci.yml** — with `if: always()`, depends on all quality
   jobs
4. **Update ci.yml permissions** — add `security-events: write`
5. **Convert codeql.yml to schedule-only** — remove push/PR triggers, keep
   weekly schedule
6. **Test** — open a docs-only PR and a code PR, verify:
   - Docs-only: CI Gate passes (all sub-checks skipped)
   - Code: CI Gate passes (all sub-checks run and pass)
7. **Configure ruleset** — add `CI Gate` as the sole required check

## Open Questions

1. **Should `Build Documentation` be a quality gate?** **Decision: Yes.** Added
   `Docs Build` job to ci.yml, gated on `needs.changes.outputs.docs == 'true'`,
   included in CI Gate. Existing docs.yml and docs-preview.yml remain for
   deployment concerns.

2. **CodeQL schedule trigger**: **Decision: keep as separate workflow.** Schedule
   triggers have no PR context; mixing them into ci.yml adds unnecessary
   conditional complexity. `codeql.yml` is now schedule-only + workflow_dispatch.

3. **Codecov coverage thresholds**: **Decision: make blocking.** Changed
   `fail_ci_if_error: true` in the Codecov upload step. Thresholds are already
   configured in `codecov.yml` (project: 80%, patch: 50%).
