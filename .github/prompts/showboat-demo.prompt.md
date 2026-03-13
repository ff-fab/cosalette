---
agent: agent
description: 'Create a showboat demo that proves the current branch work. Analyzes the diff and builds a reproducible proof-of-work document.'
---

# Showboat Demo

Create a showboat demo for the current branch's work.

## Step 1: Learn Showboat

Run `showboat --help` to understand the available commands and options.

## Step 2: Gather Context

1. Determine the current branch name:

   ```bash
   git branch --show-current
   ```

2. Analyze what changed on this branch vs `main`:

   ```bash
   git log main..HEAD --oneline
   git diff main --stat
   ```

3. Read the changed files to understand the scope of work.

## Step 3: Build the Demo

1. **Initialize** the demo using the branch name:

   ```bash
   showboat init docs/planning/demos/<branch-name>.md "<Title summarizing the work>"
   ```

2. **Add commentary** explaining what was changed and why. Write 2–4 notes covering:
   - The problem or feature being addressed
   - Key design decisions
   - What the proof commands will verify

3. **Add proof commands** using `showboat exec`. Choose commands that demonstrate the
   work is correct:
   - `task pre-pr` or specific test commands for code changes
   - `git diff main...HEAD --stat` to show scope (three-dot merge-base ensures
     reproducibility even if `main` advances)
   - Feature-specific commands (API calls, CLI output, etc.)

4. **If an exec block fails**, remove it with `showboat pop` and retry with a corrected
   command.

## Step 4: Verify

Run `showboat verify` — it must exit 0:

```bash
showboat verify docs/planning/demos/<branch-name>.md
```

If verification fails, use `showboat pop` to remove the failing block, fix the issue,
and re-add it.

## Step 5: Commit

Stage the demo file and commit:

```bash
git add docs/planning/demos/<branch-name>.md
git commit -m "docs: add showboat demo for <branch-name>"
```
