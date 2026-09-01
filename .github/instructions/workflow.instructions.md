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

This project uses **bd (beads)** — a graph issue tracker for AI agents.

**Storage & exchange:** issue data lives in a **Dolt database** (`.beads/dolt/`,
server mode). It is exchanged with the team over the **Dolt remote** — `bd dolt
push` / `bd dolt pull` against the GitHub `origin` (refs under `refs/dolt/*`) —
**not** as a committed file. `.beads/issues.jsonl` is a local-only, **gitignored
and untracked** export (F-SC1 / CWE-359: it embedded a maintainer's personal
email); use it for inspection/diffing only.

**Sync rhythm:**

- **Session start / after `git pull`:** `bd dolt pull` (or `task beads:pull`).
  The `post-merge` hook also runs `bd dolt pull` automatically when a merge
  touched `.beads/` — but it does **not** fire on `git pull --rebase`, so run
  the task manually in that workflow.
- **Session end / before `git push`:** `bd dolt push` (or `task beads:push`).
  The `pre-push` hook runs `bd dolt push` automatically; it is **non-blocking**
  on network/auth failure, so run the task manually if you see its warning.

Run `bd prime` for full workflow context.

## Session Completion ("Landing the Plane")

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete
until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** — create beads tasks for anything unfinished
2. **Run quality gates** (if code changed) — `task pre-pr`
3. **Close beads tasks and replicate DB state**:

   ```bash
   bd close <id>
   task beads:push          # bd dolt push — replicate issue data to the remote
   task beads:sync          # optional: refresh the local .beads/issues.jsonl (gitignored)
   ```

   Do **not** `git add .beads/issues.jsonl` — it is gitignored and untracked
   (F-SC1). Only commit tracked `.beads/` files (`config.yaml`, `hooks/`,
   `metadata.json`) when you actually changed them; the `pre-push` hook rejects
   a push with those uncommitted.

4. **PUSH TO REMOTE** — this is MANDATORY:

   ```bash
   git pull --rebase       # no post-merge hook on rebase — run `task beads:pull`
   git push                # pre-push hook also runs `bd dolt push` (non-blocking)
   git status              # MUST show "up to date with origin"
   ```

   If the `pre-push` hook prints a `bd dolt push failed` warning, run
   `task beads:push` manually once the remote is reachable.

5. **Create PR** (if new branch): `task pr:create -- --title "..." --body "..."`
6. **Clean up** — clear stashes, prune remote branches
7. **Verify** — all changes committed AND pushed
8. **Hand off** — provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push
- If push fails, resolve and retry until it succeeds
- Beads issue data is replicated with `bd dolt push` (auto-run by the pre-push
  hook, non-blocking), NOT committed to git. Tracked `.beads/` files
  (`config.yaml`, `hooks/`, `metadata.json`) MUST be committed before pushing —
  the pre-push hook rejects pushes with those uncommitted.
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
