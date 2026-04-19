---
description: 'Development workflow: Git flow, issue tracking, quality gates, session completion'
applyTo: '**'
---

# Workflow

## Git Workflow (GitHub Flow)

**CRITICAL: Never push directly to main. All changes go through PRs.**

1. **Create feature branch from main**

   ```bash
   git checkout main && git pull
   git checkout -b feature/description  # or fix/, docs/, chore/, etc.
   ```

2. **Make commits with clear messages** (skill `caveman-commit`)

3. **Ensure quality gates pass** (skill `pre-pr-gate`)

4. **Push and create pull request** (skill `create-pr`)

5. **Wait for CI**

   ```bash
   task ci:wait -- <pr-number>   # polls until all checks complete
   ```

   **NEVER merge a PR unless the user explicitly requests it.**

**Key principle:** `main` is always deployable.

## Releases

This project uses **Release Please**, releases are fully automated.

Agents do NOT manually create tags or releases — the bot handles it.

## Issue Tracking (Beads)

This project uses **bd (beads)** — a git-backed graph issue tracker for AI agents.
Issues are stored as JSONL in `.beads/` and committed to git.

Run `bd prime` for full workflow context.

## Session Completion ("Landing the Plane")

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete
until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** — create beads tasks for anything unfinished
2. **Run quality gates** (if code changed) — `task pre-pr`
3. **Close beads tasks and commit state**:

   ```bash
   bd close <id>
   task beads:sync
   git add .beads/ && git commit -m "chore: update beads state"
   ```

4. **PUSH TO REMOTE** — this is MANDATORY:

   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```

5. **Create PR** (if new branch): `task pr:create -- --title "..." --body "..."`
6. **Clean up** — clear stashes, prune remote branches
7. **Verify** — all changes committed AND pushed
8. **Hand off** — provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push
- If push fails, resolve and retry until it succeeds
- Beads state MUST be committed before pushing — the pre-push hook will reject pushes
  with uncommitted `.beads/` changes
- NEVER merge a PR — only the user decides when to merge

## Test Notes

- Shared fixtures (in `tests/fixtures/`) should be used to avoid duplication
- Always ensure tests, fixtures, documentation, and features stay in sync

## Feature Planning Checklist

When planning a new framework feature, include companion tasks for:

- **`ai help` topic** — create or update the relevant topic in `_ai_content.py`
- **`ai prime` what's-new entry** — add to the version feature map in `_ai_content.py`
- **Asset template update** — update `packages/src/cosalette/assets/guidance/cosalette.instructions.md`
- **Documentation** — add to `docs/` and zensical.toml if needed, consider restructuring
  existing docs for cohesion

## Template Maintenance

Scaffolding templates live in `packages/src/cosalette/_mcp/_templates/`. When changing
registration APIs (`@app.telemetry`, `@app.device`, `@app.command`, `app.adapter()`),
update the corresponding `.j2` templates so generated code stays idiomatic.

Run `task template:check` to verify templates still render valid, lint-clean Python.
