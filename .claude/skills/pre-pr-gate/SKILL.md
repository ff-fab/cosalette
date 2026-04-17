---
name: pre-pr-gate
description: Pre-PR quality gate. Runs deterministic checks, syncs beads state, pushes, and creates the PR. Use when the user says "prepare a PR", "let's wrap up", "land the plane", "session complete", "pre-pr", "ready to push", or any variation of finishing work and opening a pull request.
# NOTE: This is a Claude Code mirror. The canonical source is .github/skills/pre-pr-gate/SKILL.md
allowed-tools:
  - Bash(task *)
  - Bash(bd *)
  - Bash(git *)
  - Bash(gh *)
  - Read
  - Grep
---

# Pre-PR Quality Gate

Automate the full pre-PR workflow: quality checks → beads sync → push.
Follow these steps strictly in order. Every step must succeed
before moving to the next. Creating pr is NOT part of this skill.

**Cardinal rule: never leave work unpushed.** If something fails partway through, fix it
and continue — do not abandon the workflow.

## Step 1 — Preflight checks

Before running anything, verify the basics:

```bash
git status
git branch --show-current
```

- If on `main` or `master`, stop and tell the user. Do not run quality gates on the
  default branch.
- If there are uncommitted changes, stage and commit them first (ask the user for a
  commit message if the intent is unclear).
- If the working tree is clean and there are no new commits ahead of origin, tell the
  user there's nothing to push.

## Step 2 — Run quality gates

```bash
task pre-pr
```

This runs pre-commit hooks, lint, typecheck, tests, coverage thresholds, and complexity
checks as a single deterministic pipeline.

**If any step fails:** identify the specific failure, fix it, and re-run `task pre-pr`
from scratch. Do not skip failures. Do not move on until the full pipeline passes. If
you cannot fix a failure after two attempts, stop and explain the issue to the user
rather than looping indefinitely.

## Step 3 — Close beads tasks

If `bd` is not available or `.beads/` doesn't exist, skip this step entirely — beads is
not present in every project.

Otherwise, check for completed beads tasks and sync state:

```bash
bd list
```

If there are tasks to close:

```bash
bd close <id>        # for each completed task
git add .beads/ && git commit -m "chore: update beads state"
```

If there are no completed tasks to close, check whether `.beads/` has any uncommitted
changes (the user may have modified state manually). Commit them if so.

## Step 4 — Push

```bash
git pull --rebase origin "$(git branch --show-current)"
git push -u origin "$(git branch --show-current)"
```

If rebase produces conflicts, resolve them and continue the rebase. After pushing,
verify:

```bash
git status
```

Must show the branch is up to date with origin. If push fails for any
other reason (permissions, protected branch), explain the error and stop.
