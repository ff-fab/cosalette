---
name: pre-pr-gate
description: Pre-PR quality gate. Runs deterministic checks, syncs beads state, pushes, and creates the PR. Use when the user says "prepare a PR", "let's wrap up", "land the plane", "session complete", "pre-pr", "ready to push", or any variation of finishing work and opening a pull request.
---

# Pre-PR Quality Gate

Automate full pre-PR workflow: quality checks → beads sync → push. Follow these steps
strictly in order. Every step must succeed before moving to the next.

**Cardinal rule: never leave work unpushed.** If something fails partway
through, fix it and continue — do not abandon the workflow.

## Step 1 — Preflight checks

Before running anything, verify basics:

```bash
git status
git branch --show-current
```

- If on `main` or `master`, stop and tell user. Do not run quality gates on default
  branch.
- If there are uncommitted changes, stage and commit them first (ask user for commit
  message if intent is unclear).
- If working tree is clean and no new commits ahead of origin, tell user there's nothing
  to push.

## Step 2 — Run quality gates

```bash
task pre-pr
```

This runs pre-commit hooks, lint, typecheck, unit + integration tests (excluding
mqtt), coverage thresholds, and complexity checks as a single deterministic pipeline.

**If any step fails:** identify the specific failure, fix it, and re-run
`task pre-pr` from scratch. Do not skip failures. Do not move on until the
full pipeline passes. If you cannot fix a failure after two attempts, stop
and explain the issue to the user rather than looping indefinitely.

## Step 3 — Replicate beads state (do NOT close yet)

Check which beads tasks this work covers, and replicate the DB:

```bash
bd list
task beads:push      # bd dolt push — replicate issue data to the Dolt remote
task beads:check     # warns about issues that shipped to main but stayed open
```

**Do not `bd close` here.** This skill runs *before* the PR exists, and you are
forbidden from merging it — so the work has not landed and the issue is not done.
Closing now is wrong if the PR is reworked or rejected. Leave the tasks
`in_progress`, list their IDs in the PR body, and close them **after the PR
merges**:

```bash
bd close <id> && task beads:push
```

Issue data is replicated over the Dolt remote — the ref lives on the remote only,
so a local `git for-each-ref refs/dolt` is empty by design — and is **not**
committed. Never `git add .beads/issues.jsonl`; it is a local-only export,
gitignored and untracked (2026-08 security audit finding F-SC1 / CWE-359; the
register is private, see `SECURITY.md`). `task beads:sync` merely refreshes that
local export and is optional.

Whether or not there were tasks to close, check whether the **tracked** `.beads/`
files (`config.yaml`, `metadata.json`, `hooks/`, `README.md`, `.gitignore`) have
uncommitted changes and commit those if so — the `pre-push` hook rejects a push
that leaves them dirty.

## Step 4 — Push

```bash
git pull --rebase origin "$(git branch --show-current)"
git push -u origin "$(git branch --show-current)"
```

If rebase produces conflicts, resolve them and continue rebase. After pushing, verify:

```bash
git status
```

Must show branch is up to date with origin. If push fails for any other reason
(permissions, protected branch), explain error and stop.

## Step 5 — Report

Provide brief summary:

- Quality gate result (pass, or which step failed and how it was fixed)
- Beads tasks closed (list IDs and titles, or "none")
- Any remaining work that should be filed as new tasks
- If push succeeded, confirm PR can now be created with `create-pr` skill.
