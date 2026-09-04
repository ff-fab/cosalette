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
server mode). It is exchanged with the team over the **Dolt remote** — `task
beads:push` / `task beads:pull` against the GitHub `origin` — **not** as a
committed file. `.beads/issues.jsonl` is a local-only, **gitignored and
untracked** export — generated output, never an input; use it for
inspection/diffing only. Keeping it untracked keeps a large regenerated file out
of PR diffs and avoids a second, divergent copy of the issue data.

**Where the ref lives:** on the **remote only**. `git ls-remote origin
'refs/dolt/*'` lists `refs/dolt/data`; a local `git for-each-ref refs/dolt` is
**empty by design** — bd drives that ref out-of-band and the fetch refspec does not
include it, so an empty result is not a broken clone. Verify sync with `bd doctor`
(`Sync Staleness`, `Migration Content Skew`) or `bd dolt remote list`. Do **not**
trust `bd dolt show`: it prints `Remotes: (none)` even when a remote is configured
(a bd 1.1.2 display bug — no need to re-file it).

**Sync rhythm:**

- **Session start / after `git pull`:** run `task beads:pull` **yourself**. A
  `post-merge` hook also pulls, but git runs no post-merge hook at all on `git pull
  --rebase` — this project's documented pull — so treat the hook as a safety net,
  never as the mechanism.
- **Session end / before `git push`:** `task beads:push`. The `pre-push` hook also
  runs `bd dolt push`; it is **non-blocking** on network/auth failure, so run the
  task manually if you see its warning.
- **Before opening a PR:** `task beads:check` warns when an open or `in_progress`
  issue is already referenced by a commit merged to `main` — i.e. work that shipped
  but was never closed. It is advisory in `task pre-pr` and does not fail the gate.

Run `bd prime` for full workflow context.

## Session Completion ("Landing the Plane")

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete
until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** — create beads tasks for anything unfinished
2. **Run quality gates** (if code changed) — `task pre-pr`
3. **Replicate DB state — but do NOT close the issue yet**:

   ```bash
   task beads:push          # bd dolt push — replicate issue data to the remote
   task beads:sync          # optional: refresh the local .beads/issues.jsonl (gitignored)
   ```

   **Closing happens after the PR merges, not here.** You are forbidden from
   merging (see step 5), so at this point the work has not landed: an issue closed
   now is wrong if the PR is reworked or rejected. Leave it `in_progress`, record
   the IDs in the PR body, and close them once the PR is merged:

   ```bash
   bd close <id> && task beads:push
   ```

   `task beads:check` exists to catch the case where that post-merge close is
   forgotten. Reference the IDs in commit messages with a `Closes: cos-abcd` /
   `Refs: cos-abcd` trailer so it has clean data to work from.

   Do **not** `git add .beads/issues.jsonl` — it is gitignored and untracked
   by design. Only commit tracked `.beads/` files (`config.yaml`, `hooks/`,
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
8. **Hand off** — provide context for next session, naming the beads IDs that are
   still `in_progress` and awaiting the post-merge close
9. **After the PR merges** — `bd close <id> && task beads:push`. Nothing automates
   this: no CI job touches beads and the `post-merge` hook only replicates the DB,
   it never changes issue status. Whoever merges owns this step.

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
- NEVER `bd close` an issue at PR-creation time — the work has not landed yet.
  Close it after the merge (step 9); `task beads:check` catches the ones missed

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
