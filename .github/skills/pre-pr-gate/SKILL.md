---
name: pre-pr-gate
description:
  Pre-PR quality gate workflow. Use when preparing to create a pull request, finishing a task, completing a work session, or when the user says "let's wrap up", "land the plane", "prepare a PR", or "session complete". Runs deterministic quality checks, syncs beads state, pushes, and creates the PR.
---

# Pre-PR Quality Gate

This skill automates the full pre-PR workflow. Follow these steps in order.

## Step 1: Run Quality Gates

Run the deterministic quality gate task. This runs pre-commit checks, lint, typecheck,
all tests, and coverage thresholds:

```bash
task pre-pr
```

If any step fails, fix the issue and re-run. Do NOT skip failures.

## Step 2: Showboat Demo (Optional)

Only create a showboat demo if:

- The user explicitly requests one, OR
- This is a significant feature or architectural change

When creating a demo:

```bash
showboat init docs/demos/<branch-name>.md "<Title of Work>"
showboat note docs/demos/<branch-name>.md "What was changed and why."
showboat exec docs/demos/<branch-name>.md bash "<command that proves it works>"
showboat verify docs/demos/<branch-name>.md
```

Skip for: documentation-only changes, beads-only changes, minor fixes.

## Step 3: Close Beads Tasks

Close all completed beads tasks and sync state:

```bash
bd close <id>
bd sync
git add .beads/ && git commit -m "chore: sync beads state"
```

The pre-push hook will reject pushes with uncommitted `.beads/` changes.

## Step 4: Push to Remote

```bash
git pull --rebase
git push -u origin <branch-name>
git status  # Must show "up to date with origin"
```

If push fails, resolve conflicts and retry. NEVER stop before pushing.

## Step 5: Create Pull Request

```bash
gh pr create
```

## Step 6: Report Summary

After completing all steps, provide a brief summary:

- Quality gate results (pass/fail per stage)
- Beads tasks closed
- PR URL
- Any remaining work filed as new beads tasks

## Error Handling

- If `task pre-pr` fails: identify the failing step, fix it, re-run
- If push fails: `git pull --rebase`, resolve conflicts, push again
- If PR creation fails: check branch is pushed, retry with `gh pr create --fill`
- NEVER leave work unpushed — this is the most critical rule
